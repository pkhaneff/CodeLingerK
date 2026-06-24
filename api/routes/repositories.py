"""
Repository routes - Manage repositories for indexing.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from pathlib import Path

from infra.database import get_db
from infra.config import settings
from api.middleware.auth import get_current_user
from models.user import User
from models.repository import IndexStatus
from services.repository_service import RepositoryService
from services.index_service import IndexService
from services.github_service import GitHubService
from core.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=['Repositories'])


# Request/Response models
class AddRepoRequest(BaseModel):
    """Request to add a repository."""
    github_id: int


class RepoResponse(BaseModel):
    """Repository response model."""
    id: str
    github_id: int
    full_name: str
    name: str
    default_branch: str
    is_indexed: bool
    index_status: str
    last_indexed_at: str | None
    last_indexed_commit: str | None
    webhook_installed: bool
    created_at: str

    class Config:
        from_attributes = True


class GitHubRepoResponse(BaseModel):
    """GitHub repository from API."""
    github_id: int
    full_name: str
    name: str
    description: str | None
    private: bool
    default_branch: str
    clone_url: str
    html_url: str
    language: str | None
    updated_at: str
    stargazers_count: int


# Helper to create service
def get_repo_service(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RepositoryService:
    """Dependency to get repository service."""
    return RepositoryService(db, user)


# Routes
@router.get('/github', response_model=list[GitHubRepoResponse])
async def list_github_repos(
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
    service: RepositoryService = Depends(get_repo_service),
):
    """
    List user's GitHub repositories.

    Returns repositories from GitHub API that can be added for indexing.
    """
    repos = await service.list_github_repos(page=page, per_page=per_page)
    return repos


@router.get('', response_model=list[RepoResponse])
async def list_repos(
    service: RepositoryService = Depends(get_repo_service),
):
    """
    List repositories added for indexing.

    Returns repositories that the user has added to CodeLingerK.
    """
    repos = await service.list_added_repos()
    return [
        RepoResponse(
            id=repo.id,
            github_id=repo.github_id,
            full_name=repo.full_name,
            name=repo.name,
            default_branch=repo.default_branch,
            is_indexed=repo.is_indexed,
            index_status=repo.index_status,
            last_indexed_at=repo.last_indexed_at.isoformat() if repo.last_indexed_at else None,
            last_indexed_commit=repo.last_indexed_commit,
            webhook_installed=repo.webhook_id is not None,
            created_at=repo.created_at.isoformat(),
        )
        for repo in repos
    ]


@router.post('', response_model=RepoResponse)
async def add_repo(
    request: AddRepoRequest,
    service: RepositoryService = Depends(get_repo_service),
):
    """
    Add a repository for indexing.

    The repository will be cloned and prepared for code analysis.
    """
    try:
        repo = await service.add_repo(request.github_id)
        return RepoResponse(
            id=repo.id,
            github_id=repo.github_id,
            full_name=repo.full_name,
            name=repo.name,
            default_branch=repo.default_branch,
            is_indexed=repo.is_indexed,
            index_status=repo.index_status,
            last_indexed_at=None,
            last_indexed_commit=None,
            webhook_installed=False,
            created_at=repo.created_at.isoformat(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get('/{repo_id}', response_model=RepoResponse)
async def get_repo(
    repo_id: str,
    service: RepositoryService = Depends(get_repo_service),
):
    """Get repository details."""
    repo = await service.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail='Repository not found')

    return RepoResponse(
        id=repo.id,
        github_id=repo.github_id,
        full_name=repo.full_name,
        name=repo.name,
        default_branch=repo.default_branch,
        is_indexed=repo.is_indexed,
        index_status=repo.index_status,
        last_indexed_at=repo.last_indexed_at.isoformat() if repo.last_indexed_at else None,
        last_indexed_commit=repo.last_indexed_commit,
        webhook_installed=repo.webhook_id is not None,
        created_at=repo.created_at.isoformat(),
    )


@router.delete('/{repo_id}')
async def remove_repo(
    repo_id: str,
    service: RepositoryService = Depends(get_repo_service),
):
    """
    Remove a repository.

    This will delete the repository from CodeLingerK, remove the webhook,
    and delete any local clones.
    """
    try:
        await service.remove_repo(repo_id)
        return {'success': True, 'message': 'Repository removed'}
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
        return {
            'success': True,
            'message': f'Repository cloned to {path}',
            'local_path': str(path),
        }
    except Exception as e:
        logger.error(f'Clone failed: {e}')
        raise HTTPException(status_code=500, detail=f'Clone failed: {e}')


@router.post('/{repo_id}/webhook')
async def install_webhook(
    repo_id: str,
    service: RepositoryService = Depends(get_repo_service),
):
    """
    Install webhook on repository for PR events.

    The webhook will notify CodeLingerK when pull requests are created/updated.
    Requires WEBHOOK_BASE_URL to be configured in environment.

    If the webhook_id exists in database but not on GitHub (e.g., manually deleted),
    it will be cleared and a new webhook will be installed.
    """
    repo = await service.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail='Repository not found')

    # If webhook_id exists, verify it actually exists on GitHub
    if repo.webhook_id:
        owner, name = repo.full_name.split('/')
        github = GitHubService(service.user.github_access_token)
        existing_webhook = await github.get_webhook(owner, name, repo.webhook_id)

        if existing_webhook:
            # Webhook exists on GitHub, return error
            raise HTTPException(status_code=400, detail='Webhook already installed')

        # Webhook doesn't exist on GitHub, clear the stale webhook_id
        logger.warning(
            f'Webhook {repo.webhook_id} not found on GitHub for {repo.full_name}. '
            'Clearing stale webhook_id and reinstalling.'
        )
        repo.webhook_id = None
        await service.db.commit()

    # Use configured webhook URL
    if not settings.webhook_url:
        raise HTTPException(
            status_code=400,
            detail='WEBHOOK_BASE_URL not configured. Set it in .env file.',
        )

    try:
        webhook_id = await service.install_webhook(repo)
        return {
            'success': True,
            'message': 'Webhook installed',
            'webhook_id': webhook_id,
            'webhook_url': settings.webhook_url,
        }
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

    # Check if repository is cloned
    local_path = Path(settings.repo_storage_path) / str(repo.id)
    if not local_path.exists():
        raise HTTPException(
            status_code=400,
            detail='Repository not cloned. Call /clone first.',
        )

    try:
        index_service = IndexService(db, repo)
        stats = await index_service.full_index()

        return {
            'success': True,
            'message': f'Indexed {stats["files_processed"]} files',
            'stats': stats,
        }

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

    return {
        'status': repo.index_status,
        'is_indexed': repo.is_indexed,
        'last_indexed_at': repo.last_indexed_at.isoformat() if repo.last_indexed_at else None,
        'last_indexed_commit': repo.last_indexed_commit,
    }
