"""
Review routes - Access AI review results and pipeline status.

Provides endpoints to:
- List PRs for a repository
- Get PR with snapshots
- Get snapshot details and review
- Retry failed processing
- View queue statistics
"""

import asyncio
import json
import redis
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from infra.redis_client import redis_client

from apps.auth.api.middleware import get_current_user
from core.responses import success_response
from core.logger import get_logger
from infra.database import get_db
from apps.ai_reviewer.models.layer import Layer
from apps.ai_reviewer.models.pull_request import PullRequest
from apps.repositories.models.repository import Repository
from apps.ai_reviewer.models.review import Review, ReviewComment
from apps.ai_reviewer.models.review_job import JobType, ReviewJob
from apps.ai_reviewer.models.snapshot import Snapshot
from apps.auth.models.user import User
from apps.ai_reviewer.integrations.queue_service import QueueService

logger = get_logger(__name__)

router = APIRouter(tags=['Reviews'])


# ─────────────────────────────────────────────────────────────
# Request/Response Schemas
# ─────────────────────────────────────────────────────────────


class PullRequestResponse(BaseModel):
    """Pull request response model."""

    id: str
    pr_number: int
    title: str
    status: str
    source_branch: str
    target_branch: str
    author: str | None
    html_url: str | None
    snapshot_count: int = 0
    latest_snapshot_id: str | None = None
    created_at: str

    class Config:
        from_attributes = True


class SnapshotResponse(BaseModel):
    """Snapshot response model."""

    id: str
    commit_sha: str
    status: str
    files_changed: list[str]
    additions: int
    deletions: int
    has_review: bool = False
    review_verdict: str | None = None
    created_at: str
    processed_at: str | None = None

    class Config:
        from_attributes = True


class LayerResponse(BaseModel):
    """Layer response model."""

    id: str
    layer_type: str
    label: str
    intent: str | None
    files_count: int
    risk_score: int
    review_order: int

    class Config:
        from_attributes = True


class CommentResponse(BaseModel):
    """Review comment response model."""

    id: str
    file_path: str
    line_start: int | None
    line_end: int | None
    severity: str
    category: str | None
    comment: str
    suggestion: str | None
    confidence: float | None
    is_synced: bool

    class Config:
        from_attributes = True


class ReviewResponse(BaseModel):
    """Full review response model."""

    id: str
    status: str
    verdict: str | None
    summary: str | None
    diagram_mermaid: str | None = None
    ai_model: str | None
    ai_tokens_used: int | None
    processing_time_ms: int | None
    comments: list[CommentResponse] = []
    created_at: str
    completed_at: str | None = None

    class Config:
        from_attributes = True


class SnapshotDetailResponse(BaseModel):
    """Detailed snapshot with layers and review."""

    snapshot: SnapshotResponse
    layers: list[LayerResponse] = []
    review: ReviewResponse | None = None


class QueueStatsResponse(BaseModel):
    """Queue statistics response."""

    queues: dict[str, int]
    processing: int
    dead_letter: int


class RetryResponse(BaseModel):
    """Retry operation response."""

    success: bool
    job_id: str | None = None
    message: str


# ─────────────────────────────────────────────────────────────
# Pull Request Endpoints
# ─────────────────────────────────────────────────────────────


