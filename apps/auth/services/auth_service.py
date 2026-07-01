"""
Authentication service - Multi-provider OAuth and JWT session management.

Supports GitHub and GitLab OAuth flows.
"""

import secrets
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from jose import jwt, JWTError
import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infra.config import settings
from infra.redis_client import redis_client
from apps.auth.models.user import User
from core.logging_config import get_logger

logger = get_logger(__name__)

# GitHub OAuth URLs
GITHUB_AUTHORIZE_URL = 'https://github.com/login/oauth/authorize'
GITHUB_TOKEN_URL = 'https://github.com/login/oauth/access_token'
GITHUB_USER_URL = 'https://api.github.com/user'

# GitLab OAuth URLs (gitlab.com only)
GITLAB_AUTHORIZE_URL = 'https://gitlab.com/oauth/authorize'
GITLAB_TOKEN_URL = 'https://gitlab.com/oauth/token'
GITLAB_USER_URL = 'https://gitlab.com/api/v4/user'


class AuthService:
    """
    Authentication service for multi-provider OAuth and session management.

    Supported providers:
    - GitHub
    """

    def get_password_hash(self, password: str) -> str:
        """Hash password using bcrypt."""
        password_bytes = password.encode('utf-8')
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password_bytes, salt)
        return hashed.decode('utf-8')

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify plain password against hashed password."""
        try:
            return bcrypt.checkpw(
                plain_password.encode('utf-8'),
                hashed_password.encode('utf-8')
            )
        except Exception:
            return False

    async def register_user(
        self,
        db: AsyncSession,
        username: str,
        email: str,
        password: str,
    ) -> User:
        """Register a new user with local credentials."""
        # Check if username or email already exists
        result = await db.execute(
            select(User).where((User.username == username) | (User.email == email))
        )
        if result.scalar_one_or_none():
            raise ValueError("Username or email already exists")

        # Get default user role (authority '2')
        from apps.auth.models.role import Role
        role_result = await db.execute(
            select(Role).where(Role.authority == '2')
        )
        role = role_result.scalar_one_or_none()

        hashed_password = self.get_password_hash(password)
        user = User(
            username=username,
            email=email,
            hashed_password=hashed_password,
            role_id=role.id if role else None,
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        # Eager load role relationship for API response serialization
        from sqlalchemy.orm import selectinload
        result = await db.execute(
            select(User)
            .options(selectinload(User.role))
            .where(User.id == user.id)
        )
        return result.scalar_one()

    async def authenticate_user(
        self,
        db: AsyncSession,
        username_or_email: str,
        password: str,
    ) -> User | None:
        """Authenticate user with username/email and password."""
        from sqlalchemy.orm import selectinload
        result = await db.execute(
            select(User)
            .options(selectinload(User.role))
            .where(
                ((User.username == username_or_email) | (User.email == username_or_email))
                & (User.is_active == True)
            )
        )
        user = result.scalar_one_or_none()
        if not user:
            return None

        if not self.verify_password(password, user.hashed_password):
            return None

        return user

    async def link_github_account(
        self,
        user_id: str,
        code: str,
        db: AsyncSession,
    ) -> User:
        """Link GitHub account to an existing user."""
        github_token = await self._exchange_code_for_token(code)
        github_user = await self._get_github_user(github_token)
        github_id = github_user['id']

        # Check if already linked to another account
        result = await db.execute(
            select(User).where(User.github_id == github_id, User.id != user_id)
        )
        if result.scalar_one_or_none():
            raise ValueError("This GitHub account is already linked to another user.")

        from sqlalchemy.orm import selectinload
        user_result = await db.execute(
            select(User)
            .options(selectinload(User.role))
            .where(User.id == user_id)
        )
        user = user_result.scalar_one_or_none()
        if not user:
            raise ValueError("User not found")

        user.github_id = github_id
        user.github_username = github_user['login']
        user.github_email = github_user.get('email')
        user.github_avatar_url = github_user.get('avatar_url')
        user.github_access_token = github_token
        user.updated_at = datetime.utcnow()

        await db.commit()
        await db.refresh(user)
        return user

    async def link_gitlab_account(
        self,
        user_id: str,
        code: str,
        db: AsyncSession,
    ) -> User:
        """Link GitLab account to an existing user."""
        gitlab_token = await self._exchange_gitlab_code_for_token(code)
        gitlab_user = await self._get_gitlab_user(gitlab_token)
        gitlab_id = gitlab_user['id']

        # Check if already linked to another account
        result = await db.execute(
            select(User).where(User.gitlab_id == gitlab_id, User.id != user_id)
        )
        if result.scalar_one_or_none():
            raise ValueError("This GitLab account is already linked to another user.")

        from sqlalchemy.orm import selectinload
        user_result = await db.execute(
            select(User)
            .options(selectinload(User.role))
            .where(User.id == user_id)
        )
        user = user_result.scalar_one_or_none()
        if not user:
            raise ValueError("User not found")

        user.gitlab_id = gitlab_id
        user.gitlab_username = gitlab_user['username']
        user.gitlab_email = gitlab_user.get('email')
        user.gitlab_avatar_url = gitlab_user.get('avatar_url')
        user.gitlab_access_token = gitlab_token
        user.updated_at = datetime.utcnow()

        await db.commit()
        await db.refresh(user)
        return user

    def get_github_auth_url(self) -> tuple[str, str]:
        """
        Generate GitHub OAuth authorization URL.

        Returns:
            Tuple of (auth_url, state) for CSRF protection
        """
        state = secrets.token_urlsafe(32)

        params = {
            'client_id': settings.github_client_id,
            'redirect_uri': settings.github_redirect_uri,
            'scope': 'read:user user:email repo',
            'state': state,
        }

        auth_url = f'{GITHUB_AUTHORIZE_URL}?{urlencode(params)}'
        return auth_url, state

    async def handle_github_callback(
        self,
        code: str,
        db: AsyncSession,
    ) -> tuple[User, str]:
        """
        Handle GitHub OAuth callback.

        Args:
            code: Authorization code from GitHub
            db: Database session

        Returns:
            Tuple of (user, access_token)

        Raises:
            ValueError: If OAuth fails
        """
        # Exchange code for access token
        github_token = await self._exchange_code_for_token(code)

        # Get GitHub user info
        github_user = await self._get_github_user(github_token)

        # Create or update user in database
        user = await self._get_or_create_user(db, github_user, github_token)

        # Generate JWT access token
        access_token = self.create_access_token(user)

        return user, access_token

    async def _exchange_code_for_token(self, code: str) -> str:
        """Exchange authorization code for GitHub access token."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                GITHUB_TOKEN_URL,
                data={
                    'client_id': settings.github_client_id,
                    'client_secret': settings.github_client_secret,
                    'code': code,
                },
                headers={'Accept': 'application/json'},
            )

            if response.status_code != 200:
                logger.error(f'GitHub token exchange failed: {response.text}')
                raise ValueError('Failed to exchange code for token')

            data = response.json()

            if 'error' in data:
                logger.error(f'GitHub OAuth error: {data}')
                raise ValueError(data.get('error_description', 'OAuth error'))

            return data['access_token']

    async def _get_github_user(self, access_token: str) -> dict[str, Any]:
        """Get GitHub user profile using access token."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                GITHUB_USER_URL,
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'Accept': 'application/json',
                },
            )

            if response.status_code != 200:
                logger.error(f'GitHub user fetch failed: {response.text}')
                raise ValueError('Failed to get GitHub user')

            return response.json()

    async def _get_or_create_user(
        self,
        db: AsyncSession,
        github_user: dict[str, Any],
        access_token: str,
    ) -> User:
        """Get existing user or create new one from GitHub profile."""
        github_id = github_user['id']

        # Try to find existing user
        result = await db.execute(
            select(User).where(User.github_id == github_id)
        )
        user = result.scalar_one_or_none()

        if user:
            # Update existing user
            user.github_username = github_user['login']
            user.github_email = github_user.get('email')
            user.github_avatar_url = github_user.get('avatar_url')
            user.github_access_token = access_token
            user.last_login_at = datetime.utcnow()
            logger.info(f'User logged in: {user.github_username}')
        else:
            # Create new user
            user = User(
                github_id=github_id,
                github_username=github_user['login'],
                github_email=github_user.get('email'),
                github_avatar_url=github_user.get('avatar_url'),
                github_access_token=access_token,
                last_login_at=datetime.utcnow(),
            )
            db.add(user)
            logger.info(f'New user created: {user.github_username}')

        await db.commit()
        await db.refresh(user)
        return user

    # ─────────────────────────────────────────────────────────────
    # GitLab OAuth Methods
    # ─────────────────────────────────────────────────────────────

    def get_gitlab_auth_url(self) -> tuple[str, str]:
        """
        Generate GitLab OAuth authorization URL.

        Returns:
            Tuple of (auth_url, state) for CSRF protection
        """
        state = secrets.token_urlsafe(32)

        params = {
            'client_id': settings.gitlab_client_id,
            'redirect_uri': settings.gitlab_redirect_uri,
            'response_type': 'code',
            'scope': 'read_user api read_repository',
            'state': state,
        }

        auth_url = f'{GITLAB_AUTHORIZE_URL}?{urlencode(params)}'
        return auth_url, state

    async def handle_gitlab_callback(
        self,
        code: str,
        db: AsyncSession,
    ) -> tuple[User, str]:
        """
        Handle GitLab OAuth callback.

        Args:
            code: Authorization code from GitLab
            db: Database session

        Returns:
            Tuple of (user, access_token)

        Raises:
            ValueError: If OAuth fails
        """
        # Exchange code for access token
        gitlab_token = await self._exchange_gitlab_code_for_token(code)

        # Get GitLab user info
        gitlab_user = await self._get_gitlab_user(gitlab_token)

        # Create or update user in database
        user = await self._get_or_create_gitlab_user(db, gitlab_user, gitlab_token)

        # Generate JWT access token
        access_token = self.create_access_token(user)

        return user, access_token

    async def _exchange_gitlab_code_for_token(self, code: str) -> str:
        """Exchange authorization code for GitLab access token."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                GITLAB_TOKEN_URL,
                data={
                    'client_id': settings.gitlab_client_id,
                    'client_secret': settings.gitlab_client_secret,
                    'code': code,
                    'grant_type': 'authorization_code',
                    'redirect_uri': settings.gitlab_redirect_uri,
                },
                headers={'Accept': 'application/json'},
            )

            if response.status_code != 200:
                logger.error(f'GitLab token exchange failed: {response.text}')
                raise ValueError('Failed to exchange code for token')

            data = response.json()

            if 'error' in data:
                logger.error(f'GitLab OAuth error: {data}')
                raise ValueError(data.get('error_description', 'OAuth error'))

            return data['access_token']

    async def _get_gitlab_user(self, access_token: str) -> dict[str, Any]:
        """Get GitLab user profile using access token."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                GITLAB_USER_URL,
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'Accept': 'application/json',
                },
            )

            if response.status_code != 200:
                logger.error(f'GitLab user fetch failed: {response.text}')
                raise ValueError('Failed to get GitLab user')

            return response.json()

    async def _get_or_create_gitlab_user(
        self,
        db: AsyncSession,
        gitlab_user: dict[str, Any],
        access_token: str,
    ) -> User:
        """Get existing user or create new one from GitLab profile."""
        gitlab_id = gitlab_user['id']

        # Try to find existing user by GitLab ID
        result = await db.execute(
            select(User).where(User.gitlab_id == gitlab_id)
        )
        user = result.scalar_one_or_none()

        if user:
            # Update existing user's GitLab info
            user.gitlab_username = gitlab_user['username']
            user.gitlab_email = gitlab_user.get('email')
            user.gitlab_avatar_url = gitlab_user.get('avatar_url')
            user.gitlab_access_token = access_token
            user.last_login_at = datetime.utcnow()
            logger.info(f'GitLab user logged in: {user.gitlab_username}')
        else:
            # Check if user exists with same email (link accounts)
            email = gitlab_user.get('email')
            if email:
                result = await db.execute(
                    select(User).where(User.github_email == email)
                )
                user = result.scalar_one_or_none()

            if user:
                # Link GitLab to existing GitHub user
                user.gitlab_id = gitlab_id
                user.gitlab_username = gitlab_user['username']
                user.gitlab_email = email
                user.gitlab_avatar_url = gitlab_user.get('avatar_url')
                user.gitlab_access_token = access_token
                user.last_login_at = datetime.utcnow()
                logger.info(f'Linked GitLab account to existing user: {user.github_username}')
            else:
                # Create new user (GitLab-only)
                # Note: github_id is required, so we use a placeholder
                # In production, you might want to make github_id nullable
                user = User(
                    github_id=0,  # Placeholder for GitLab-only users
                    github_username=gitlab_user['username'],  # Use GitLab username as fallback
                    gitlab_id=gitlab_id,
                    gitlab_username=gitlab_user['username'],
                    gitlab_email=gitlab_user.get('email'),
                    gitlab_avatar_url=gitlab_user.get('avatar_url'),
                    gitlab_access_token=access_token,
                    last_login_at=datetime.utcnow(),
                )
                db.add(user)
                logger.info(f'New GitLab user created: {user.gitlab_username}')

        await db.commit()
        await db.refresh(user)
        return user

    # ─────────────────────────────────────────────────────────────
    # JWT Token Methods
    # ─────────────────────────────────────────────────────────────

    def create_access_token(self, user: User) -> str:
        """
        Create JWT access token for user.

        Args:
            user: User model instance

        Returns:
            JWT token string
        """
        expire = datetime.utcnow() + timedelta(
            minutes=settings.jwt_access_token_expire_minutes
        )

        payload = {
            'sub': user.id,
            'username': user.username,
            'email': user.email,
            'roles_id': user.role.authority if user.role else '2',
            'type': 'access',
            'exp': expire,
            'iat': datetime.utcnow().timestamp(),
        }

        token = jwt.encode(
            payload,
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )

        return token

    def create_refresh_token(self, user: User) -> str:
        """
        Create JWT refresh token for user.
        """
        expire = datetime.utcnow() + timedelta(
            days=settings.jwt_refresh_token_expire_days
        )

        payload = {
            'sub': user.id,
            'type': 'refresh',
            'exp': expire,
            'iat': datetime.utcnow().timestamp(),
        }

        token = jwt.encode(
            payload,
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )

        return token

    async def blacklist_token(
        self,
        db: AsyncSession,
        token: str,
        user_id: str,
        expires_at: datetime,
    ) -> None:
        """Add a token to the blacklist in database."""
        from apps.auth.models.blacklisted_token import BlacklistedToken
        # Make sure expires_at is timezone-naive if stored so
        if expires_at.tzinfo is not None:
            expires_at = expires_at.replace(tzinfo=None)

        blacklisted = BlacklistedToken(
            token=token,
            user_id=user_id,
            expires_at=expires_at,
            blacklisted_at=datetime.utcnow()
        )
        db.add(blacklisted)
        await db.commit()

    async def is_token_blacklisted(
        self,
        db: AsyncSession,
        token: str,
    ) -> bool:
        """Check if a token is in the database blacklist."""
        from apps.auth.models.blacklisted_token import BlacklistedToken
        result = await db.execute(
            select(BlacklistedToken).where(BlacklistedToken.token == token)
        )
        return result.scalar_one_or_none() is not None

    async def get_current_user(
        self,
        token: str,
        db: AsyncSession,
    ) -> User | None:
        """
        Validate JWT token and return current user.

        Checks blacklist database and user's last_logout timestamp.

        Args:
            token: JWT access token
            db: Database session

        Returns:
            User if token is valid, None otherwise
        """
        try:
            # 1. Check blacklist
            if await self.is_token_blacklisted(db, token):
                return None

            payload = jwt.decode(
                token,
                settings.jwt_secret_key,
                algorithms=[settings.jwt_algorithm],
            )

            # 2. Check token type
            token_type = payload.get('type')
            if token_type != 'access':
                return None

            user_id = payload.get('sub')
            if not user_id:
                return None

            from sqlalchemy.orm import selectinload
            result = await db.execute(
                select(User)
                .options(selectinload(User.role))
                .where(User.id == user_id, User.is_active == True)
            )
            user = result.scalar_one_or_none()
            if not user:
                return None

            # 3. Check last_logout validation
            iat = payload.get('iat')
            if user.last_logout and iat:
                last_logout_ts = user.last_logout.timestamp()
                if iat <= last_logout_ts:
                    logger.debug("Token issued before last logout")
                    return None

            return user

        except JWTError as e:
            logger.debug(f'JWT validation failed: {e}')
            return None

    async def logout(self, token: str) -> None:
        """
        Invalidate user session.
        (Note: For route-level logout, we use db-backed blacklisting inside the API controller,
        this method is retained for compatibility).
        """
        pass


# Global service instance
auth_service = AuthService()
