"""
Redis client for caching and session management.
"""

import json
from typing import Any

import redis.asyncio as redis

from infra.config import settings
from core.logging_config import get_logger

logger = get_logger(__name__)


class RedisClient:
    """
    Async Redis client with helper methods for caching.

    Key naming convention:
        codelingerk:v1:{entity}:{identifier}:{variant}

    Examples:
        codelingerk:v1:session:abc123
        codelingerk:v1:user:123:repos
        codelingerk:v1:repo:456:index_status
    """

    PREFIX = 'codelingerk:v1'

    def __init__(self):
        self._client: redis.Redis | None = None

    async def connect(self) -> None:
        """Connect to Redis."""
        try:
            self._client = redis.from_url(
                settings.redis_url,
                encoding='utf-8',
                decode_responses=True,
            )
            await self._client.ping()
        except Exception as e:
            logger.error(f'Failed to connect to Redis: {e}')
            raise

    async def close(self) -> None:
        """Close Redis connection."""
        if self._client:
            await self._client.close()
            self._client = None
            logger.info('Redis connection closed')

    def _key(self, *parts: str) -> str:
        """Build a prefixed Redis key."""
        return ':'.join([self.PREFIX, *parts])

    async def get(self, *key_parts: str) -> str | None:
        """Get a string value."""
        if not self._client:
            raise RuntimeError('Redis client not connected')
        return await self._client.get(self._key(*key_parts))

    async def set(
        self,
        *key_parts: str,
        value: str,
        ttl_seconds: int | None = None,
    ) -> None:
        """Set a string value with optional TTL."""
        if not self._client:
            raise RuntimeError('Redis client not connected')
        key = self._key(*key_parts)
        if ttl_seconds:
            await self._client.setex(key, ttl_seconds, value)
        else:
            await self._client.set(key, value)

    async def delete(self, *key_parts: str) -> None:
        """Delete a key."""
        if not self._client:
            raise RuntimeError('Redis client not connected')
        await self._client.delete(self._key(*key_parts))

    async def get_json(self, *key_parts: str) -> dict[str, Any] | None:
        """Get a JSON value as dict."""
        value = await self.get(*key_parts)
        if value:
            return json.loads(value)
        return None

    async def set_json(
        self,
        *key_parts: str,
        value: dict[str, Any],
        ttl_seconds: int | None = None,
    ) -> None:
        """Set a JSON value from dict."""
        await self.set(*key_parts, value=json.dumps(value), ttl_seconds=ttl_seconds)

    async def exists(self, *key_parts: str) -> bool:
        """Check if key exists."""
        if not self._client:
            raise RuntimeError('Redis client not connected')
        return await self._client.exists(self._key(*key_parts)) > 0

    async def health_check(self) -> bool:
        """Check if Redis connection is healthy."""
        try:
            if not self._client:
                return False
            await self._client.ping()
            return True
        except Exception:
            return False

    # Session management helpers
    async def store_session(
        self,
        session_id: str,
        user_data: dict[str, Any],
        ttl_days: int = 7,
    ) -> None:
        """Store user session."""
        await self.set_json(
            'session', session_id,
            value=user_data,
            ttl_seconds=ttl_days * 24 * 60 * 60,
        )

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Get user session."""
        return await self.get_json('session', session_id)

    async def delete_session(self, session_id: str) -> None:
        """Delete user session."""
        await self.delete('session', session_id)

    # OAuth state management
    async def store_oauth_state(self, state: str, ttl_minutes: int = 10) -> None:
        """Store OAuth state for CSRF protection."""
        await self.set('oauth', 'state', state, value='1', ttl_seconds=ttl_minutes * 60)

    async def verify_oauth_state(self, state: str) -> bool:
        """Verify and consume OAuth state."""
        exists = await self.exists('oauth', 'state', state)
        if exists:
            await self.delete('oauth', 'state', state)
            return True
        return False


# Global client instance
redis_client = RedisClient()
