"""
Repository routes - Manage repositories for indexing.

Provider-agnostic routes that work with any Git provider (GitHub, GitLab, etc.)
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from apps.repositories.api.dependencies import create_provider_for_user, get_git_provider
from apps.auth.api.middleware import get_current_user
from core.responses import success_response
from core.logging_config import get_logger
from infra.config import settings
from infra.database import get_db
from apps.auth.models.user import User
from apps.code_analyzer.services.index_service import IndexService
from apps.repositories.services.providers.base import GitProvider, GitProviderType
from apps.repositories.services.repository_service import RepositoryService

logger = get_logger(__name__)

router = APIRouter(tags=['Repositories'])


# ─────────────────────────────────────────────────────────────
# Request/Response Schemas
# ─────────────────────────────────────────────────────────────


class AddRepoRequest(BaseModel):
    """Request to add a repository."""

    provider_repo_id: int


class RepoResponse(BaseModel):
    """Repository response model."""

    id: str
    provider: str
    provider_repo_id: int
    full_name: str
    name: str
    default_branch: str
    is_indexed: bool
    index_status: str
    last_indexed_at: str | None
    last_indexed_commit: str | None
    webhook_installed: bool
    is_active: bool
    created_at: str

    class Config:
        from_attributes = True


class ProviderRepoResponse(BaseModel):
    """Repository from provider API (not yet added)."""

    provider_id: int
    full_name: str
    name: str
    description: str | None
    private: bool
    default_branch: str
    clone_url: str
    html_url: str


# ─────────────────────────────────────────────────────────────
# Dependencies
# ─────────────────────────────────────────────────────────────


def get_repo_service(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RepositoryService:
    """Dependency to get repository service (without provider)."""
    return RepositoryService(db, user)


# ─────────────────────────────────────────────────────────────
# Provider-specific Routes
# ─────────────────────────────────────────────────────────────


@router.get('/{provider}/repos')
async def list_provider_repos(
    provider: GitProviderType,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    git_provider: GitProvider = Depends(get_git_provider),
):
    """
    List user's repositories from a specific provider.

    Returns repositories from provider API (GitHub/GitLab) that can be added for indexing.

    - **provider**: Git provider (github or gitlab)
    - **page**: Page number (1-indexed)
    - **per_page**: Results per page (max 100)
    """
    service = RepositoryService(db, user, git_provider)
    repos = await service.list_provider_repos(page=page, per_page=per_page)
    return success_response(repos)


@router.post('/{provider}/repos')
async def add_repo(
    provider: GitProviderType,
    request: AddRepoRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    git_provider: GitProvider = Depends(get_git_provider),
):
    """
    Add a repository for indexing.

    The repository will be registered for code analysis.

    - **provider**: Git provider (github or gitlab)
    - **provider_repo_id**: Provider-specific repository ID
    """
    service = RepositoryService(db, user, git_provider)
    try:
        repo = await service.add_repo(request.provider_repo_id)
        return success_response(_repo_to_response(repo))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ─────────────────────────────────────────────────────────────
# Repository Management Routes (provider-agnostic)
# ─────────────────────────────────────────────────────────────


@router.get('')
async def list_repos(
    provider: GitProviderType | None = Query(None, description='Filter by provider'),
    service: RepositoryService = Depends(get_repo_service),
):
    """
    List repositories added for indexing.

    Returns repositories that the user has added to CodeLingerK.

    - **provider**: Filter by provider (optional)
    """
    repos = await service.list_added_repos(provider_type=provider)
    return success_response([_repo_to_response(repo) for repo in repos])


@router.get('/{repo_id}')
async def get_repo(
    repo_id: str,
    service: RepositoryService = Depends(get_repo_service),
):
    """Get repository details."""
    repo = await service.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail='Repository not found')

    return success_response(_repo_to_response(repo))


@router.delete('/{repo_id}')
async def remove_repo(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Remove a repository.

    This will delete the repository from CodeLingerK, remove the webhook,
    and delete any local clones.
    """
    service = RepositoryService(db, user)
    repo = await service.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail='Repository not found')

    provider_type = GitProviderType(repo.provider)
    git_provider = create_provider_for_user(user, provider_type)
    service.set_provider(git_provider)

    try:
        await service.remove_repo(repo_id)
        return success_response(None, message='Repository removed')
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post('/{repo_id}/clone')
async def clone_repo(
    repo_id: str,
    service: RepositoryService = Depends(get_repo_service),
):
    """
    Clone repository to local storage.

    This is usually done automatically during indexing.
    """
    repo = await service.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail='Repository not found')

    try:
        path = await service.clone_repo(repo)
        return success_response(
            {'local_path': str(path)},
            message=f'Repository cloned to {path}',
        )
    except Exception as e:
        logger.error(f'Clone failed: {e}')
        raise HTTPException(status_code=500, detail=f'Clone failed: {e}')