@router.get('/repos/{repo_id}/pull-requests')
async def list_pull_requests(
    repo_id: str,
    status: str | None = Query(None, description='Filter by status'),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List pull requests for a repository.

    Returns PRs with snapshot counts, ordered by update time.
    """
    # Verify user owns the repository
    repo_result = await db.execute(
        select(Repository).where(
            Repository.id == repo_id,
            Repository.owner_id == current_user.id,
        )
    )
    if not repo_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail='Repository not found')

    # Build query
    query = (
        select(PullRequest)
        .where(PullRequest.repository_id == repo_id)
        .order_by(PullRequest.updated_at.desc())
        .offset(offset)
        .limit(limit)
    )

    if status:
        query = query.where(PullRequest.status == status)

    result = await db.execute(query)
    prs = result.scalars().all()

    # Get snapshot counts
    responses = []
    for pr in prs:
        count_result = await db.execute(
            select(func.count(Snapshot.id))
            .where(Snapshot.pull_request_id == pr.id)
        )
        snapshot_count = count_result.scalar() or 0

        # Get latest snapshot
        latest_result = await db.execute(
            select(Snapshot.id)
            .where(Snapshot.pull_request_id == pr.id)
            .order_by(Snapshot.created_at.desc())
            .limit(1)
        )
        latest_snapshot_id = latest_result.scalar_one_or_none()

        responses.append(PullRequestResponse(
            id=pr.id,
            pr_number=pr.pr_number,
            title=pr.title,
            status=pr.status,
            source_branch=pr.source_branch,
            target_branch=pr.target_branch,
            author=pr.author,
            html_url=pr.html_url,
            snapshot_count=snapshot_count,
            latest_snapshot_id=latest_snapshot_id,
            created_at=pr.created_at.isoformat() if pr.created_at else '',
        ))

    return success_response(responses)


@router.get('/repos/{repo_id}/pull-requests/{pr_number}')
async def get_pull_request(
    repo_id: str,
    pr_number: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get pull request details with snapshots."""
    # Verify ownership and get PR and Repository
    result = await db.execute(
        select(PullRequest, Repository)
        .join(Repository)
        .where(
            PullRequest.repository_id == repo_id,
            PullRequest.pr_number == pr_number,
            Repository.owner_id == current_user.id,
        )
    )
    db_data = result.one_or_none()

    if not db_data:
        raise HTTPException(status_code=404, detail='Pull request not found')

    pr, repository = db_data

    # Fetch latest PR info from provider to sync status on-demand
    try:
        from apps.repositories.services.providers.base import GitProviderType
        from apps.repositories.services.providers.factory import GitProviderFactory

        access_token = current_user.get_access_token(repository.provider)
        if access_token:
            git_provider = GitProviderFactory.create(
                GitProviderType(repository.provider), access_token
            )
            pr_info = await git_provider.get_pr(
                repository.full_name,
                pr_number,
            )
            latest_status = pr_info.get('state')
            if latest_status and pr.status != latest_status:
                pr.status = latest_status
                await db.commit()
                logger.info(f"Synced PR #{pr_number} status to '{latest_status}' on-demand")
    except Exception as e:
        logger.warning(f"Failed to sync PR #{pr_number} status with git provider: {e}")

    # Get snapshot count
    count_result = await db.execute(
        select(func.count(Snapshot.id))
        .where(Snapshot.pull_request_id == pr.id)
    )
    snapshot_count = count_result.scalar() or 0

    # Get latest snapshot
    latest_result = await db.execute(
        select(Snapshot.id)
        .where(Snapshot.pull_request_id == pr.id)
        .order_by(Snapshot.created_at.desc())
        .limit(1)
    )
    latest_snapshot_id = latest_result.scalar_one_or_none()

    return success_response(PullRequestResponse(
        id=pr.id,
        pr_number=pr.pr_number,
        title=pr.title,
        status=pr.status,
        source_branch=pr.source_branch,
        target_branch=pr.target_branch,
        author=pr.author,
        html_url=pr.html_url,
        snapshot_count=snapshot_count,
        latest_snapshot_id=latest_snapshot_id,
        created_at=pr.created_at.isoformat() if pr.created_at else '',
    ))


# ─────────────────────────────────────────────────────────────
# Snapshot Endpoints
# ─────────────────────────────────────────────────────────────


@router.get('/snapshots/{snapshot_id}')
async def get_snapshot(
    snapshot_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get snapshot details with layers and review."""
    # Get snapshot with related data
    result = await db.execute(
        select(Snapshot)
        .options(
            selectinload(Snapshot.pull_request).selectinload(PullRequest.repository),
            selectinload(Snapshot.layers),
            selectinload(Snapshot.review).selectinload(Review.comments),
        )
        .where(Snapshot.id == snapshot_id)
    )
    snapshot = result.scalar_one_or_none()

    if not snapshot:
        raise HTTPException(status_code=404, detail='Snapshot not found')

    # Verify ownership
    if snapshot.pull_request.repository.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail='Access denied')

    # Build response
    snapshot_resp = SnapshotResponse(
        id=snapshot.id,
        commit_sha=snapshot.commit_sha,
        status=snapshot.status,
        files_changed=snapshot.files_changed or [],
        additions=snapshot.additions or 0,
        deletions=snapshot.deletions or 0,
        has_review=snapshot.review is not None,
        review_verdict=snapshot.review.verdict if snapshot.review else None,
        created_at=snapshot.created_at.isoformat() if snapshot.created_at else '',
        processed_at=snapshot.processed_at.isoformat() if snapshot.processed_at else None,
    )

    layers_resp = [
        LayerResponse(
            id=layer.id,
            layer_type=layer.layer_type,
            label=layer.label,
            intent=layer.intent,
            files_count=layer.files_count,
            risk_score=layer.risk_score,
            review_order=layer.review_order,
        )
        for layer in sorted(snapshot.layers, key=lambda l: l.review_order)
    ]

    review_resp = None
    if snapshot.review:
        review = snapshot.review
        comments_resp = [
            CommentResponse(
                id=c.id,
                file_path=c.file_path,
                line_start=c.line_start,
                line_end=c.line_end,
                severity=c.severity,
                category=c.category,
                comment=c.comment,
                suggestion=c.suggestion,
                confidence=c.confidence,
                is_synced=c.is_synced,
            )
            for c in review.comments
        ]

        # Fallback: if diagram_mermaid is NULL in DB, try to extract it from ai_passes on the fly
        diagram_mermaid = review.diagram_mermaid
        if not diagram_mermaid and review.ai_passes:
            for k, v in review.ai_passes.items():
                if 'understanding' in k and isinstance(v, dict):
                    diag = v.get('sequence_diagram')
                    if isinstance(diag, dict) and diag.get('include') and diag.get('mermaid'):
                        mermaid_text = str(diag.get('mermaid', '')).strip()
                        if 'sequenceDiagram' in mermaid_text:
                            diagram_mermaid = mermaid_text
                    break

        review_resp = ReviewResponse(
            id=review.id,
            status=review.status,
            verdict=review.verdict,
            summary=review.summary,
            diagram_mermaid=diagram_mermaid,
            ai_model=review.ai_model,
            ai_tokens_used=review.ai_tokens_used,
            processing_time_ms=review.processing_time_ms,
            comments=comments_resp,
            created_at=review.created_at.isoformat() if review.created_at else '',
            completed_at=review.completed_at.isoformat() if review.completed_at else None,
        )

    return success_response(SnapshotDetailResponse(
        snapshot=snapshot_resp,
        layers=layers_resp,
        review=review_resp,
    ))


@router.get('/snapshots/{snapshot_id}/events')
async def get_snapshot_events(
    snapshot_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Stream snapshot status updates using Server-Sent Events (SSE).
    """
    # Verify snapshot exists and user owns the repository
    result = await db.execute(
        select(Snapshot)
        .options(
            selectinload(Snapshot.pull_request).selectinload(PullRequest.repository)
        )
        .where(Snapshot.id == snapshot_id)
    )
    snapshot = result.scalar_one_or_none()

    if not snapshot:
        raise HTTPException(status_code=404, detail='Snapshot not found')

    if snapshot.pull_request.repository.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail='Access denied')

    async def event_generator():
        channel = f"codelingerk:v1:snapshot:{snapshot_id}:events"
        
        # Subscribe to Redis pubsub
        pubsub = redis_client._client.pubsub()
        await pubsub.subscribe(channel)
        
        try:
            # Yield the current status immediately
            initial_payload = {
                "snapshot_id": snapshot_id,
                "status": snapshot.status,
                "error_message": snapshot.error_message,
            }
            yield f"data: {json.dumps(initial_payload)}\n\n"
            
            # If the snapshot is already in a terminal state, terminate the stream
            if snapshot.status in ("completed", "failed"):
                return

            # Listen for updates
            async for message in pubsub.listen():
                if message['type'] == 'message':
                    data = message['data']
                    yield f"data: {data}\n\n"
                    
                    try:
                        payload = json.loads(data)
                        if payload.get("status") in ("completed", "failed"):
                            break
                    except Exception:
                        pass
        except (redis.exceptions.TimeoutError, redis.exceptions.ConnectionError) as e:
            logger.error(f"Redis connection timeout/error in SSE event stream for snapshot {snapshot_id}: {e}")
            err_payload = {
                "snapshot_id": snapshot_id,
                "status": "failed",
                "error_message": "Redis connection lost or timed out",
            }
            yield f"data: {json.dumps(err_payload)}\n\n"
        except asyncio.CancelledError:
            logger.info(f"SSE client disconnected for snapshot {snapshot_id}")
            raise
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream",
        }
    )


