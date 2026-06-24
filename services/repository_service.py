"""
Repository service - Manage repositories for indexing.

Provider-agnostic service that works with any Git provider (GitHub, GitLab, etc.)
through the GitProvider abstraction.
"""

import secrets
import shutil
from datetime import datetime
from pathlib import Path
from uuid import UUID

from git import Repo as GitRepo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging_config import get_logger
from infra.config import settings
from models.repository import IndexStatus, Repository
from models.user import User
from services.providers.base import GitProvider, GitProviderType, RepoInfo

logger = get_logger(__name__)


class RepositoryService:
    """
    Service for managing repositories.

    Provider-agnostic - works with any GitProvider implementation.

    Handles:
    - Listing repos from provider
    - Adding repos for indexing
    - Cloning repos locally
    - Managing webhooks
    - Tracking index status
    """

    def __init__(
        self,
        db: AsyncSession,
        user: User,
        provider: GitProvider | None = None,
    ):
        """
        Initialize repository service.

        Args:
            db: Database session
            user: Authenticated user
            provider: GitProvider instance (optional, set via set_provider)
        """
        self.db = db
        self.user = user
        self._provider = provider

    def set_provider(self, provider: GitProvider) -> None:
        """Set the git provider for operations."""
        self._provider = provider

    @property
    def provider(self) -> GitProvider:
        """Get current provider, raise if not set."""
        if not self._provider:
            raise ValueError('GitProvider not set. Call set_provider() first.')
        return self._provider

    async def list_provider_repos(
        self,
        page: int = 1,
        per_page: int = 30,
    ) -> list[dict]:
        """
        List user's repositories from the current provider.

        Returns repos from provider API, not from our database.
        """
        repos = await self.provider.list_user_repos(page=page, per_page=per_page)

        return [self._normalize_repo_response(repo) for repo in repos]

    def _normalize_repo_response(self, repo: RepoInfo) -> dict:
        """Normalize RepoInfo to API response format."""
        return {
            'provider_id': repo['provider_id'],
            'full_name': repo['full_name'],
            'name': repo['name'],
            'description': repo.get('description'),
            'private': repo['private'],
            'default_branch': repo.get('default_branch', 'main'),
            'clone_url': repo['clone_url'],
            'html_url': repo['html_url'],
        }

    async def list_added_repos(
        self,
        provider_type: GitProviderType | None = None,
    ) -> list[Repository]:
        """
        List repositories added by the user for indexing.

        Args:
            provider_type: Filter by provider (optional)
        """
        query = select(Repository).where(Repository.owner_id == self.user.id)

        if provider_type:
            query = query.where(Repository.provider == provider_type.value)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_repo(self, repo_id: str) -> Repository | None:
        """
        Get repository by ID.

        Accepts either a UUID string or provider_repo_id (integer string).
        """
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

        try:
            provider_repo_id = int(repo_id)
            result = await self.db.execute(
                select(Repository).where(
                    Repository.provider_repo_id == provider_repo_id,
                    Repository.owner_id == self.user.id,
                )
            )
            return result.scalar_one_or_none()
        except ValueError:
            return None

    async def get_repo_by_provider_id(
        self,
        provider_type: GitProviderType,
        provider_repo_id: int,
    ) -> Repository | None:
        """Get repository by provider type and provider-specific ID."""
        result = await self.db.execute(
            select(Repository).where(
                Repository.provider == provider_type.value,
                Repository.provider_repo_id == provider_repo_id,
            )
        )
        return result.scalar_one_or_none()

    async def add_repo(self, provider_repo_id: int) -> Repository:
        """
        Add a repository for indexing.

        Automatically registers a webhook if WEBHOOK_BASE_URL is configured.

        Args:
            provider_repo_id: Provider-specific repository ID

        Returns:
            Created Repository model

        Raises:
            ValueError: If repo already added or not accessible
        """
        provider_type = self.provider.provider_type

        existing = await self.get_repo_by_provider_id(provider_type, provider_repo_id)
        if existing:
            raise ValueError(f'Repository already added: {existing.full_name}')

        try:
            repo_info = await self.provider.get_repo_by_id(provider_repo_id)
        except Exception as e:
            logger.error(f'Failed to fetch repo {provider_repo_id}: {e}')
            raise ValueError('Repository not found or not accessible')

        repo = Repository(
            provider=provider_type.value,
            provider_repo_id=provider_repo_id,
            github_id=provider_repo_id if provider_type == GitProviderType.GITHUB else None,
            owner_id=self.user.id,
            full_name=repo_info['full_name'],
            name=repo_info['name'],
            clone_url=repo_info['clone_url'],
            default_branch=repo_info.get('default_branch', 'main'),
            index_status=IndexStatus.PENDING.value,
            webhook_secret=secrets.token_urlsafe(32),
        )

        self.db.add(repo)
        await self.db.commit()
        await self.db.refresh(repo)

        logger.info(f'Repository added: {repo.full_name} ({provider_type.value})')

        if settings.webhook_url:
            try:
                await self._register_webhook(repo)
            except Exception as e:
                logger.warning(
                    f'Failed to auto-register webhook for {repo.full_name}: {e}. '
                    'Webhook can be installed manually via API.'
                )

        return repo

    async def _register_webhook(self, repo: Repository) -> int:
        """
        Register webhook for PR/MR events.

        Args:
            repo: Repository to register webhook for

        Returns:
            Webhook ID from provider
        """
        webhook = await self.provider.create_webhook(
            repo_identifier=repo.provider_repo_id,
            webhook_url=settings.webhook_url,
            secret=repo.webhook_secret,
            events=['pull_request'],
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

        if repo.webhook_id:
            try:
                await self.provider.delete_webhook(
                    repo_identifier=repo.provider_repo_id,
                    webhook_id=repo.webhook_id,
                )
            except Exception as e:
                logger.warning(f'Failed to delete webhook: {e}')

        local_path = self._get_local_path(repo)
        if local_path.exists():
            shutil.rmtree(local_path)
            logger.info(f'Removed local clone: {local_path}')

        await self.db.delete(repo)
        await self.db.commit()

        logger.info(f'Repository removed: {repo.full_name}')

    def _get_local_path(self, repo: Repository) -> Path:
        """Get local storage path for repository."""
        return Path(settings.repo_storage_path) / str(repo.id)

    def _get_access_token_for_clone(self, repo: Repository) -> str:
        """Get the appropriate access token for cloning based on repo provider."""
        provider_type = repo.provider
        token = self.user.get_access_token(provider_type)
        if not token:
            raise ValueError(f'User not connected to {provider_type}')
        return token

    async def clone_repo(self, repo: Repository) -> Path:
        """
        Clone repository to local storage.

        Uses user's access token for authentication.

        Returns:
            Path to cloned repository
        """
        local_path = self._get_local_path(repo)

        if local_path.exists():
            shutil.rmtree(local_path)

        local_path.parent.mkdir(parents=True, exist_ok=True)

        access_token = self._get_access_token_for_clone(repo)

        if repo.provider == GitProviderType.GITHUB.value:
            clone_url = repo.clone_url.replace(
                'https://',
                f'https://x-access-token:{access_token}@',
            )
        elif repo.provider == GitProviderType.GITLAB.value:
            clone_url = repo.clone_url.replace(
                'https://',
                f'https://oauth2:{access_token}@',
            )
        else:
            clone_url = repo.clone_url.replace(
                'https://',
                f'https://oauth2:{access_token}@',
            )

        logger.info(f'Cloning {repo.full_name} to {local_path}')

        try:
            GitRepo.clone_from(
                clone_url,
                local_path,
                branch=repo.default_branch,
                depth=1,
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
        Install webhook on repository for PR/MR events.

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

        try:
            webhook = await self.provider.create_webhook(
                repo_identifier=repo.provider_repo_id,
                webhook_url=webhook_url,
                secret=repo.webhook_secret,
                events=['pull_request'],
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