@router.post('/{repo_id}/webhook')
async def install_webhook(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Install webhook on repository for PR/MR events.

    The webhook will notify CodeLingerK when pull requests are created/updated.
    Requires WEBHOOK_BASE_URL to be configured in environment.
    """
    service = RepositoryService(db, user)
    repo = await service.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail='Repository not found')

    provider_type = GitProviderType(repo.provider)
    git_provider = create_provider_for_user(user, provider_type)
    service.set_provider(git_provider)

    if repo.webhook_id:
        existing_webhook = await git_provider.get_webhook(
            repo_identifier=repo.provider_repo_id,
            webhook_id=repo.webhook_id,
        )

        if existing_webhook:
            raise HTTPException(status_code=400, detail='Webhook already installed')

        logger.warning(
            f'Webhook {repo.webhook_id} not found on provider for {repo.full_name}. '
            'Clearing stale webhook_id and reinstalling.'
        )
        repo.webhook_id = None
        await db.commit()

    if not settings.webhook_url:
        raise HTTPException(
            status_code=400,
            detail='WEBHOOK_BASE_URL not configured. Set it in .env file.',
        )

    try:
        webhook_id = await service.install_webhook(repo)
        return success_response(
            {'webhook_id': webhook_id, 'webhook_url': settings.webhook_url},
            message='Webhook installed',
        )
    except Exception as e:
        logger.error(f'Webhook install failed: {e}')
        raise HTTPException(status_code=500, detail=f'Failed to install webhook: {e}')


@router.post('/{repo_id}/index')
async def trigger_index(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Trigger full repository indexing.

    This will:
    1. Clear existing graph data
    2. Parse all Python files in the repository
    3. Store symbols, imports, and relationships in PostgreSQL
    """
    service = RepositoryService(db, user)
    repo = await service.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail='Repository not found')

    local_path = Path(settings.repo_storage_path) / str(repo.id)
    if not local_path.exists():
        raise HTTPException(
            status_code=400,
            detail='Repository not cloned. Call /clone first.',
        )

    try:
        index_service = IndexService(db, repo)
        stats = await index_service.full_index()

        return success_response(
            {'stats': stats},
            message=f'Indexed {stats["files_processed"]} files',
        )

    except Exception as e:
        logger.error(f'Indexing failed: {e}')
        raise HTTPException(status_code=500, detail=f'Indexing failed: {e}')


@router.get('/{repo_id}/index/status')
async def get_index_status(
    repo_id: str,
    service: RepositoryService = Depends(get_repo_service),
):
    """Get repository indexing status."""
    repo = await service.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail='Repository not found')

    return success_response({
        'status': repo.index_status,
        'is_indexed': repo.is_indexed,
        'last_indexed_at': repo.last_indexed_at.isoformat() if repo.last_indexed_at else None,
        'last_indexed_commit': repo.last_indexed_commit,
    })


# ─────────────────────────────────────────────────────────────
# Active Repository Management
# ─────────────────────────────────────────────────────────────


@router.post('/{repo_id}/activate')
async def activate_repo(
    repo_id: str,
    service: RepositoryService = Depends(get_repo_service),
):
    """
    Set a repository as active.

    Only one repository can be active per user at a time.
    Activating a repo will automatically deactivate any other active repo.

    Active repositories are the ones currently being worked on and will
    receive priority for AI code reviews.
    """
    try:
        repo = await service.activate_repo(repo_id)
        return success_response(
            _repo_to_response(repo),
            message=f'Repository {repo.full_name} is now active',
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post('/{repo_id}/deactivate')
async def deactivate_repo(
    repo_id: str,
    service: RepositoryService = Depends(get_repo_service),
):
    """
    Deactivate a repository.

    The repository will no longer be marked as active.
    """
    try:
        repo = await service.deactivate_repo(repo_id)
        return success_response(
            _repo_to_response(repo),
            message=f'Repository {repo.full_name} is now inactive',
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get('/active')
async def get_active_repo(
    service: RepositoryService = Depends(get_repo_service),
):
    """
    Get the currently active repository.

    Returns null if no repository is currently active.
    """
    repo = await service.get_active_repo()
    if not repo:
        return success_response(None, message='No active repository')

    return success_response(_repo_to_response(repo))


# ─────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────


def _repo_to_response(repo) -> RepoResponse:
    """Convert Repository model to response."""
    return RepoResponse(
        id=repo.id,
        provider=repo.provider,
        provider_repo_id=repo.provider_repo_id,
        full_name=repo.full_name,
        name=repo.name,
        default_branch=repo.default_branch,
        is_indexed=repo.is_indexed,
        index_status=repo.index_status,
        last_indexed_at=repo.last_indexed_at.isoformat() if repo.last_indexed_at else None,
        last_indexed_commit=repo.last_indexed_commit,
        webhook_installed=repo.webhook_id is not None,
        is_active=repo.is_active,
        created_at=repo.created_at.isoformat(),
    )
