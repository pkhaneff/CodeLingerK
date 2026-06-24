"""
SnapshotService - Creates immutable snapshots of PR state.

This is the entry point for the review pipeline. When a webhook is received,
SnapshotService creates an immutable snapshot of the PR state at that commit.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging_config import get_logger
from models.pull_request import PullRequest, PullRequestStatus
from models.snapshot import Snapshot, SnapshotStatus
from models.repository import Repository
from models.review_job import ReviewJob
from services.providers.base import GitProvider

logger = get_logger(__name__)


class SnapshotService:
    """
    Creates immutable snapshots when PR is opened/updated.

    Responsibilities:
    - Get or create PullRequest record
    - Create Snapshot record from webhook payload
    - Fetch diff from GitHub API
    - Store diff content and file list
    - Calculate processing priority
    """

    def __init__(self, db: AsyncSession, git_provider: GitProvider):
        """
        Initialize snapshot service.

        Args:
            db: Database session
            git_provider: GitProvider instance for API calls
        """
        self.db = db
        self.git_provider = git_provider

    async def get_or_create_pull_request(
        self,
        repository: Repository,
        pr_number: int,
        title: str | None = None,
        source_branch: str | None = None,
        target_branch: str | None = None,
        author: str | None = None,
        html_url: str | None = None,
    ) -> PullRequest:
        """
        Get existing PR or create new record.

        Args:
            repository: Repository this PR belongs to
            pr_number: PR number from GitHub
            title: PR title (optional, fetched from API if not provided)
            source_branch: Head branch (optional)
            target_branch: Base branch (optional)
            author: PR author username (optional)
            html_url: GitHub PR URL (optional)

        Returns:
            PullRequest model
        """
        # Check for existing PR
        result = await self.db.execute(
            select(PullRequest).where(
                PullRequest.repository_id == repository.id,
                PullRequest.pr_number == pr_number,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Update fields if provided
            if title:
                existing.title = title
            if source_branch:
                existing.source_branch = source_branch
            if target_branch:
                existing.target_branch = target_branch
            if author:
                existing.author = author
            if html_url:
                existing.html_url = html_url

            await self.db.flush()
            return existing

        # Fetch PR details from GitHub if not provided
        if not title or not source_branch or not target_branch:
            try:
                pr_info = await self.git_provider.get_pr(
                    repo_identifier=repository.full_name,
                    pr_number=pr_number,
                )
                title = title or pr_info.get('title', f'PR #{pr_number}')
                source_branch = source_branch or pr_info.get('source_branch', 'unknown')
                target_branch = target_branch or pr_info.get('target_branch', 'main')
                html_url = html_url or pr_info.get('html_url')
            except Exception as e:
                logger.warning(f'Failed to fetch PR info: {e}')
                title = title or f'PR #{pr_number}'
                source_branch = source_branch or 'unknown'
                target_branch = target_branch or 'main'

        # Create new PR record
        pull_request = PullRequest(
            repository_id=repository.id,
            pr_number=pr_number,
            title=title,
            status=PullRequestStatus.OPEN.value,
            source_branch=source_branch,
            target_branch=target_branch,
            author=author,
            html_url=html_url,
        )

        self.db.add(pull_request)
        await self.db.flush()
        await self.db.refresh(pull_request)

        logger.info(f'Created PullRequest #{pr_number} for {repository.full_name}')
        return pull_request

    async def snapshot_exists(
        self,
        pull_request_id: str,
        commit_sha: str,
    ) -> bool:
        """
        Check if snapshot already exists (idempotent).

        Args:
            pull_request_id: PullRequest ID
            commit_sha: Commit SHA to check

        Returns:
            True if snapshot exists
        """
        result = await self.db.execute(
            select(Snapshot.id).where(
                Snapshot.pull_request_id == pull_request_id,
                Snapshot.commit_sha == commit_sha,
            )
        )
        return result.scalar_one_or_none() is not None

    async def create_snapshot(
        self,
        repository: Repository,
        pr_number: int,
        commit_sha: str,
        source_branch: str | None = None,
        target_branch: str | None = None,
        title: str | None = None,
        author: str | None = None,
    ) -> Snapshot:
        """
        Create immutable snapshot of PR state.

        Args:
            repository: Repository model
            pr_number: PR number
            commit_sha: Exact commit SHA (40 chars)
            source_branch: Head branch (optional)
            target_branch: Base branch (optional)
            title: PR title (optional)
            author: PR author (optional)

        Returns:
            Created Snapshot model

        Raises:
            ValueError: If snapshot already exists for this commit
        """
        # Get or create PR
        pull_request = await self.get_or_create_pull_request(
            repository=repository,
            pr_number=pr_number,
            title=title,
            source_branch=source_branch,
            target_branch=target_branch,
            author=author,
        )

        # Check idempotency
        if await self.snapshot_exists(pull_request.id, commit_sha):
            logger.info(f'Snapshot already exists for PR #{pr_number} @ {commit_sha[:8]}')
            # Return existing snapshot
            result = await self.db.execute(
                select(Snapshot).where(
                    Snapshot.pull_request_id == pull_request.id,
                    Snapshot.commit_sha == commit_sha,
                )
            )
            return result.scalar_one()

        # Fetch PR files and diff from GitHub
        try:
            pr_files = await self.git_provider.get_pr_files(
                repo_identifier=repository.full_name,
                pr_number=pr_number,
            )
        except Exception as e:
            logger.error(f'Failed to fetch PR files: {e}')
            pr_files = []

        # Extract file info
        files_changed = [f.get('filename', '') for f in pr_files if f.get('filename')]
        additions = sum(f.get('additions', 0) for f in pr_files)
        deletions = sum(f.get('deletions', 0) for f in pr_files)

        # Build combined diff content
        diff_content = self._build_diff_content(pr_files)

        # Create snapshot
        snapshot = Snapshot(
            pull_request_id=pull_request.id,
            commit_sha=commit_sha,
            diff_content=diff_content,
            files_changed=files_changed,
            additions=additions,
            deletions=deletions,
            status=SnapshotStatus.PENDING.value,
        )

        self.db.add(snapshot)
        await self.db.flush()
        await self.db.refresh(snapshot)

        logger.info(
            f'Created snapshot for PR #{pr_number} @ {commit_sha[:8]}: '
            f'{len(files_changed)} files, +{additions}/-{deletions}'
        )

        return snapshot

    def _build_diff_content(self, pr_files: list[dict]) -> str:
        """
        Build combined diff content from PR files.

        Args:
            pr_files: List of file change dicts from GitHub API

        Returns:
            Combined unified diff string
        """
        diff_parts = []

        for file_info in pr_files:
            filename = file_info.get('filename', '')
            status = file_info.get('status', '')
            patch = file_info.get('patch', '')

            if not patch:
                continue

            # Add file header
            if status == 'added':
                diff_parts.append(f'diff --git a/{filename} b/{filename}')
                diff_parts.append('new file mode 100644')
                diff_parts.append(f'--- /dev/null')
                diff_parts.append(f'+++ b/{filename}')
            elif status == 'removed':
                diff_parts.append(f'diff --git a/{filename} b/{filename}')
                diff_parts.append('deleted file mode 100644')
                diff_parts.append(f'--- a/{filename}')
                diff_parts.append(f'+++ /dev/null')
            else:
                diff_parts.append(f'diff --git a/{filename} b/{filename}')
                diff_parts.append(f'--- a/{filename}')
                diff_parts.append(f'+++ b/{filename}')

            diff_parts.append(patch)
            diff_parts.append('')  # Empty line between files

        return '\n'.join(diff_parts)

    def calculate_priority(self, snapshot: Snapshot) -> int:
        """
        Calculate job priority based on PR size.

        Small PRs get high priority for fast feedback.

        Args:
            snapshot: Snapshot to calculate priority for

        Returns:
            Priority value (0-100, higher = process first)
        """
        files_count = snapshot.files_count
        total_changes = snapshot.total_changes

        if files_count <= 3 and total_changes <= 50:
            return 95  # Tiny PR - immediate
        elif files_count <= 5 and total_changes <= 100:
            return 80  # Small PR - high priority
        elif files_count <= 10 and total_changes <= 300:
            return 60  # Medium PR - normal
        elif files_count <= 20 and total_changes <= 500:
            return 40  # Large PR - lower priority
        else:
            return 20  # Huge PR - lowest priority

    async def get_snapshot(self, snapshot_id: str) -> Snapshot | None:
        """Get snapshot by ID."""
        result = await self.db.execute(
            select(Snapshot).where(Snapshot.id == snapshot_id)
        )
        return result.scalar_one_or_none()

    async def get_snapshots_for_pr(
        self,
        pull_request_id: str,
        limit: int = 10,
    ) -> list[Snapshot]:
        """
        Get snapshots for a PR, ordered by creation time (newest first).

        Args:
            pull_request_id: PullRequest ID
            limit: Maximum number of snapshots to return

        Returns:
            List of Snapshot models
        """
        result = await self.db.execute(
            select(Snapshot)
            .where(Snapshot.pull_request_id == pull_request_id)
            .order_by(Snapshot.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update_snapshot_status(
        self,
        snapshot: Snapshot,
        status: SnapshotStatus,
        error_message: str | None = None,
    ) -> None:
        """
        Update snapshot processing status.

        Args:
            snapshot: Snapshot to update
            status: New status
            error_message: Error message if failed
        """
        snapshot.status = status.value
        if error_message:
            snapshot.error_message = error_message

        if status == SnapshotStatus.COMPLETED:
            snapshot.mark_completed()
        elif status == SnapshotStatus.FAILED:
            snapshot.mark_failed(error_message or 'Unknown error')

        await self.db.flush()
        logger.info(f'Snapshot {snapshot.id[:8]} status updated to {status.value}')
