"""
Authentication middleware - JWT token validation.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database import get_db
from services.auth_service import auth_service
from models.user import User

# HTTP Bearer token scheme
security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Dependency to get current authenticated user.

    Validates JWT token from Authorization header and returns user.

    Raises:
        HTTPException 401: If token is missing or invalid

    Usage:
        @router.get("/protected")
        async def protected_route(user: User = Depends(get_current_user)):
            return {"user": user.github_username}
    """
    token = credentials.credentials

    user = await auth_service.get_current_user(token, db)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid or expired token',
            headers={'WWW-Authenticate': 'Bearer'},
        )

    return user


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_security),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """
    Dependency to optionally get current user.

    Returns None if no token provided, user if valid token.

    Usage:
        @router.get("/public")
        async def public_route(user: User | None = Depends(get_optional_user)):
            if user:
                return {"message": f"Hello, {user.github_username}"}
            return {"message": "Hello, anonymous"}
    """
    if not credentials:
        return None

    return await auth_service.get_current_user(credentials.credentials, db)
