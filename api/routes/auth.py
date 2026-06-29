"""
Authentication routes - Local credentials & Git provider OAuth linking flow.
"""

from datetime import datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt, JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from infra.database import get_db
from infra.redis_client import redis_client
from infra.config import settings
from services.auth_service import auth_service
from api.middleware.auth import get_current_user, security
from api.responses import success_response
from models.user import User
from core.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=['Authentication'])


# ─────────────────────────────────────────────────────────────
# Request Schemas
# ─────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    """Request schema for user registration."""
    username: str = Field(..., min_length=3, max_length=150, description="Unique username")
    email: str = Field(..., min_length=3, max_length=254, description="Unique email address")
    password: str = Field(..., min_length=8, description="Password (min 8 characters)")


class LoginRequest(BaseModel):
    """Request schema for user login."""
    username_or_email: str = Field(..., description="Username or email address")
    password: str = Field(..., description="Password")


class RefreshTokenRequest(BaseModel):
    """Request schema for access token refresh."""
    refresh_token: str = Field(..., description="Valid JWT Refresh Token")


# ─────────────────────────────────────────────────────────────
# Local Authentication Endpoints
# ─────────────────────────────────────────────────────────────

@router.post('/register')
async def register(
    request: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Register a new user account.
    
    Creates a new user in the database with User role (authority='2').
    """
    if '@' not in request.email or '.' not in request.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email format",
        )
    
    try:
        user = await auth_service.register_user(
            db=db,
            username=request.username,
            email=request.email,
            password=request.password,
        )
        logger.info(f'New user registered: {user.username}')
        return success_response(user.to_dict(), message="User registered successfully")
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post('/login')
async def login(
    request: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticate user and return access and refresh tokens.
    """
    user = await auth_service.authenticate_user(
        db=db,
        username_or_email=request.username_or_email,
        password=request.password,
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username/email or password",
        )
    
    # Generate Dual Tokens
    access_token = auth_service.create_access_token(user)
    refresh_token = auth_service.create_refresh_token(user)
    
    user.last_login_at = datetime.utcnow()
    await db.commit()
    
    logger.info(f'User logged in: {user.username}')
    return success_response({
        'access_token': access_token,
        'refresh_token': refresh_token,
        'token_type': 'bearer',
        'user': user.to_dict(),
    })


@router.post('/refresh-token')
async def refresh_token(
    request: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Rotate expired Access Token using a valid Refresh Token.
    """
    try:
        # 1. Decode and check signature
        payload = jwt.decode(
            request.refresh_token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        
        # 2. Check token type
        token_type = payload.get('type')
        if token_type != 'refresh':
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type",
            )
            
        user_id = payload.get('sub')
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
            )
            
        # 3. Check blacklist
        if await auth_service.is_token_blacklisted(db, request.refresh_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token has been revoked",
            )
            
        # 4. Fetch user
        from sqlalchemy.orm import selectinload
        result = await db.execute(
            select(User)
            .options(selectinload(User.role))
            .where(User.id == user_id, User.is_active == True)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or inactive",
            )
            
        # 5. Check last_logout validation
        iat = payload.get('iat')
        if user.last_logout and iat:
            if iat <= user.last_logout.timestamp():
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Refresh token was issued before the last logout",
                )
                
        # 6. Revoke the used Refresh Token (Token Rotation Blacklisting)
        exp_ts = payload.get('exp')
        exp_dt = datetime.utcfromtimestamp(exp_ts) if exp_ts else datetime.utcnow() + timedelta(days=7)
        await auth_service.blacklist_token(db, request.refresh_token, user.id, exp_dt)
        
        # 7. Generate new token pair
        new_access_token = auth_service.create_access_token(user)
        new_refresh_token = auth_service.create_refresh_token(user)
        
        return success_response({
            'access_token': new_access_token,
            'refresh_token': new_refresh_token,
            'token_type': 'bearer',
        })
        
    except JWTError as e:
        logger.debug(f"Refresh token decoding failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )


@router.post('/logout')
async def logout(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Logout current user.
    
    Revokes the current access token and sets last_logout to invalidate other sessions.
    """
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        exp_ts = payload.get('exp')
        exp_dt = datetime.utcfromtimestamp(exp_ts) if exp_ts else datetime.utcnow() + timedelta(minutes=30)
        
        # Blacklist the current access token
        await auth_service.blacklist_token(db, token, current_user.id, exp_dt)
    except JWTError:
        pass
        
    # Update last_logout timestamp to invalidate all tokens issued before this moment
    current_user.last_logout = datetime.utcnow()
    await db.commit()
    
    logger.info(f'User logged out: {current_user.username}')
    return success_response(None, message='Successfully logged out')


# ─────────────────────────────────────────────────────────────
# User Profile Route
# ─────────────────────────────────────────────────────────────

@router.get('/me')
async def get_me(
    current_user: User = Depends(get_current_user),
):
    """
    Get current authenticated user info.
    """
    return success_response(current_user.to_dict())


# ─────────────────────────────────────────────────────────────
# Git Provider Link Endpoints
# ─────────────────────────────────────────────────────────────

@router.get('/link/github')
async def github_link_init(
    current_user: User = Depends(get_current_user),
):
    """
    Initiate GitHub account linking flow.
    
    Generates OAuth redirection URL and binds state parameter to user in Redis.
    """
    auth_url, state = auth_service.get_github_auth_url()
    
    # Store OAuth state mapping to the current user ID
    await redis_client.set('oauth', 'state', state, value=current_user.id, ttl_seconds=600)
    
    logger.info(f'Generated GitHub OAuth link for user {current_user.username}')
    return success_response({'redirect_url': auth_url})


@router.get('/github/callback')
async def github_callback(
    code: str = Query(..., description='Authorization code from GitHub'),
    state: str = Query(..., description='CSRF state parameter'),
    db: AsyncSession = Depends(get_db),
):
    """
    Handle GitHub OAuth callback to link profile.
    """
    frontend_callback = f'{settings.frontend_url}/settings/providers'
    
    # Verify and retrieve current user_id from state
    user_id = await redis_client.get('oauth', 'state', state)
    if not user_id:
        logger.warning('Invalid or expired OAuth state for GitHub linking')
        redirect_url = f'{frontend_callback}?{urlencode({"status": "failed", "error": "invalid_state"})}'
        return RedirectResponse(url=redirect_url)
        
    # Delete consumed state
    await redis_client.delete('oauth', 'state', state)
    
    try:
        user = await auth_service.link_github_account(user_id, code, db)
        logger.info(f'GitHub account linked successfully to user: {user.username}')
        redirect_url = f'{frontend_callback}?{urlencode({"status": "success", "provider": "github"})}'
        return RedirectResponse(url=redirect_url)
    except ValueError as e:
        logger.error(f'GitHub linking failed: {e}')
        redirect_url = f'{frontend_callback}?{urlencode({"status": "failed", "error": str(e)})}'
        return RedirectResponse(url=redirect_url)
    except Exception as e:
        logger.error(f'Unexpected error during GitHub callback: {e}', exc_info=True)
        redirect_url = f'{frontend_callback}?{urlencode({"status": "failed", "error": "unexpected_error"})}'
        return RedirectResponse(url=redirect_url)


@router.get('/link/gitlab')
async def gitlab_link_init(
    current_user: User = Depends(get_current_user),
):
    """
    Initiate GitLab account linking flow.
    
    Generates OAuth redirection URL and binds state parameter to user in Redis.
    """
    auth_url, state = auth_service.get_gitlab_auth_url()
    
    # Store OAuth state mapping to the current user ID
    await redis_client.set('oauth', 'state', state, value=current_user.id, ttl_seconds=600)
    
    logger.info(f'Generated GitLab OAuth link for user {current_user.username}')
    return success_response({'redirect_url': auth_url})


@router.get('/gitlab/callback')
async def gitlab_callback(
    code: str = Query(..., description='Authorization code from GitLab'),
    state: str = Query(..., description='CSRF state parameter'),
    db: AsyncSession = Depends(get_db),
):
    """
    Handle GitLab OAuth callback to link profile.
    """
    frontend_callback = f'{settings.frontend_url}/settings/providers'
    
    # Verify and retrieve current user_id from state
    user_id = await redis_client.get('oauth', 'state', state)
    if not user_id:
        logger.warning('Invalid or expired OAuth state for GitLab linking')
        redirect_url = f'{frontend_callback}?{urlencode({"status": "failed", "error": "invalid_state"})}'
        return RedirectResponse(url=redirect_url)
        
    # Delete consumed state
    await redis_client.delete('oauth', 'state', state)
    
    try:
        user = await auth_service.link_gitlab_account(user_id, code, db)
        logger.info(f'GitLab account linked successfully to user: {user.username}')
        redirect_url = f'{frontend_callback}?{urlencode({"status": "success", "provider": "gitlab"})}'
        return RedirectResponse(url=redirect_url)
    except ValueError as e:
        logger.error(f'GitLab linking failed: {e}')
        redirect_url = f'{frontend_callback}?{urlencode({"status": "failed", "error": str(e)})}'
        return RedirectResponse(url=redirect_url)
    except Exception as e:
        logger.error(f'Unexpected error during GitLab callback: {e}', exc_info=True)
        redirect_url = f'{frontend_callback}?{urlencode({"status": "failed", "error": "unexpected_error"})}'
        return RedirectResponse(url=redirect_url)
