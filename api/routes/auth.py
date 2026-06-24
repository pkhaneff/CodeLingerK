"""
Authentication routes - GitHub OAuth flow.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database import get_db
from infra.redis_client import redis_client
from services.auth_service import auth_service
from api.middleware.auth import get_current_user
from models.user import User
from core.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=['Authentication'])


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

    Exchanges authorization code for access token and creates/updates user.

    Returns:
        JSON with access_token and user info
    """
    # Verify state for CSRF protection
    if not await redis_client.verify_oauth_state(state):
        logger.warning('Invalid OAuth state - possible CSRF attack')
        raise HTTPException(status_code=400, detail='Invalid state parameter')

    try:
        user, access_token = await auth_service.handle_github_callback(code, db)

        logger.info(f'User authenticated: {user.github_username}')

        return {
            'success': True,
            'data': {
                'access_token': access_token,
                'token_type': 'bearer',
                'user': user.to_dict(),
            },
            'message': 'Successfully authenticated with GitHub',
        }

    except ValueError as e:
        logger.error(f'OAuth callback failed: {e}')
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f'Unexpected error in OAuth callback: {e}', exc_info=True)
        raise HTTPException(status_code=500, detail='Authentication failed')


@router.get('/me')
async def get_me(
    current_user: User = Depends(get_current_user),
):
    """
    Get current authenticated user info.

    Requires valid JWT token in Authorization header.
    """
    return {
        'success': True,
        'data': current_user.to_dict(),
    }


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

    return {
        'success': True,
        'message': 'Successfully logged out',
    }
