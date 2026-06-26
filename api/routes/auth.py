"""
Authentication routes - Multi-provider OAuth flow.

Supports GitHub and GitLab OAuth.
"""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database import get_db
from infra.redis_client import redis_client
from infra.config import settings
from services.auth_service import auth_service
from api.middleware.auth import get_current_user
from api.responses import success_response
from models.user import User
from core.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=['Authentication'])


# ─────────────────────────────────────────────────────────────
# GitHub OAuth Routes
# ─────────────────────────────────────────────────────────────


@router.get('/github')
async def github_login():
    """
    Initiate GitHub OAuth flow.

    Redirects user to GitHub authorization page.
    """
    auth_url, state = auth_service.get_github_auth_url()

    # Store state in Redis for CSRF protection
    await redis_client.store_oauth_state(state)

    logger.info('Redirecting to GitHub OAuth')
    return RedirectResponse(url=auth_url)


@router.get('/github/callback')
async def github_callback(
    code: str = Query(..., description='Authorization code from GitHub'),
    state: str = Query(..., description='CSRF state parameter'),
    db: AsyncSession = Depends(get_db),
):
    """
    Handle GitHub OAuth callback.

    Exchanges authorization code for access token and redirects to frontend.
    """
    frontend_callback = f'{settings.frontend_url}/auth/callback'

    # Verify state for CSRF protection
    if not await redis_client.verify_oauth_state(state):
        logger.warning('Invalid OAuth state - possible CSRF attack')
        redirect_url = f'{frontend_callback}?{urlencode({"error": "invalid_state"})}'
        return RedirectResponse(url=redirect_url)

    try:
        user, access_token = await auth_service.handle_github_callback(code, db)

        logger.info(f'User authenticated: {user.github_username}')

        # Redirect to frontend with token
        redirect_url = f'{frontend_callback}?{urlencode({"token": access_token})}'
        return RedirectResponse(url=redirect_url)

    except ValueError as e:
        logger.error(f'OAuth callback failed: {e}')
        redirect_url = f'{frontend_callback}?{urlencode({"error": "auth_failed", "message": str(e)})}'
        return RedirectResponse(url=redirect_url)
    except Exception as e:
        logger.error(f'Unexpected error in OAuth callback: {e}', exc_info=True)
        redirect_url = f'{frontend_callback}?{urlencode({"error": "auth_failed"})}'
        return RedirectResponse(url=redirect_url)


# ─────────────────────────────────────────────────────────────
# GitLab OAuth Routes
# ─────────────────────────────────────────────────────────────


@router.get('/gitlab')
async def gitlab_login():
    """
    Initiate GitLab OAuth flow.

    Redirects user to GitLab authorization page.
    """
    auth_url, state = auth_service.get_gitlab_auth_url()

    # Store state in Redis for CSRF protection
    await redis_client.store_oauth_state(state)

    logger.info('Redirecting to GitLab OAuth')
    return RedirectResponse(url=auth_url)


@router.get('/gitlab/callback')
async def gitlab_callback(
    code: str = Query(..., description='Authorization code from GitLab'),
    state: str = Query(..., description='CSRF state parameter'),
    db: AsyncSession = Depends(get_db),
):
    """
    Handle GitLab OAuth callback.

    Exchanges authorization code for access token and redirects to frontend.
    """
    frontend_callback = f'{settings.frontend_url}/auth/callback'

    # Verify state for CSRF protection
    if not await redis_client.verify_oauth_state(state):
        logger.warning('Invalid OAuth state - possible CSRF attack')
        redirect_url = f'{frontend_callback}?{urlencode({"error": "invalid_state"})}'
        return RedirectResponse(url=redirect_url)

    try:
        user, access_token = await auth_service.handle_gitlab_callback(code, db)

        username = user.gitlab_username or user.github_username
        logger.info(f'GitLab user authenticated: {username}')

        # Redirect to frontend with token
        redirect_url = f'{frontend_callback}?{urlencode({"token": access_token})}'
        return RedirectResponse(url=redirect_url)

    except ValueError as e:
        logger.error(f'GitLab OAuth callback failed: {e}')
        redirect_url = f'{frontend_callback}?{urlencode({"error": "auth_failed", "message": str(e)})}'
        return RedirectResponse(url=redirect_url)
    except Exception as e:
        logger.error(f'Unexpected error in GitLab OAuth callback: {e}', exc_info=True)
        redirect_url = f'{frontend_callback}?{urlencode({"error": "auth_failed"})}'
        return RedirectResponse(url=redirect_url)


# ─────────────────────────────────────────────────────────────
# User Profile Routes
# ─────────────────────────────────────────────────────────────


@router.get('/me')
async def get_me(
    current_user: User = Depends(get_current_user),
):
    """
    Get current authenticated user info.

    Requires valid JWT token in Authorization header.
    """
    return success_response(current_user.to_dict())


@router.post('/logout')
async def logout(
    current_user: User = Depends(get_current_user),
):
    """
    Logout current user.

    Note: JWT tokens are stateless. This endpoint is mainly for
    client-side cleanup. In production, consider token blacklisting.
    """
    logger.info(f'User logged out: {current_user.github_username}')

    return success_response(None, message='Successfully logged out')
