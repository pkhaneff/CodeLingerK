"""
Authentication middleware - JWT token validation.
"""

from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from infra.database import get_db
from apps.auth.services.auth_service import auth_service
from apps.auth.models.user import User
from core.exceptions import UnauthorizedException, ForbiddenException, ErrorCode

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
        UnauthorizedException: If token is missing or invalid
    """
    token = credentials.credentials

    user = await auth_service.get_current_user(token, db)

    if not user:
        raise UnauthorizedException(
            error_code=ErrorCode.INVALID_TOKEN,
            message='Invalid or expired token'
        )

    return user


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_security),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """
    Dependency to optionally get current user.

    Returns None if no token provided, user if valid token.
    """
    if not credentials:
        return None

    return await auth_service.get_current_user(credentials.credentials, db)


class AuthorityChecker:
    """FastAPI Dependency for Role-Based Access Control (RBAC)."""

    def __init__(self, allowed_authorities: list[str]):
        self.allowed_authorities = allowed_authorities

    def __call__(self, user: User = Depends(get_current_user)) -> User:
        if not user.role or user.role.authority not in self.allowed_authorities:
            raise ForbiddenException(
                error_code=ErrorCode.UNAUTHORIZED,
                message='Forbidden: insufficient permissions'
            )
        return user


def require_authority(allowed_authorities: list[str]) -> AuthorityChecker:
    """
    FastAPI dependency to enforce user roles/authorities.
    """
    return AuthorityChecker(allowed_authorities)


