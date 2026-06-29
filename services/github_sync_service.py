"""
GitHubSyncService - Sync reviews to Git providers.

Posts AI-generated review summary and inline comments as bot review.
Tracks sync status and handles errors gracefully.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.logging_config import get_logger
from models.pull_request import PullRequest
from models.repository import Repository
from models.review import Review, ReviewComment
from models.snapshot import Snapshot
from models.user import User
from infra.config import settings
from services.providers.base import GitProvider, ReviewComment as ProviderReviewComment
from services.providers.factory import GitProviderFactory, GitProviderType

logger = get_logger(__name__)


class GitHubSyncService:
    """
    Service for syncing reviews to Git providers.

    Responsibilities:
    - Post review summary as bot review comment
    - Post inline comments via review API
    - Track provider_comment_id for each comment
    - Handle sync errors gracefully
    """

    def __init__(self, db: AsyncSession):
        """
        Initialize sync service.

        Args:
            db: Database session
        """
        self.db = db

    async def sync_review(
        self,
        review: Review,
        snapshot: Snapshot,
    ) -> dict[str, Any]:
        """
        Sync review to GitHub.

        Args:
            review: Review to sync
            snapshot: Snapshot with PR information

        Returns:
            Sync result with posted comment IDs
        """
        logger.info(f'Syncing review {review.id[:8]} to GitHub')

        # Get PR and repository info
        pull_request = await self._get_pull_request(snapshot.pull_request_id)
        if not pull_request:
            raise ValueError(f'PullRequest not found: {snapshot.pull_request_id}')

        repository = await self._get_repository(pull_request.repository_id)
        if not repository:
            raise ValueError(f'Repository not found: {pull_request.repository_id}')

        # Create provider instance
        # For GitHub: use App provider (bot) if configured, otherwise use user OAuth
        provider_type = GitProviderType(repository.provider)

        if provider_type == GitProviderType.GITHUB and settings.github_app_enabled:
            # Use GitHub App - comments will appear as "CodeLingerK [bot]"
            git_provider = GitProviderFactory.create_github_app()
            logger.info('Using GitHub App provider for bot comments')
        else:
            # Fallback to user OAuth token
            owner = await self._get_repository_owner(repository.id)
            if not owner:
                raise ValueError('Repository owner not found')

            access_token = owner.get_access_token(repository.provider)
            if not access_token:
                raise ValueError(f'No access token for {repository.provider}')

            git_provider = GitProviderFactory.create(provider_type, access_token)

        # Build review body
        review_body = self._build_review_body(review)

        # Get unsynced comments
        comments = await self._get_unsynced_comments(review.id)

        # Convert to provider format
        provider_comments = self._convert_comments(comments)

        # Sync review to provider
        try:
            # Post review with summary and inline comments
            # This appears as "CodeLingerK [bot] reviewed" instead of "edited by"
            result = await git_provider.post_pr_review(
                repo_identifier=repository.full_name,
                pr_number=pull_request.pr_number,
                commit_sha=snapshot.commit_sha,
                body=review_body,
                comments=provider_comments,
            )

            # Update review with GitHub review ID
            if 'id' in result:
                review.github_review_id = result['id']

            # Mark comments as synced
            for comment in comments:
                comment.is_synced = True
                comment.sync_error = None

            await self.db.flush()

            logger.info(
                f'Synced review to provider: review_id={result.get("id")}, '
                f'comments={len(comments)}'
            )

            return {
                'status': 'synced',
                'github_review_id': result.get('id'),
                'comments_synced': len(comments),
            }

        except Exception as e:
            logger.error(f'Failed to sync review to provider: {e}')

            # Mark comments with sync error
            for comment in comments:
                comment.sync_error = str(e)

            await self.db.flush()

            return {
                'status': 'error',
                'error': str(e),
                'comments_synced': 0,
            }

    def _build_review_body(self, review: Review) -> str:
        """Build review summary body for GitHub."""
        parts = []

        # Header
        parts.append('## 🔍 CodeLingerK AI Review')
        parts.append('')

        # Summary
        if review.summary:
            parts.append(review.summary)
            parts.append('')

        # Verdict
        verdict_emoji = {
            'approved': '✅',
            'changes_requested': '⚠️',
            'needs_discussion': '💬',
        }
        emoji = verdict_emoji.get(review.verdict, '📝')
        if review.verdict:
            parts.append(f'**Verdict:** {emoji} {review.verdict.replace("_", " ").title()}')
            parts.append('')

        # Statistics
        if review.files_analyzed:
            parts.append(f'📁 **Files Analyzed:** {review.files_analyzed}')

        parts.append('')
        parts.append('---')
        parts.append('*Generated by [CodeLingerK](https://github.com/codelingerk)*')

        return '\n'.join(parts)

    def _convert_comments(
        self,
        comments: list[ReviewComment],
    ) -> list[ProviderReviewComment]:
        """Convert ReviewComment models to provider format."""
        provider_comments: list[ProviderReviewComment] = []

        for comment in comments:
            # Build comment body with severity and suggestion
            body_parts = []

            # Severity badge
            severity_badge = {
                'critical': '🚨 **Critical:**',
                'error': '❌ **Error:**',
                'warning': '⚠️ **Warning:**',
                'info': 'ℹ️ **Info:**',
            }
            badge = severity_badge.get(comment.severity, '📝')
            body_parts.append(f'{badge}')
            body_parts.append('')

            # Category if available
            if comment.category:
                body_parts.append(f'**Category:** {comment.category}')
                body_parts.append('')

            # Main comment
            body_parts.append(comment.comment)

            # Suggestion if available
            if comment.suggestion:
                body_parts.append('')
                body_parts.append('**Suggestion:**')
                body_parts.append(f'```')
                body_parts.append(comment.suggestion)
                body_parts.append('```')

            # Confidence
            if comment.confidence:
                body_parts.append('')
                confidence_pct = int(comment.confidence * 100)
                body_parts.append(f'*Confidence: {confidence_pct}%*')

            provider_comment: ProviderReviewComment = {
                'path': comment.file_path,
                'body': '\n'.join(body_parts),
                'line': comment.line_start or 1,
                'side': 'RIGHT',
            }

            provider_comments.append(provider_comment)

        return provider_comments

    async def _get_pull_request(self, pr_id: str) -> PullRequest | None:
        """Get PullRequest by ID."""
        result = await self.db.execute(
            select(PullRequest).where(PullRequest.id == pr_id)
        )
        return result.scalar_one_or_none()

    async def _get_repository(self, repo_id: str) -> Repository | None:
        """Get Repository by ID."""
        result = await self.db.execute(
            select(Repository).where(Repository.id == repo_id)
        )
        return result.scalar_one_or_none()

    async def _get_repository_owner(self, repo_id: str) -> User | None:
        """Get Repository owner."""
        result = await self.db.execute(
            select(Repository)
            .options(selectinload(Repository.owner))
            .where(Repository.id == repo_id)
        )
        repo = result.scalar_one_or_none()
        return repo.owner if repo else None

    async def _get_unsynced_comments(
        self,
        review_id: str,
    ) -> list[ReviewComment]:
        """Get unsynced comments for a review."""
        result = await self.db.execute(
            select(ReviewComment)
            .where(
                ReviewComment.review_id == review_id,
                ReviewComment.is_synced == False,  # noqa: E712
            )
            .order_by(ReviewComment.created_at)
        )
        return list(result.scalars().all())

    async def retry_failed_comments(
        self,
        review_id: str,
    ) -> dict[str, Any]:
        """
        Retry syncing failed comments.

        Args:
            review_id: Review ID to retry

        Returns:
            Retry result
        """
        # Get review with snapshot
        result = await self.db.execute(
            select(Review)
            .options(selectinload(Review.snapshot))
            .where(Review.id == review_id)
        )
        review = result.scalar_one_or_none()

        if not review:
            raise ValueError(f'Review not found: {review_id}')

        if not review.snapshot:
            raise ValueError(f'Review has no associated snapshot')

        return await self.sync_review(review, review.snapshot)
