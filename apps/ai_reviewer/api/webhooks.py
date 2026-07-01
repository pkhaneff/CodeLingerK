"""
Webhook routes - Handle PR/MR events and queue for AI review pipeline.

Supports both GitHub and GitLab providers.
Only triggers on PR/MR events (opened, reopened, synchronize/update).
Push events are acknowledged but not processed.

Pipeline flow:
    Webhook → Snapshot → Queue(CONTEXT) → Worker processes pipeline
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.logging_config import get_logger
from infra.database import get_db
from apps.repositories.models.repository import Repository
from apps.ai_reviewer.models.review_job import JobType
from apps.auth.models.user import User
from apps.repositories.services.providers.base import GitProviderType
from apps.repositories.services.providers.factory import GitProviderFactory
from apps.ai_reviewer.services.snapshot_service import SnapshotService
from apps.ai_reviewer.services.queue_service import QueueService
from apps.ai_reviewer.api.webhooks_parser import (
    NormalizedWebhookPayload,
    WebhookPayloadParser,
)
from apps.ai_reviewer.api.models import GitHubPushPayload

logger = get_logger(__name__)

router = APIRouter(tags=['Webhooks'])


# ─────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────


async def get_repository_with_owner_by_provider(
    db: AsyncSession,
    provider: GitProviderType,
    full_name: str,
) -> tuple[Repository, User] | None:
    """
    Look up repository and its owner by provider and full_name.

    Args:
        db: Database session
        provider: Git provider type (github/gitlab)
        full_name: Repository full name (owner/repo or namespace/project)

    Returns:
        Tuple of (Repository, User) or None if not found
    """
    stmt = (
        select(Repository)
        .options(selectinload(Repository.owner))
        .where(
            Repository.provider == provider.value,
            Repository.full_name == full_name,
        )
    )
    result = await db.execute(stmt)
    repo = result.scalar_one_or_none()

    if repo and repo.owner:
        return repo, repo.owner

    return None


async def process_pr_webhook(
    db: AsyncSession,
    provider: GitProviderType,
    payload: NormalizedWebhookPayload,
) -> dict:
    """
    Unified PR/MR webhook processing with queue-based pipeline.

    Creates an immutable snapshot of the PR state and queues it for
    AI review processing through the pipeline:
        CONTEXT → LAYER → REVIEW → PUBLISH

    Args:
        db: Database session
        provider: Git provider type
        payload: Normalized webhook payload

    Returns:
        Response dictionary with snapshot_id and job_id for tracking
    """
    # Check if we should process this event
    if not payload.should_process:
        return {
            'status': 'skipped',
            'reason': payload.skip_reason,
            'provider': provider.value,
        }

    # Look up repository and owner
    repo_data = await get_repository_with_owner_by_provider(
        db,
        provider,
        payload.repo_full_name,
    )

    if not repo_data:
        logger.warning(
            f'Repository not found: {payload.repo_full_name} (provider: {provider.value})'
        )
        return {
            'status': 'skipped',
            'reason': 'Repository not registered in CodeLingerK',
            'provider': provider.value,
        }

    repo, owner = repo_data

    # Get access token for the provider
    access_token = owner.get_access_token(provider.value)
    if not access_token:
        username = owner.gitlab_username or owner.github_username
        logger.warning(f'No {provider.value} access token for user: {username}')
        return {
            'status': 'error',
            'reason': f'{provider.value} access token not available',
            'provider': provider.value,
        }

    # Create provider instance (Dependency Injection)
    git_provider = GitProviderFactory.create(provider, access_token)

    # ─────────────────────────────────────────────────────────────
    # Phase 1: Create Snapshot (Immutable PR State)
    # ─────────────────────────────────────────────────────────────
    snapshot_service = SnapshotService(db, git_provider)

    # Create snapshot (idempotent - returns existing if same commit)
    snapshot, created = await snapshot_service.create_snapshot(
        repository=repo,
        pr_number=payload.pr_number,
        commit_sha=payload.head_sha,
        source_branch=payload.source_branch,
        target_branch=payload.target_branch,
        title=payload.title,
        author=payload.author,
    )

    logger.info(
        f'Snapshot created: {snapshot.id[:8]} for PR #{payload.pr_number} '
        f'@ {payload.head_sha[:8]} (newly created: {created})'
    )

    from apps.ai_reviewer.models.snapshot import SnapshotStatus

    if not created and snapshot.status not in (SnapshotStatus.FAILED.value,):
        logger.info(
            f'Snapshot {snapshot.id[:8]} already exists with status {snapshot.status}. '
            f'Skipping queueing duplicate pipeline.'
        )
        return {
            'status': 'skipped',
            'reason': f'Snapshot already exists with status: {snapshot.status}',
            'provider': provider.value,
            'pr_number': payload.pr_number,
            'commit_sha': payload.head_sha,
            'snapshot_id': str(snapshot.id),
        }

    # If the snapshot previously failed and we are reprocessing it, reset its status to pending
    if not created and snapshot.status == SnapshotStatus.FAILED.value:
        logger.info(f'Retrying failed snapshot {snapshot.id[:8]} from webhook event')
        snapshot.status = SnapshotStatus.PENDING.value
        snapshot.error_message = None
        await db.flush()

    # ─────────────────────────────────────────────────────────────
    # Phase 2: Enqueue for Processing
    # ─────────────────────────────────────────────────────────────
    queue_service = QueueService(db)

    # Calculate priority based on PR size
    priority = snapshot_service.calculate_priority(snapshot)

    # Enqueue the first job in pipeline (CONTEXT)
    job_id = await queue_service.enqueue(
        job_type=JobType.CONTEXT,
        snapshot_id=str(snapshot.id),
        priority=priority,
        metadata={
            'provider': provider.value,
            'pr_number': payload.pr_number,
            'repo_full_name': repo.full_name,
            'owner_id': str(owner.id),
        },
    )

    # Commit the transaction
    await db.commit()

    logger.info(
        f'Queued job {job_id[:8]} for snapshot {snapshot.id[:8]} '
        f'with priority {priority}'
    )

    return {
        'status': 'queued',
        'provider': provider.value,
        'pr_number': payload.pr_number,
        'commit_sha': payload.head_sha,
        'snapshot_id': str(snapshot.id),
        'job_id': job_id,
        'priority': priority,
        'files_changed': snapshot.files_count,
        'total_changes': snapshot.total_changes,
        'message': 'PR queued for AI review pipeline',
    }


# ─────────────────────────────────────────────────────────────
# GitHub Webhook Routes
# ─────────────────────────────────────────────────────────────


@router.post('/github/push')
async def github_push(payload: GitHubPushPayload):
    """
    Handle GitHub push webhook event.

    Push events are acknowledged but NOT processed.
    Review comments are only triggered by PR events.
    """
    logger.info(
        f'Push event received: {payload.repository.full_name}, '
        f'commits: {len(payload.commits)} - SKIPPED (PR-only mode)'
    )

    return {
        'status': 'skipped',
        'reason': 'Push events are not processed. Reviews only trigger on PR creation.',
        'provider': 'github',
        'repository': payload.repository.full_name,
        'commits': len(payload.commits),
    }


@router.post('/github/pull_request')
async def github_pr(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Handle GitHub pull request webhook event.

    For each file changed in the PR, posts an inline review comment
    directly on the file diff in the GitHub PR interface.

    Only processes: opened, reopened, synchronize actions.
    Also handles GitHub ping events (sent when webhook is created).
    """
    # Check for ping event (sent when webhook is first created)
    event_type = request.headers.get('X-GitHub-Event')
    if event_type == 'ping':
        logger.info('GitHub ping event received - webhook verified')
        return {'status': 'pong', 'message': 'Webhook configured successfully'}

    # Parse raw payload
    raw_payload = await request.json()

    try:
        # Use unified parser to normalize payload
        payload = WebhookPayloadParser.parse(GitProviderType.GITHUB, raw_payload)
    except Exception as e:
        logger.warning(f'Invalid GitHub PR payload: {e}')
        return {
            'status': 'skipped',
            'reason': f'Not a valid pull_request event: {e}',
            'provider': 'github',
        }

    logger.info(f'GitHub PR event: {payload.action} - PR #{payload.pr_number}')

    try:
        return await process_pr_webhook(db, GitProviderType.GITHUB, payload)
    except Exception as e:
        logger.error(f'Error processing GitHub PR: {e}', exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/github')
async def github_generic(request: Request):
    """
    Handle generic GitHub webhook events.

    Routes to specific handlers or acknowledges unknown events.
    """
    event_type = request.headers.get('X-GitHub-Event')

    if not event_type:
        raise HTTPException(status_code=400, detail='Missing X-GitHub-Event header')

    logger.info(f'GitHub event: {event_type}')

    if event_type == 'ping':
        return {'status': 'pong', 'provider': 'github'}

    return {
        'status': 'received',
        'provider': 'github',
        'event_type': event_type,
        'message': f"Event '{event_type}' acknowledged",
    }


# ─────────────────────────────────────────────────────────────
# GitLab Webhook Routes
# ─────────────────────────────────────────────────────────────


@router.post('/gitlab/merge_request')
async def gitlab_mr(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Handle GitLab merge request webhook event.

    For each file changed in the MR, posts an inline review comment
    directly on the file diff in the GitLab MR interface.

    Only processes: open, reopen, update actions/states.
    """
    # Parse raw payload
    raw_payload = await request.json()

    # Check event type
    object_kind = raw_payload.get('object_kind')
    if object_kind != 'merge_request':
        logger.info(f'GitLab event type: {object_kind} - SKIPPED (MR-only mode)')
        return {
            'status': 'skipped',
            'reason': f"Event type '{object_kind}' not processed",
            'provider': 'gitlab',
        }

    try:
        # Use unified parser to normalize payload
        payload = WebhookPayloadParser.parse(GitProviderType.GITLAB, raw_payload)
    except Exception as e:
        logger.warning(f'Invalid GitLab MR payload: {e}')
        return {
            'status': 'skipped',
            'reason': f'Not a valid merge_request event: {e}',
            'provider': 'gitlab',
        }

    logger.info(f'GitLab MR event: {payload.action} - MR !{payload.pr_number}')

    try:
        return await process_pr_webhook(db, GitProviderType.GITLAB, payload)
    except Exception as e:
        logger.error(f'Error processing GitLab MR: {e}', exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/gitlab')
async def gitlab_generic(request: Request):
    """
    Handle generic GitLab webhook events.

    Routes to specific handlers or acknowledges unknown events.
    """
    raw_payload = await request.json()
    event_type = raw_payload.get('object_kind', 'unknown')

    logger.info(f'GitLab event: {event_type}')

    return {
        'status': 'received',
        'provider': 'gitlab',
        'event_type': event_type,
        'message': f"Event '{event_type}' acknowledged",
    }
