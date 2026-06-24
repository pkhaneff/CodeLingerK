"""
AIReviewService - Multi-pass AI code review pipeline.

Runs a 5-pass review process:
1. Understanding: What changed?
2. Risks: What could break?
3. Quality: Can we simplify?
4. Business: Does it violate intent?
5. Comments: Generate actionable comments

Each pass builds on the previous, resulting in high-quality,
context-aware review comments.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.ai_client import AIClient, create_ai_client_from_settings
from core.logging_config import get_logger
from infra.config import settings
from models.layer import Layer
from models.review import Review, ReviewComment, ReviewStatus, ReviewVerdict, CommentSeverity
from models.snapshot import Snapshot, SnapshotStatus
from services.context_service import ContextService, SnapshotContext

logger = get_logger(__name__)


class ReviewCategory(str, Enum):
    """Review comment categories."""
    BUG = 'bug'
    SECURITY = 'security'
    PERFORMANCE = 'performance'
    DESIGN = 'design'
    MAINTAINABILITY = 'maintainability'
    TESTING = 'testing'
    DOCUMENTATION = 'documentation'


@dataclass
class ReviewPass:
    """Result of a single review pass."""

    name: str
    prompt: str
    response: str
    tokens_used: int
    duration_ms: int
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class GeneratedComment:
    """AI-generated review comment."""

    file_path: str
    line_start: int
    line_end: int | None
    severity: str
    category: str
    explanation: str
    suggestion: str | None
    confidence: float


@dataclass
class ReviewResult:
    """Complete result of AI review."""

    passes: list[ReviewPass]
    comments: list[GeneratedComment]
    summary: str
    verdict: str
    total_tokens: int
    duration_ms: int


class AIReviewService:
    """
    Multi-pass AI code review service.

    Runs a structured 5-pass review pipeline that builds context
    progressively to generate high-quality review comments.
    """

    # System prompts for each pass
    SYSTEM_PROMPTS = {
        'understanding': '''You are an expert code reviewer analyzing changes in a pull request.
Your task is to understand WHAT changed and WHY.

Respond with a JSON object containing:
{
  "summary": "Brief summary of changes (2-3 sentences)",
  "intent": "What the developer is trying to achieve",
  "scope": "affected areas (api, database, ui, etc.)",
  "complexity": "low/medium/high",
  "key_changes": ["list of most important changes"]
}''',

        'risks': '''You are a senior engineer reviewing code for potential risks.
Based on the understanding from Pass 1, identify what could go wrong.

Respond with a JSON object containing:
{
  "breaking_changes": ["list of potential breaking changes"],
  "security_concerns": ["list of security issues"],
  "performance_issues": ["list of performance concerns"],
  "data_integrity": ["list of data integrity risks"],
  "risk_level": "low/medium/high/critical"
}''',

        'quality': '''You are a code quality expert reviewing for clean code principles.
Focus on simplification and maintainability.

Respond with a JSON object containing:
{
  "complexity_issues": ["overly complex code"],
  "duplication": ["duplicated code"],
  "naming_issues": ["poor naming"],
  "design_smells": ["design problems"],
  "simplification_opportunities": ["ways to simplify"]
}''',

        'business': '''You are reviewing code for business logic correctness.
Consider if the implementation matches the intent.

Respond with a JSON object containing:
{
  "intent_violations": ["where implementation differs from intent"],
  "edge_cases": ["unhandled edge cases"],
  "validation_gaps": ["missing input validation"],
  "business_risks": ["business logic concerns"]
}''',

        'comments': '''You are generating actionable code review comments.
Based on all previous analysis, generate specific inline comments.

Respond with a JSON array of comments:
[
  {
    "file_path": "path/to/file.py",
    "line_start": 42,
    "line_end": null,
    "severity": "warning",
    "category": "security",
    "explanation": "Why this is an issue",
    "suggestion": "How to fix it",
    "confidence": 0.85
  }
]

Rules:
- Max 5 comments per file
- Max 20 comments total
- Focus on most impactful issues
- severity: info, warning, error, critical
- category: bug, security, performance, design, maintainability, testing
- confidence: 0.0-1.0 (how certain you are)'''
    }

    def __init__(
        self,
        db: AsyncSession,
        ai_client: AIClient | None = None,
    ):
        """
        Initialize AI review service.

        Args:
            db: Database session
            ai_client: AI client (creates default if None)
        """
        self.db = db
        self.ai_client = ai_client or self._create_ai_client()
        self.max_comments_per_file = settings.review_max_comments_per_file
        self.max_comments_per_pr = settings.review_max_comments_per_pr

    def _create_ai_client(self) -> AIClient:
        """Create AI client from settings using factory."""
        return create_ai_client_from_settings()

    async def review_snapshot(
        self,
        snapshot: Snapshot,
        context: SnapshotContext,
        layers: list[Layer],
    ) -> ReviewResult:
        """
        Run complete 5-pass review on snapshot.

        Args:
            snapshot: Snapshot being reviewed
            context: Parsed context with diff information
            layers: Functional layers for the snapshot

        Returns:
            ReviewResult with all passes and comments
        """
        start_time = time.time()
        passes: list[ReviewPass] = []
        total_tokens = 0

        logger.info(f'Starting AI review for snapshot {snapshot.id[:8]}')

        # Build context string for prompts
        context_str = self._build_context_string(context, layers)

        # Pass 1: Understanding
        understanding = await self._run_pass(
            'understanding',
            self._build_understanding_prompt(context_str),
        )
        passes.append(understanding)
        total_tokens += understanding.tokens_used

        # Pass 2: Risks
        risks = await self._run_pass(
            'risks',
            self._build_risks_prompt(context_str, understanding.data),
        )
        passes.append(risks)
        total_tokens += risks.tokens_used

        # Pass 3: Quality
        quality = await self._run_pass(
            'quality',
            self._build_quality_prompt(context_str, understanding.data),
        )
        passes.append(quality)
        total_tokens += quality.tokens_used

        # Pass 4: Business
        business = await self._run_pass(
            'business',
            self._build_business_prompt(context_str, understanding.data),
        )
        passes.append(business)
        total_tokens += business.tokens_used

        # Pass 5: Generate Comments
        comments_pass = await self._run_pass(
            'comments',
            self._build_comments_prompt(
                context_str,
                understanding.data,
                risks.data,
                quality.data,
                business.data,
            ),
        )
        passes.append(comments_pass)
        total_tokens += comments_pass.tokens_used

        # Parse comments from last pass
        comments = self._parse_comments(comments_pass.data)

        # Apply limits
        comments = self._apply_comment_limits(comments)

        # Generate summary and verdict
        summary = self._generate_summary(understanding.data, risks.data)
        verdict = self._determine_verdict(risks.data, comments)

        duration_ms = int((time.time() - start_time) * 1000)

        logger.info(
            f'AI review complete: {len(comments)} comments, '
            f'{total_tokens} tokens, {duration_ms}ms'
        )

        return ReviewResult(
            passes=passes,
            comments=comments,
            summary=summary,
            verdict=verdict,
            total_tokens=total_tokens,
            duration_ms=duration_ms,
        )

    async def _run_pass(
        self,
        pass_name: str,
        prompt: str,
    ) -> ReviewPass:
        """Run a single review pass."""
        start_time = time.time()

        system_prompt = self.SYSTEM_PROMPTS.get(pass_name, '')

        try:
            response = await self.ai_client.complete_json(
                prompt=prompt,
                system_prompt=system_prompt,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            return ReviewPass(
                name=pass_name,
                prompt=prompt[:500] + '...' if len(prompt) > 500 else prompt,
                response=str(response)[:1000],
                tokens_used=self.ai_client.count_tokens(prompt + str(response)),
                duration_ms=duration_ms,
                data=(
                    response
                    if isinstance(response, (dict, list))
                    else {'raw': response}
                ),
            )
        except Exception as e:
            logger.error(f'Pass {pass_name} failed: {e}')
            return ReviewPass(
                name=pass_name,
                prompt=prompt[:500],
                response=f'Error: {e}',
                tokens_used=self.ai_client.count_tokens(prompt),
                duration_ms=int((time.time() - start_time) * 1000),
                data={'error': str(e)},
            )

    def _build_context_string(
        self,
        context: SnapshotContext,
        layers: list[Layer],
    ) -> str:
        """Build context string for AI prompts."""
        parts = []

        parts.append('## Pull Request Overview')
        parts.append(f'Files Changed: {context.file_count}')
        parts.append(f'Lines Added: {context.total_additions}')
        parts.append(f'Lines Deleted: {context.total_deletions}')
        parts.append('')

        # Layer summary
        if layers:
            parts.append('## Functional Layers')
            for layer in sorted(layers, key=lambda l: l.review_order):
                parts.append(
                    f'- {layer.layer_type.upper()} ({layer.files_count} files): '
                    f'{layer.intent or "No description"}'
                )
            parts.append('')

        # File list with status
        parts.append('## Changed Files')
        for f in context.files:
            status_icon = {
                'added': '+',
                'deleted': '-',
                'modified': 'M',
                'renamed': 'R',
            }.get(f.status, '?')
            parts.append(
                f'[{status_icon}] {f.file_path} '
                f'(+{f.additions}/-{f.deletions})'
            )
        parts.append('')

        # Diff content (truncated if needed)
        parts.append('## Diff Content')
        for f in context.files:
            parts.append(f'### {f.file_path}')
            for hunk in f.hunks:
                parts.append('```diff')
                parts.append(
                    f'@@ -{hunk.get("old_start", 0)},{hunk.get("old_count", 0)} '
                    f'+{hunk.get("new_start", 0)},{hunk.get("new_count", 0)} @@'
                )
                for deleted in hunk.get('deleted_lines', [])[:20]:
                    parts.append(f'-{deleted.get("content", "")}')
                for added in hunk.get('added_lines', [])[:20]:
                    parts.append(f'+{added.get("content", "")}')
                parts.append('```')

        return '\n'.join(parts)

    def _build_understanding_prompt(self, context: str) -> str:
        """Build prompt for understanding pass."""
        return f'''Analyze the following code changes and provide your understanding.

{context}

Provide your analysis in the specified JSON format.'''

    def _build_risks_prompt(
        self,
        context: str,
        understanding: dict[str, Any],
    ) -> str:
        """Build prompt for risks pass."""
        return f'''Based on the following code changes and understanding, identify risks.

## Previous Understanding
Summary: {understanding.get('summary', 'N/A')}
Intent: {understanding.get('intent', 'N/A')}
Complexity: {understanding.get('complexity', 'N/A')}

## Code Changes
{context}

Provide your risk analysis in the specified JSON format.'''

    def _build_quality_prompt(
        self,
        context: str,
        understanding: dict[str, Any],
    ) -> str:
        """Build prompt for quality pass."""
        return f'''Review the following code changes for quality issues.

## Context
Summary: {understanding.get('summary', 'N/A')}
Scope: {understanding.get('scope', 'N/A')}

## Code Changes
{context}

Provide your quality analysis in the specified JSON format.'''

    def _build_business_prompt(
        self,
        context: str,
        understanding: dict[str, Any],
    ) -> str:
        """Build prompt for business pass."""
        return f'''Review if the implementation matches the stated intent.

## Developer Intent
{understanding.get('intent', 'N/A')}

## Key Changes
{', '.join(understanding.get('key_changes', []))}

## Code Changes
{context}

Provide your business logic analysis in the specified JSON format.'''

    def _build_comments_prompt(
        self,
        context: str,
        understanding: dict[str, Any],
        risks: dict[str, Any],
        quality: dict[str, Any],
        business: dict[str, Any],
    ) -> str:
        """Build prompt for comments pass."""
        return f'''Generate actionable code review comments based on the analysis.

## Analysis Summary
Risk Level: {risks.get('risk_level', 'unknown')}
Security Concerns: {len(risks.get('security_concerns', []))}
Quality Issues: {len(quality.get('complexity_issues', []))}
Business Risks: {len(business.get('business_risks', []))}

## Key Issues Found

### Security
{chr(10).join(f'- {c}' for c in risks.get('security_concerns', [])[:5])}

### Breaking Changes
{chr(10).join(f'- {c}' for c in risks.get('breaking_changes', [])[:5])}

### Quality
{chr(10).join(f'- {c}' for c in quality.get('complexity_issues', [])[:5])}

### Business Logic
{chr(10).join(f'- {c}' for c in business.get('intent_violations', [])[:5])}

## Code Changes
{context}

Generate specific, actionable comments in the specified JSON format.
Focus on the most impactful issues. Be constructive and helpful.'''

    def _parse_comments(
        self,
        data: dict[str, Any] | list[Any],
    ) -> list[GeneratedComment]:
        """Parse comments from AI response."""
        comments = []

        # Handle both dict and list responses
        if isinstance(data, dict):
            if isinstance(data.get('comments'), list):
                items = data.get('comments', [])
            elif isinstance(data.get('raw'), list):
                items = data.get('raw', [])
            elif (
                isinstance(data.get('raw'), dict)
                and isinstance(data.get('raw', {}).get('comments'), list)
            ):
                items = data.get('raw', {}).get('comments', [])
            else:
                items = []
        elif isinstance(data, list):
            items = data
        else:
            return comments

        for item in items:
            if not isinstance(item, dict):
                continue

            try:
                comment = GeneratedComment(
                    file_path=item.get('file_path', ''),
                    line_start=item.get('line_start', 1),
                    line_end=item.get('line_end'),
                    severity=item.get('severity', 'info'),
                    category=item.get('category', 'design'),
                    explanation=item.get('explanation', ''),
                    suggestion=item.get('suggestion'),
                    confidence=float(item.get('confidence', 0.5)),
                )
                comments.append(comment)
            except (ValueError, KeyError) as e:
                logger.warning(f'Failed to parse comment: {e}')

        return comments

    def _apply_comment_limits(
        self,
        comments: list[GeneratedComment],
    ) -> list[GeneratedComment]:
        """Apply per-file and total comment limits."""
        # Sort by confidence (highest first)
        comments.sort(key=lambda c: c.confidence, reverse=True)

        # Apply per-file limit
        file_counts: dict[str, int] = {}
        filtered = []

        for comment in comments:
            file_count = file_counts.get(comment.file_path, 0)
            if file_count < self.max_comments_per_file:
                filtered.append(comment)
                file_counts[comment.file_path] = file_count + 1

        # Apply total limit
        return filtered[:self.max_comments_per_pr]

    def _generate_summary(
        self,
        understanding: dict[str, Any],
        risks: dict[str, Any],
    ) -> str:
        """Generate review summary."""
        parts = []

        summary = understanding.get('summary', '')
        if summary:
            parts.append(summary)

        risk_level = risks.get('risk_level', 'unknown')
        if risk_level in ('high', 'critical'):
            parts.append(f'\n⚠️ Risk Level: {risk_level.upper()}')

        security = risks.get('security_concerns', [])
        if security:
            parts.append(f'\n🔒 Security concerns identified: {len(security)}')

        breaking = risks.get('breaking_changes', [])
        if breaking:
            parts.append(f'\n💥 Potential breaking changes: {len(breaking)}')

        return '\n'.join(parts) if parts else 'Review complete.'

    def _determine_verdict(
        self,
        risks: dict[str, Any],
        comments: list[GeneratedComment],
    ) -> str:
        """Determine review verdict."""
        risk_level = risks.get('risk_level', 'low')

        # Count critical/error comments
        critical_count = sum(
            1 for c in comments
            if c.severity in ('critical', 'error')
        )

        security_count = sum(
            1 for c in comments
            if c.category == 'security'
        )

        if risk_level == 'critical' or critical_count > 0 or security_count > 0:
            return ReviewVerdict.CHANGES_REQUESTED.value
        elif risk_level == 'high' or len(comments) > 10:
            return ReviewVerdict.NEEDS_DISCUSSION.value
        else:
            return ReviewVerdict.APPROVED.value

    async def save_review(
        self,
        snapshot: Snapshot,
        result: ReviewResult,
    ) -> Review:
        """
        Save review result to database.

        Args:
            snapshot: Snapshot being reviewed
            result: AI review result

        Returns:
            Created Review record
        """
        # Create Review record
        review = Review(
            repository_id=snapshot.pull_request.repository_id,
            snapshot_id=str(snapshot.id),
            pull_request_number=snapshot.pull_request.pr_number,
            commit_sha=snapshot.commit_sha,
            review_type='pull_request',
            status=ReviewStatus.COMPLETED.value,
            verdict=result.verdict,
            summary=result.summary,
            files_analyzed=len(set(c.file_path for c in result.comments)),
            ai_model=self.ai_client.model,
            ai_tokens_used=result.total_tokens,
            processing_time_ms=result.duration_ms,
            ai_passes={
                f'pass_{i+1}_{p.name}': p.data
                for i, p in enumerate(result.passes)
            },
            review_order=[c.file_path for c in result.comments],
            completed_at=datetime.utcnow(),
        )

        self.db.add(review)
        await self.db.flush()
        await self.db.refresh(review)

        # Create ReviewComment records
        for gen_comment in result.comments:
            comment = ReviewComment(
                review_id=review.id,
                file_path=gen_comment.file_path,
                line_start=gen_comment.line_start,
                line_end=gen_comment.line_end,
                severity=gen_comment.severity,
                category=gen_comment.category,
                comment=gen_comment.explanation,
                suggestion=gen_comment.suggestion,
                confidence=gen_comment.confidence,
            )
            self.db.add(comment)

        await self.db.flush()

        logger.info(
            f'Saved review {review.id[:8]} with {len(result.comments)} comments'
        )

        return review

    async def get_review_for_snapshot(
        self,
        snapshot_id: str,
    ) -> Review | None:
        """Get review for a snapshot."""
        result = await self.db.execute(
            select(Review)
            .options(selectinload(Review.comments))
            .where(Review.snapshot_id == snapshot_id)
        )
        return result.scalar_one_or_none()
