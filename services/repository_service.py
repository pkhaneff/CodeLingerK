"""
Repository service - Manage repositories for indexing.
"""

import os
import secrets
import shutil
from pathlib import Path
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from git import Repo as GitRepo

from infra.config import settings
from models.user import User
from models.repository import Repository, IndexStatus
from services.github_service import GitHubService
from core.logging_config import get_logger

logger = get_logger(__name__)


class RepositoryService:
    """
    Service for managing repositories.

    Handles:
    - Adding repos from GitHub
    - Cloning repos locally
    - Managing webhooks
    - Tracking index status
    """

    def __init__(self, db: AsyncSession, user: User):
        """
        Initialize repository service.

        Args:
            db: Database session
            user: Authenticated user
        """
        self.db = db
        self.user = user
        self.github = GitHubService(user.github_access_token)

    async def list_github_repos(
        self,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        """
        List user's GitHub repositories.

        Returns repos from GitHub API, not from our database.
        """
        repos = await self.github.list_user_repos(page=page, per_page=per_page)

        return [
            {
                'github_id': repo['id'],
                'full_name': repo['full_name'],
                'name': repo['name'],
                'description': repo.get('description'),
                'private': repo['private'],
                'default_branch': repo.get('default_branch', 'main'),
                'clone_url': repo['clone_url'],
                'html_url': repo['html_url'],
                'language': repo.get('language'),
                'updated_at': repo['updated_at'],
                'stargazers_count': repo.get('stargazers_count', 0),
            }
            for repo in repos
        ]

    async def list_added_repos(self) -> list[Repository]:
        """List repositories added by the user for indexing."""
        result = await self.db.execute(
            select(Repository).where(Repository.owner_id == self.user.id)
        )
        return list(result.scalars().all())

    async def get_repo(self, repo_id: str) -> Repository | None:
        """
        Get repository by ID.

        Accepts either a UUID string or a GitHub ID (integer string).
        """
        # Try to parse as UUID first
        try:
            UUID(repo_id)
            is_uuid = True
        except ValueError:
            is_uuid = False

        if is_uuid:
            result = await self.db.execute(
                select(Repository).where(
                    Repository.id == repo_id,
                    Repository.owner_id == self.user.id,
                )
            )
            return result.scalar_one_or_none()

        # Not a UUID, try as GitHub ID
        try:
            github_id = int(repo_id)
            result = await self.db.execute(
                select(Repository).where(
                    Repository.github_id == github_id,
                    Repository.owner_id == self.user.id,
                )
            )
            return result.scalar_one_or_none()
        except ValueError:
            # Not a valid UUID or integer
            return None

    async def get_repo_by_github_id(self, github_id: int) -> Repository | None:
        """Get repository by GitHub ID."""
        result = await self.db.execute(
            select(Repository).where(Repository.github_id == github_id)
        )
        return result.scalar_one_or_none()

    async def add_repo(self, github_id: int) -> Repository:
        """
        Add a repository for indexing.

        Automatically registers a webhook with GitHub to receive PR events
        if WEBHOOK_BASE_URL is configured.

        Args:
            github_id: GitHub repository ID

        Returns:
            Created Repository model

        Raises:
            ValueError: If repo already added or not accessible
        """
        # Check if already added
        existing = await self.get_repo_by_github_id(github_id)
        if existing:
            raise ValueError(f'Repository already added: {existing.full_name}')

        # Fetch repo details from GitHub
        try:
            github_repo = await self.github.get_repo_by_id(github_id)
        except Exception as e:
            logger.error(f'Failed to fetch repo {github_id}: {e}')
            raise ValueError('Repository not found or not accessible')

        # Create repository record
        repo = Repository(
            github_id=github_id,
            owner_id=self.user.id,
            full_name=github_repo['full_name'],
            name=github_repo['name'],
            clone_url=github_repo['clone_url'],
            default_branch=github_repo.get('default_branch', 'main'),
            index_status=IndexStatus.PENDING.value,
            webhook_secret=secrets.token_urlsafe(32),
        )

        self.db.add(repo)
        await self.db.commit()
        await self.db.refresh(repo)

        logger.info(f'Repository added: {repo.full_name}')

        # Auto-register webhook if WEBHOOK_BASE_URL is configured
        if settings.webhook_url:
            try:
                await self._register_pr_webhook(repo)
            except Exception as e:
                # Log but don't fail - webhook can be installed manually later
                logger.warning(
                    f'Failed to auto-register webhook for {repo.full_name}: {e}. '
                    'Webhook can be installed manually via API.'
                )

        return repo

    async def _register_pr_webhook(self, repo: Repository) -> int:
        """
        Register webhook for PR events only.

        Args:
            repo: Repository to register webhook for

        Returns:
            Webhook ID from GitHub
        """
        owner, name = repo.full_name.split('/')

        webhook = await self.github.create_webhook(
            owner=owner,
            repo=name,
            webhook_url=settings.webhook_url,
            secret=repo.webhook_secret,
            events=['pull_request'],  # Only PR events, not push
        )

        repo.webhook_id = webhook['id']
        await self.db.commit()

        logger.info(
            f'Webhook registered for {repo.full_name}: '
            f'ID={webhook["id"]}, URL={settings.webhook_url}'
        )
        return webhook['id']

    async def remove_repo(self, repo_id: str) -> None:
        """
        Remove a repository.

        Also removes local clone and webhook.
        """
        repo = await self.get_repo(repo_id)
        if not repo:
            raise ValueError('Repository not found')

        # Remove webhook if installed
        if repo.webhook_id:
            try:
                owner, name = repo.full_name.split('/')
                await self.github.delete_webhook(owner, name, repo.webhook_id)
            except Exception as e:
                logger.warning(f'Failed to delete webhook: {e}')

        # Remove local clone
        local_path = self._get_local_path(repo)
        if local_path.exists():
            shutil.rmtree(local_path)
            logger.info(f'Removed local clone: {local_path}')

        # Delete from database
        await self.db.delete(repo)
        await self.db.commit()

        logger.info(f'Repository removed: {repo.full_name}')

    def _get_local_path(self, repo: Repository) -> Path:
        """Get local storage path for repository."""
        return Path(settings.repo_storage_path) / str(repo.id)

    async def clone_repo(self, repo: Repository) -> Path:
        """
        Clone repository to local storage.

        Uses user's access token for authentication.

        Returns:
            Path to cloned repository
        """
        local_path = self._get_local_path(repo)

        # Remove existing clone if present
        if local_path.exists():
            shutil.rmtree(local_path)

        # Create parent directory
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # Build authenticated clone URL
        # https://x-access-token:{token}@github.com/owner/repo.git
        clone_url = repo.clone_url.replace(
            'https://',
            f'https://x-access-token:{self.user.github_access_token}@',
        )

        logger.info(f'Cloning {repo.full_name} to {local_path}')

        try:
            GitRepo.clone_from(
                clone_url,
                local_path,
                branch=repo.default_branch,
                depth=1,  # Shallow clone for faster indexing
            )
            logger.info(f'Clone complete: {repo.full_name}')
            return local_path

        except Exception as e:
            logger.error(f'Clone failed for {repo.full_name}: {e}')
            raise

    async def install_webhook(
        self,
        repo: Repository,
        webhook_url: str | None = None,
    ) -> int:
        """
        Install webhook on repository for PR events.

        Args:
            repo: Repository model
            webhook_url: URL to receive webhook events (defaults to settings.webhook_url)

        Returns:
            Webhook ID
        """
        webhook_url = webhook_url or settings.webhook_url
        if not webhook_url:
            raise ValueError(
                'No webhook URL provided. Set WEBHOOK_BASE_URL in environment.'
            )

        owner, name = repo.full_name.split('/')

        try:
            webhook = await self.github.create_webhook(
                owner=owner,
                repo=name,
                webhook_url=webhook_url,
                secret=repo.webhook_secret,
                events=['pull_request'],  # PR events only
            )

            repo.webhook_id = webhook['id']
            await self.db.commit()

            logger.info(f'Webhook installed on {repo.full_name}: {webhook["id"]}')
            return webhook['id']

        except Exception as e:
            logger.error(f'Failed to install webhook on {repo.full_name}: {e}')
            raise

    async def update_index_status(
        self,
        repo: Repository,
        status: IndexStatus,
        commit_sha: str | None = None,
    ) -> None:
        """Update repository index status."""
        repo.index_status = status.value

        if status == IndexStatus.INDEXED:
            repo.is_indexed = True
            repo.last_indexed_at = datetime.utcnow()
            if commit_sha:
                repo.last_indexed_commit = commit_sha

        elif status == IndexStatus.FAILED:
            repo.is_indexed = False

        await self.db.commit()
        logger.info(f'Index status updated: {repo.full_name} -> {status.value}')
