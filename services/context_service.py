"""
ContextService - Build context from PR diff for AI review.

This service parses the diff content from a snapshot and builds
structured context that can be used by the AI review service.

Process:
1. Parse diff hunks
2. Extract changed files and line ranges
3. Build context with file content
4. Count tokens for budget management
5. Truncate if needed to fit token limit
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.diff_parser import DiffParser, ParsedDiff
from core.logging_config import get_logger
from models.snapshot import Snapshot, SnapshotStatus

logger = get_logger(__name__)

# Default token limit for context
DEFAULT_MAX_CONTEXT_TOKENS = 50000

# Approximate tokens per character (for estimation)
TOKENS_PER_CHAR = 0.25


@dataclass
class FileContext:
    """Context information for a single file."""

    file_path: str
    status: str  # 'added', 'modified', 'deleted', 'renamed'
    hunks: list[dict[str, Any]] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    old_path: str | None = None  # For renamed files

    @property
    def total_changes(self) -> int:
        """Total number of changed lines."""
        return self.additions + self.deletions


@dataclass
class SnapshotContext:
    """
    Full context for a snapshot.

    Contains all parsed diff information needed for AI review.
    """

    snapshot_id: str
    commit_sha: str
    files: list[FileContext] = field(default_factory=list)
    total_additions: int = 0
    total_deletions: int = 0
    estimated_tokens: int = 0

    @property
    def file_count(self) -> int:
        """Number of files in context."""
        return len(self.files)

    @property
    def total_changes(self) -> int:
        """Total number of changed lines."""
        return self.total_additions + self.total_deletions

    def to_dict(self) -> dict[str, Any]:
        """Convert context to dictionary for storage."""
        return {
            'snapshot_id': self.snapshot_id,
            'commit_sha': self.commit_sha,
            'file_count': self.file_count,
            'total_additions': self.total_additions,
            'total_deletions': self.total_deletions,
            'estimated_tokens': self.estimated_tokens,
            'files': [
                {
                    'file_path': f.file_path,
                    'status': f.status,
                    'additions': f.additions,
                    'deletions': f.deletions,
                    'old_path': f.old_path,
                    'hunk_count': len(f.hunks),
                }
                for f in self.files
            ],
        }


class ContextService:
    """
    Build context from PR diff for AI review.

    Responsibilities:
    - Parse diff content into structured format
    - Extract changed files and line ranges
    - Build context with full diff information
    - Estimate token count for budget management
    """

    def __init__(
        self,
        db: AsyncSession,
        max_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    ):
        """
        Initialize context service.

        Args:
            db: Database session
            max_tokens: Maximum tokens allowed in context
        """
        self.db = db
        self.max_tokens = max_tokens

    async def build_context(self, snapshot: Snapshot) -> SnapshotContext:
        """
        Build context from snapshot diff.

        Args:
            snapshot: Snapshot with diff_content

        Returns:
            SnapshotContext with parsed diff information
        """
        logger.info(f'Building context for snapshot {snapshot.id[:8]}')

        # Parse the diff content
        files = self._parse_diff(snapshot.diff_content or '')

        # Calculate totals
        total_additions = sum(f.additions for f in files)
        total_deletions = sum(f.deletions for f in files)

        # Estimate token count
        estimated_tokens = self._estimate_tokens(snapshot.diff_content or '')

        # Build context object
        context = SnapshotContext(
            snapshot_id=str(snapshot.id),
            commit_sha=snapshot.commit_sha,
            files=files,
            total_additions=total_additions,
            total_deletions=total_deletions,
            estimated_tokens=estimated_tokens,
        )

        # Update snapshot with token count
        snapshot.context_token_count = estimated_tokens
        await self.db.flush()

        logger.info(
            f'Context built: {context.file_count} files, '
            f'+{total_additions}/-{total_deletions}, '
            f'~{estimated_tokens} tokens'
        )

        return context

    def _parse_diff(self, diff_content: str) -> list[FileContext]:
        """
        Parse diff content into FileContext objects.

        Args:
            diff_content: Raw diff string

        Returns:
            List of FileContext objects
        """
        if not diff_content:
            return []

        parsed_diffs: list[ParsedDiff] = DiffParser.parse_multi_file_diff(diff_content)

        files = []
        for parsed in parsed_diffs:
            # Determine file status
            if parsed.is_new_file:
                status = 'added'
            elif parsed.is_deleted_file:
                status = 'deleted'
            elif parsed.is_renamed:
                status = 'renamed'
            else:
                status = 'modified'

            # Build hunks list
            hunks = []
            additions = 0
            deletions = 0

            for hunk in parsed.hunks:
                added_lines = hunk.get_added_lines()
                deleted_lines = hunk.get_deleted_lines()

                hunks.append({
                    'old_start': hunk.old_start,
                    'old_count': hunk.old_count,
                    'new_start': hunk.new_start,
                    'new_count': hunk.new_count,
                    'added_lines': [
                        {'line': ln, 'content': content}
                        for ln, content in added_lines
                    ],
                    'deleted_lines': [
                        {'line': ln, 'content': content}
                        for ln, content in deleted_lines
                    ],
                })

                additions += len(added_lines)
                deletions += len(deleted_lines)

            file_context = FileContext(
                file_path=parsed.file_path,
                status=status,
                hunks=hunks,
                additions=additions,
                deletions=deletions,
                old_path=parsed.old_path if parsed.is_renamed else None,
            )

            files.append(file_context)

        return files

    def _estimate_tokens(self, content: str) -> int:
        """
        Estimate token count for content.

        Uses a simple character-based estimation.
        For more accurate counting, integrate with tiktoken or similar.

        Args:
            content: Text content

        Returns:
            Estimated token count
        """
        return int(len(content) * TOKENS_PER_CHAR)

    def build_review_prompt_context(
        self,
        context: SnapshotContext,
        include_full_diff: bool = True,
    ) -> str:
        """
        Build context string for AI review prompt.

        Args:
            context: Parsed snapshot context
            include_full_diff: Whether to include full diff content

        Returns:
            Formatted context string for AI prompt
        """
        parts = []

        # Header
        parts.append(f'## Code Review Context')
        parts.append(f'Commit: {context.commit_sha[:8]}')
        parts.append(f'Files Changed: {context.file_count}')
        parts.append(f'Total Changes: +{context.total_additions}/-{context.total_deletions}')
        parts.append('')

        # Files summary
        parts.append('### Changed Files')
        for f in context.files:
            status_emoji = {
                'added': '➕',
                'deleted': '➖',
                'modified': '📝',
                'renamed': '📛',
            }.get(f.status, '📄')
            parts.append(f'- {status_emoji} `{f.file_path}` (+{f.additions}/-{f.deletions})')

        parts.append('')

        # Detailed diffs
        if include_full_diff:
            parts.append('### Detailed Changes')
            parts.append('')

            for f in context.files:
                parts.append(f'#### {f.file_path}')

                for i, hunk in enumerate(f.hunks):
                    parts.append(f'```diff')
                    parts.append(f'@@ -{hunk["old_start"]},{hunk["old_count"]} +{hunk["new_start"]},{hunk["new_count"]} @@')

                    for deleted in hunk.get('deleted_lines', []):
                        parts.append(f'-{deleted["content"]}')
                    for added in hunk.get('added_lines', []):
                        parts.append(f'+{added["content"]}')

                    parts.append('```')
                    parts.append('')

        return '\n'.join(parts)

    async def get_snapshot(self, snapshot_id: str) -> Snapshot | None:
        """Get snapshot by ID."""
        result = await self.db.execute(
            select(Snapshot).where(Snapshot.id == snapshot_id)
        )
        return result.scalar_one_or_none()

    async def update_snapshot_status(
        self,
        snapshot: Snapshot,
        status: SnapshotStatus,
    ) -> None:
        """Update snapshot status."""
        snapshot.status = status.value
        await self.db.flush()
