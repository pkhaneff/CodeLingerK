"""
MemoryService - Repository-scoped rules, patterns, and accepted decision management.

The memory layer allows the system to:
1. Inject project-specific conventions into the LLM prompt (RepoRules)
2. Filter out files that should never be reviewed (IgnoredPatterns)
3. Suppress re-reporting of acknowledged findings (AcceptedDecisions)

Usage in the review pipeline:
    memory = MemoryService(db, repository_id=repo_id)

    # Filter files before sending to LLM
    filtered_files = await memory.filter_files(changed_files)

    # Get rules for prompt injection
    rules_prompt = await memory.get_rules_prompt_section()

    # After LLM generates comments, suppress accepted ones
    active_comments = await memory.filter_accepted_decisions(comments)
"""

import fnmatch
import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging_config import get_logger
from apps.ai_reviewer.models.memory import (
    AcceptedDecision,
    IgnoredPattern,
    IgnoredPatternScope,
    RepoRule,
)

logger = get_logger(__name__)


# ─────────────────────────────────────────────
# Data
# ─────────────────────────────────────────────

@dataclass
class MemoryContext:
    """
    Loaded memory context for a single review run.

    Attributes:
        rules: Active repo rules (for prompt injection)
        ignored_patterns: Glob patterns for files to skip
        accepted_fingerprints: Set of finding fingerprints that are accepted
        filtered_files: Files removed from context by ignore patterns
    """
    rules: list[RepoRule]
    ignored_patterns: list[IgnoredPattern]
    accepted_fingerprints: set[str]
    filtered_files: list[str]


# ─────────────────────────────────────────────
# Service
# ─────────────────────────────────────────────

