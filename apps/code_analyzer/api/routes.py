"""
Graph routes - Query code graph from PostgreSQL.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database import get_db
from apps.auth.api.middleware import get_current_user
from core.responses import success_response
from apps.auth.models.user import User
from apps.repositories.models.repository import Repository
from apps.code_analyzer.services.graph_service import GraphService
from apps.repositories.services.repository_service import RepositoryService
from core.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=['Code Graph'])


# Response models
class FileResponse(BaseModel):
    path: str
    language: str
    hash: str


class SymbolResponse(BaseModel):
    name: str
    type: str
    file: str | None = None
    line_start: int
    signature: str | None = None
    docstring: str | None = None


class CallerResponse(BaseModel):
    caller: str
    file: str
    line: int
    type: str


class DependencyResponse(BaseModel):
    module: str
    imported: str


class IndexStatsResponse(BaseModel):
    files: int
    symbols: int
    classes: int
    functions: int
    methods: int


# Helper to get repository and verify ownership
async def get_user_repo(
    repo_id: str,
    db: AsyncSession,
    user: User,
) -> Repository:
    """Get repository and verify user ownership."""
    service = RepositoryService(db, user)
    repo = await service.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail='Repository not found')
    return repo


# Routes
@router.get('/{repo_id}/graph/files')
async def list_files(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all indexed files in the repository."""
    repo = await get_user_repo(repo_id, db, user)

    if not repo.is_indexed:
        raise HTTPException(status_code=400, detail='Repository not indexed yet')

    graph = GraphService(db, str(repo.id))
    files = await graph.get_files()

    return success_response([FileResponse(**f) for f in files])


@router.get('/{repo_id}/graph/symbols')
async def list_symbols(
    repo_id: str,
    file: str | None = Query(None, description='Filter by file path'),
    q: str | None = Query(None, description='Search query'),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    List symbols in the repository.

    Optionally filter by file or search by name.
    """
    repo = await get_user_repo(repo_id, db, user)

    if not repo.is_indexed:
        raise HTTPException(status_code=400, detail='Repository not indexed yet')

    graph = GraphService(db, str(repo.id))

    if q:
        symbols = await graph.search_symbols(q)
    else:
        symbols = await graph.get_symbols(file)

    return success_response([SymbolResponse(**s) for s in symbols])


@router.get('/{repo_id}/graph/callers/{symbol_name}')
async def get_callers(
    repo_id: str,
    symbol_name: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get all functions/methods that call a given symbol."""
    repo = await get_user_repo(repo_id, db, user)

    if not repo.is_indexed:
        raise HTTPException(status_code=400, detail='Repository not indexed yet')

    graph = GraphService(db, str(repo.id))
    callers = await graph.get_callers(symbol_name)

    return success_response([CallerResponse(**c) for c in callers])


@router.get('/{repo_id}/graph/callees/{symbol_name}')
async def get_callees(
    repo_id: str,
    symbol_name: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get all functions/methods called by a given symbol."""
    repo = await get_user_repo(repo_id, db, user)

    if not repo.is_indexed:
        raise HTTPException(status_code=400, detail='Repository not indexed yet')

    graph = GraphService(db, str(repo.id))
    callees = await graph.get_callees(symbol_name)

    return success_response(callees)


@router.get('/{repo_id}/graph/dependencies/{file_path:path}')
async def get_dependencies(
    repo_id: str,
    file_path: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get imports/dependencies of a file."""
    repo = await get_user_repo(repo_id, db, user)

    if not repo.is_indexed:
        raise HTTPException(status_code=400, detail='Repository not indexed yet')

    graph = GraphService(db, str(repo.id))
    deps = await graph.get_dependencies(file_path)

    return success_response([DependencyResponse(**d) for d in deps])


@router.get('/{repo_id}/graph/hierarchy/{class_name}')
async def get_class_hierarchy(
    repo_id: str,
    class_name: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get inheritance hierarchy for a class."""
    repo = await get_user_repo(repo_id, db, user)

    if not repo.is_indexed:
        raise HTTPException(status_code=400, detail='Repository not indexed yet')

    graph = GraphService(db, str(repo.id))
    hierarchy = await graph.get_class_hierarchy(class_name)

    return success_response(hierarchy)


@router.get('/{repo_id}/graph/stats')
async def get_graph_stats(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get statistics about the indexed repository."""
    repo = await get_user_repo(repo_id, db, user)

    graph = GraphService(db, str(repo.id))
    stats = await graph.get_graph_stats()

    return success_response(IndexStatsResponse(**stats))


