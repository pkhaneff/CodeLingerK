"""
GitHub webhook routes - Handle PR events and post inline review comments.

Only triggers on PR events (opened, reopened, synchronize).
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
from services.github_service import GitHubService
from api.models import (
    GitHubPushPayload,
    GitHubPRPayload,
)

logger = get_logger(__name__)

router = APIRouter(tags=['Webhooks'])


async def get_repository_with_owner(
    db: AsyncSession,
    full_name: str,
) -> tuple[Repository, User] | None:
    """
    Look up repository and its owner by full_name.

    Args:
        db: Database session
        full_name: Repository full name (owner/repo)

    Returns:
        Tuple of (Repository, User) or None if not found
    """
    stmt = (
        select(Repository)
        .options(selectinload(Repository.owner))
        .where(Repository.full_name == full_name)
    )
    result = await db.execute(stmt)
    repo = result.scalar_one_or_none()

    if repo and repo.owner:
        return repo, repo.owner

    return None


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
        logger.info('Ping event received - webhook verified')
        return {'status': 'pong', 'message': 'Webhook configured successfully'}

    # Parse and validate PR payload
    raw_payload = await request.json()
    try:
        payload = GitHubPRPayload(**raw_payload)
    except Exception as e:
        logger.warning(f'Invalid PR payload: {e}')
        return {
            'status': 'skipped',
            'reason': f'Not a valid pull_request event: {e}',
        }

    logger.info(f'PR event: {payload.action} - PR #{payload.number}')

    if payload.action not in ['opened', 'reopened', 'synchronize']:
        return {
            'status': 'skipped',
            'reason': f"Action '{payload.action}' not processed",
        }

    try:
        # Look up repository and owner to get access token
        repo_data = await get_repository_with_owner(
            db,
            payload.repository.full_name,
        )

        if not repo_data:
            logger.warning(
                f'Repository not found in database: {payload.repository.full_name}'
            )
            return {
                'status': 'skipped',
                'reason': 'Repository not registered in CodeLingerK',
            }

        repo, owner = repo_data

        if not owner.github_access_token:
            logger.warning(f'No access token for user: {owner.github_username}')
            return {
                'status': 'error',
                'reason': 'Owner access token not available',
            }

        # Initialize GitHub service with owner's token
        github_service = GitHubService(owner.github_access_token)

        # Parse owner and repo name from full_name
        owner_name, repo_name = payload.repository.full_name.split('/')
        pr_number = payload.number
        head_sha = payload.pull_request.head['sha']

        # Get list of files changed in the PR
        pr_files = await github_service.get_pull_request_files(
            owner=owner_name,
            repo=repo_name,
            pr_number=pr_number,
        )

        logger.info(f'PR #{pr_number}: Found {len(pr_files)} changed files')

        if not pr_files:
            return {
                'status': 'success',
                'message': 'No files changed in PR',
                'pr_number': pr_number,
            }

        # Build inline comments for each file
        comments = []
        for file_info in pr_files:
            filename = file_info.get('filename', '')
            status = file_info.get('status', '')  # added, modified, removed
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

            logger.info(f'Prepared comment for {filename} at line {comment_line}')

        # Post review with all inline comments at once
        if comments:
            review = await github_service.create_pr_review_with_comments(
                owner=owner_name,
                repo=repo_name,
                pr_number=pr_number,
                commit_id=head_sha,
                body=f'🔍 CodeLingerK đã review {len(comments)} file(s) trong PR này.',
                event='COMMENT',
                comments=comments,
            )

            logger.info(
                f'Posted review with {len(comments)} inline comments on PR #{pr_number}'
            )

            return {
                'status': 'success',
                'pr_number': pr_number,
                'files_reviewed': len(comments),
                'review_id': review.get('id'),
                'message': f'Posted {len(comments)} inline comments',
            }

        return {
            'status': 'success',
            'pr_number': pr_number,
            'files_reviewed': 0,
            'message': 'No files to comment on',
        }

    except Exception as e:
        logger.error(f'Error processing PR: {e}', exc_info=True)
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
        return {'status': 'pong'}

    return {
        'status': 'received',
        'event_type': event_type,
        'message': f"Event '{event_type}' acknowledged",
    }