class MemoryService:
    """
    Repository-scoped memory layer for the review pipeline.

    Responsibilities:
    - Load and cache repo rules
    - Filter files by ignored patterns before review
    - Suppress comments matching accepted decisions
    - Provide formatted rules section for LLM prompts
    """

    def __init__(
        self,
        db: AsyncSession,
        repository_id: str,
    ):
        """
        Initialize memory service.

        Args:
            db: Async database session
            repository_id: UUID of the repository being reviewed
        """
        self.db = db
        self.repository_id = repository_id
        self._context: MemoryContext | None = None

    async def load(self) -> MemoryContext:
        """
        Load all memory data for this repository.

        Fetches rules, patterns, and accepted decisions from DB.
        Result is cached for the lifetime of this service instance.

        Returns:
            MemoryContext with all loaded data
        """
        if self._context is not None:
            return self._context

        rules = await self._load_rules()
        patterns = await self._load_ignored_patterns()
        fingerprints = await self._load_accepted_fingerprints()

        self._context = MemoryContext(
            rules=rules,
            ignored_patterns=patterns,
            accepted_fingerprints=fingerprints,
            filtered_files=[],  # Populated by filter_files()
        )

        logger.info(
            f'Memory loaded for repo {self.repository_id[:8]}: '
            f'{len(rules)} rules, {len(patterns)} patterns, '
            f'{len(fingerprints)} accepted decisions'
        )

        return self._context

    async def _load_rules(self) -> list[RepoRule]:
        """Load active repo rules."""
        result = await self.db.execute(
            select(RepoRule)
            .where(
                RepoRule.repository_id == self.repository_id,
                RepoRule.is_active == True,  # noqa: E712
            )
            .order_by(RepoRule.created_at)
        )
        return list(result.scalars().all())

    async def _load_ignored_patterns(self) -> list[IgnoredPattern]:
        """Load both global and repo-specific ignored patterns."""
        result = await self.db.execute(
            select(IgnoredPattern)
            .where(
                IgnoredPattern.is_active == True,  # noqa: E712
            )
            .where(
                # Either global patterns OR patterns for this specific repo
                (IgnoredPattern.scope == IgnoredPatternScope.GLOBAL.value) |
                (IgnoredPattern.repository_id == self.repository_id)
            )
        )
        return list(result.scalars().all())

    async def _load_accepted_fingerprints(self) -> set[str]:
        """Load fingerprints of non-expired accepted decisions."""
        result = await self.db.execute(
            select(AcceptedDecision.finding_fingerprint)
            .where(
                AcceptedDecision.repository_id == self.repository_id,
                AcceptedDecision.finding_fingerprint.isnot(None),
            )
        )
        rows = result.scalars().all()
        return {fp for fp in rows if fp}

    async def filter_files(
        self,
        file_paths: list[str],
    ) -> list[str]:
        """
        Remove files matching ignored patterns from the list.

        This filters BEFORE the LLM sees the diff, so ignored files
        don't consume tokens or influence the review.

        Args:
            file_paths: List of relative file paths from the snapshot diff

        Returns:
            Filtered list with ignored files removed
        """
        context = await self.load()

        if not context.ignored_patterns:
            return file_paths

        kept = []
        filtered = []

        for path in file_paths:
            should_ignore = False
            for pattern_obj in context.ignored_patterns:
                if fnmatch.fnmatch(path, pattern_obj.pattern):
                    should_ignore = True
                    logger.debug(
                        f'Ignoring file {path!r} (pattern: {pattern_obj.pattern!r})'
                    )
                    break

            if should_ignore:
                filtered.append(path)
            else:
                kept.append(path)

        if filtered:
            context.filtered_files = filtered
            logger.info(
                f'Memory filtered {len(filtered)} files: {filtered[:5]}'
            )

        return kept

    async def get_rules_prompt_section(self) -> str:
        """
        Build a formatted rules section for injection into the LLM prompt.

        Returns:
            Markdown-formatted string ready to embed in a prompt.
            Returns empty string if no rules are defined.
        """
        context = await self.load()

        if not context.rules:
            return ''

        parts = ['## Project-Specific Rules']
        parts.append(
            'The following project conventions MUST be respected when reviewing:'
        )
        parts.append('')

        for rule in context.rules:
            parts.append(f'### {rule.title}')
            parts.append(rule.description)
            if rule.category:
                parts.append(f'_(Category: {rule.category})_')
            parts.append('')

        return '\n'.join(parts)

    async def filter_accepted_decisions(
        self,
        comments: list,
    ) -> tuple[list, int]:
        """
        Remove comments that match previously accepted decisions.

        A comment is suppressed if its finding fingerprint matches
        an accepted decision that hasn't expired.

        Args:
            comments: List of GeneratedComment objects

        Returns:
            Tuple of (active_comments, suppressed_count)
        """
        context = await self.load()

        if not context.accepted_fingerprints:
            return comments, 0

        active = []
        suppressed_count = 0

        for comment in comments:
            fingerprint = self._compute_fingerprint(comment)
            if fingerprint in context.accepted_fingerprints:
                suppressed_count += 1
                logger.debug(
                    f'Suppressed accepted finding: {fingerprint} '
                    f'({getattr(comment, "file_path", "")})'
                )
            else:
                active.append(comment)

        if suppressed_count > 0:
            logger.info(
                f'Memory suppressed {suppressed_count} previously accepted findings'
            )

        return active, suppressed_count

    def _compute_fingerprint(self, comment) -> str:
        """
        Compute a stable fingerprint for a comment.

        The fingerprint is based on the category and a normalized form
        of the explanation, so minor rephrasing doesn't prevent suppression.

        Args:
            comment: GeneratedComment object

        Returns:
            Hex string fingerprint (SHA-256 prefix)
        """
        category = getattr(comment, 'category', 'unknown')
        explanation = getattr(comment, 'explanation', '')

        # Normalize: lowercase, strip whitespace, keep only alphanum
        normalized = ''.join(
            c for c in explanation.lower()
            if c.isalnum() or c.isspace()
        ).split()
        # Use first 10 significant words for fingerprint stability
        key_words = ' '.join(normalized[:10])

        raw = f'{category}:{key_words}'
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    async def record_accepted_decision(
        self,
        comment,
        rationale: str | None = None,
        accepted_by: str | None = None,
    ) -> AcceptedDecision:
        """
        Record that a human reviewer accepted/dismissed a finding.

        Future reviews will suppress this finding if the fingerprint matches.

        Args:
            comment: The GeneratedComment that was accepted
            rationale: Human-provided reason for dismissing
            accepted_by: Identifier of the reviewer (e.g. GitHub username)

        Returns:
            The created AcceptedDecision record
        """
        fingerprint = self._compute_fingerprint(comment)
        file_path = getattr(comment, 'file_path', '')
        category = getattr(comment, 'category', None)
        severity = getattr(comment, 'severity', None)
        explanation = getattr(comment, 'explanation', '')

        decision = AcceptedDecision(
            repository_id=self.repository_id,
            file_pattern=file_path,
            rule_category=category,
            finding_fingerprint=fingerprint,
            original_comment=explanation,
            original_severity=severity,
            rationale=rationale,
            accepted_by=accepted_by,
        )

        self.db.add(decision)
        await self.db.flush()

        logger.info(
            f'Recorded accepted decision: {fingerprint} '
            f'({file_path}, {category})'
        )

        return decision
