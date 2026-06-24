"""
Webhook routes - Handle PR/MR events and post inline review comments.

Supports both GitHub and GitLab providers.
Only triggers on PR/MR events (opened, reopened, synchronize/update).
Push events are acknowledged but not processed.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.logging_config import get_logger
from infra.database import get_db
from models.repository import Repository
from models.user import User
from services.providers.base import GitProviderType
from services.providers.factory import GitProviderFactory
from api.webhooks import (
    NormalizedWebhookPayload,
    WebhookPayloadParser,
)
from api.models import GitHubPushPayload

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


def build_review_comments(files: list[dict]) -> list[dict]:
    """
    Build inline review comments for changed files.

    Args:
        files: List of file change info from provider API

    Returns:
        List of comment dictionaries ready for posting
    """
    comments = []

    for file_info in files:
        filename = file_info.get('filename', '')
        status = file_info.get('status', '')
        patch = file_info.get('patch', '')

        # Skip deleted files (no lines to comment on)
        if status == 'removed':
            logger.debug(f'Skipping deleted file: {filename}')
            continue

        # Find the first added/modified line to place the comment
        # Default to line 1 if we can't parse the patch
        comment_line = 1

        if patch:
            # Parse patch to find a valid line number
            # Look for the first @@ hunk header
            for line in patch.split('\n'):
                if line.startswith('@@'):
                    # Format: @@ -old_start,old_count +new_start,new_count @@
                    try:
                        parts = line.split('+')[1].split(' ')[0]
                        new_start = int(parts.split(',')[0])
                        comment_line = new_start
                        break
                    except (IndexError, ValueError):
                        pass

        comments.append({
            'path': filename,
            'body': f'hello đây là file {filename}',
            'line': comment_line,
            'side': 'RIGHT',  # Comment on new file version
        })

        logger.debug(f'Prepared comment for {filename} at line {comment_line}')

    return comments


async def process_pr_webhook(
    db: AsyncSession,
    provider: GitProviderType,
    payload: NormalizedWebhookPayload,
) -> dict:
    """
    Unified PR/MR webhook processing.

    Works with any provider through the GitProvider abstraction.

    Args:
        db: Database session
        provider: Git provider type
        payload: Normalized webhook payload

    Returns:
        Response dictionary with processing result
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

    # Get list of files changed in the PR/MR
    pr_files = await git_provider.get_pr_files(
        repo_identifier=repo.full_name,
        pr_number=payload.pr_number,
    )

    logger.info(
        f'{provider.value} PR #{payload.pr_number}: Found {len(pr_files)} changed files'
    )

    if not pr_files:
        return {
            'status': 'success',
            'message': 'No files changed in PR/MR',
            'provider': provider.value,
            'pr_number': payload.pr_number,
        }

    # Build inline comments
    comments = build_review_comments(pr_files)

    # Post review with all inline comments
    if comments:
        review = await git_provider.post_pr_review(
            repo_identifier=repo.full_name,
            pr_number=payload.pr_number,
            commit_sha=payload.head_sha,
            body=f'🔍 CodeLingerK đã review {len(comments)} file(s) trong PR/MR này.',
            comments=comments,
        )

        logger.info(
            f'Posted review with {len(comments)} inline comments '
            f'on {provider.value} PR #{payload.pr_number}'
        )

        return {
            'status': 'success',
            'provider': provider.value,
            'pr_number': payload.pr_number,
            'files_reviewed': len(comments),
            'review_id': review.get('id'),
            'message': f'Posted {len(comments)} inline comments',
        }

    return {
        'status': 'success',
        'provider': provider.value,
        'pr_number': payload.pr_number,
        'files_reviewed': 0,
        'message': 'No files to comment on',
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