@router.get('/snapshots/{snapshot_id}/logs')
async def get_snapshot_logs(
    snapshot_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Stream snapshot execution logs in real-time using Server-Sent Events (SSE).
    """
    # Verify snapshot exists and user owns the repository
    result = await db.execute(
        select(Snapshot)
        .options(
            selectinload(Snapshot.pull_request).selectinload(PullRequest.repository)
        )
        .where(Snapshot.id == snapshot_id)
    )
    snapshot = result.scalar_one_or_none()

    if not snapshot:
        raise HTTPException(status_code=404, detail='Snapshot not found')

    if snapshot.pull_request.repository.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail='Access denied')

    async def log_generator():
        channel = f"codelingerk:v1:snapshot:{snapshot_id}:logs"
        
        # Subscribe to Redis pubsub
        pubsub = redis_client._client.pubsub()
        await pubsub.subscribe(channel)
        
        try:
            # Yield initial connection message
            initial_payload = {
                "timestamp": None,
                "level": "INFO",
                "message": "Connected to log stream...",
            }
            yield f"data: {json.dumps(initial_payload)}\n\n"

            # If the snapshot is already in a terminal state, terminate immediately
            if snapshot.status in ("completed", "failed"):
                eof_payload = {
                    "timestamp": None,
                    "level": "INFO",
                    "message": "[EOF]",
                }
                yield f"data: {json.dumps(eof_payload)}\n\n"
                return

            # Listen for updates
            async for message in pubsub.listen():
                if message['type'] == 'message':
                    data = message['data']
                    yield f"data: {data}\n\n"
                    
                    try:
                        payload = json.loads(data)
                        if "[EOF]" in payload.get("message", ""):
                            break
                    except Exception:
                        pass
        except (redis.exceptions.TimeoutError, redis.exceptions.ConnectionError) as e:
            logger.error(f"Redis connection timeout/error in SSE log stream for snapshot {snapshot_id}: {e}")
            err_payload = {
                "timestamp": None,
                "level": "ERROR",
                "message": "Redis connection lost or timed out",
            }
            yield f"data: {json.dumps(err_payload)}\n\n"
        except asyncio.CancelledError:
            logger.info(f"SSE log client disconnected for snapshot {snapshot_id}")
            raise
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return StreamingResponse(
        log_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream",
        }
    )



@router.post('/snapshots/{snapshot_id}/retry')
async def retry_snapshot(
    snapshot_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retry failed snapshot processing."""
    # Get snapshot and verify ownership
    result = await db.execute(
        select(Snapshot)
        .options(selectinload(Snapshot.pull_request).selectinload(PullRequest.repository))
        .where(Snapshot.id == snapshot_id)
    )
    snapshot = result.scalar_one_or_none()

    if not snapshot:
        raise HTTPException(status_code=404, detail='Snapshot not found')

    if snapshot.pull_request.repository.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail='Access denied')

    # Only allow retry for failed snapshots
    if snapshot.status != 'failed':
        raise HTTPException(
            status_code=400,
            detail=f'Cannot retry snapshot with status: {snapshot.status}'
        )

    # Reset status and enqueue
    snapshot.status = 'pending'
    snapshot.error_message = None
    await db.commit()

    queue_service = QueueService(db)
    job_id = await queue_service.enqueue(
        job_type=JobType.CONTEXT,
        snapshot_id=snapshot_id,
        priority=90,  # High priority for retry
    )

    return success_response(
        {'job_id': job_id},
        message='Snapshot queued for reprocessing',
    )


# ─────────────────────────────────────────────────────────────
# Queue Endpoints
# ─────────────────────────────────────────────────────────────


@router.get('/queue/stats')
async def get_queue_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get queue statistics."""
    queue_service = QueueService(db)
    stats = await queue_service.get_queue_stats()

    return success_response(QueueStatsResponse(
        queues=stats.get('queues', {}),
        processing=stats.get('processing', 0),
        dead_letter=stats.get('dead_letter', 0),
    ))


@router.get('/jobs')
async def list_jobs(
    snapshot_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List review jobs with optional filters."""
    query = select(ReviewJob).order_by(ReviewJob.created_at.desc()).limit(limit)

    if snapshot_id:
        query = query.where(ReviewJob.snapshot_id == snapshot_id)
    if status:
        query = query.where(ReviewJob.status == status)

    result = await db.execute(query)
    jobs = result.scalars().all()

    return success_response([job.to_dict() for job in jobs])
