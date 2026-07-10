import math
import time
import uuid
from fastapi import Request, Response
from jose import jwt, JWTError

from core.exceptions import TooManyRequestsException
from core.logger import get_logger
from infra.config import settings
from infra.redis_client import redis_client

logger = get_logger(__name__)

LUA_SLIDING_WINDOW = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

local clear_before = now - window

-- Remove old items
redis.call('ZREMRANGEBYSCORE', key, 0, clear_before)

-- Count current items
local current_requests = redis.call('ZCARD', key)

if current_requests < limit then
    -- Add the new request
    redis.call('ZADD', key, now, member)
    -- Set TTL on key
    redis.call('EXPIRE', key, window)
    return {1, current_requests + 1, 0}
else
    -- Find the oldest timestamp in the zset
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_after = 1
    if oldest and oldest[2] then
        local oldest_ts = tonumber(oldest[2])
        retry_after = math.max(1, math.ceil(oldest_ts + window - now))
    end
    return {0, current_requests, retry_after}
end
"""


class RateLimiter:
    """
    FastAPI Dependency for Rate Limiting.
    Implements a sliding window log algorithm using Redis Lua Script.
    """
    def __init__(self, times: int, seconds: int, name: str = "default", use_ip: bool = False, use_repo: bool = False):
        self.times = times
        self.seconds = seconds
        self.name = name
        self.use_ip = use_ip
        self.use_repo = use_repo

    async def __call__(self, request: Request, response: Response):
        # 1. Determine key identifier
        identifier = None
        
        if not self.use_ip:
            # Try to get user_id from Authorization header
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]
                try:
                    payload = jwt.decode(
                        token,
                        settings.jwt_secret_key,
                        algorithms=[settings.jwt_algorithm],
                    )
                    user_id = payload.get("sub")
                    if user_id:
                        identifier = f"user:{user_id}"
                except JWTError:
                    pass
        
        if not identifier:
            # Fallback to Client IP
            client_ip = request.client.host if request.client else "unknown"
            identifier = f"ip:{client_ip}"

        # If use_repo is True, append repository context
        if self.use_repo:
            repo_id = request.path_params.get("repo_id") or request.query_params.get("repo_id")
            if repo_id:
                identifier = f"{identifier}:repo:{repo_id}"

        # 2. Redis check
        client = redis_client._client
        if not client:
            # Fail open if Redis not connected
            logger.warning("Redis client is not connected. Rate limiter falling open.")
            return

        key = f"ratelimit:{self.name}:{identifier}"
        now = time.time()
        member = f"{now}:{uuid.uuid4()}"

        try:
            # Run atomic Lua Script
            result = await client.eval(
                LUA_SLIDING_WINDOW,
                1,  # Number of keys
                redis_client._key(key),  # KEYS[1]
                str(now),  # ARGV[1]
                str(self.seconds),  # ARGV[2]
                str(self.times),  # ARGV[3]
                member  # ARGV[4]
            )
            
            allowed, count, retry_after = result
            
            if not allowed:
                logger.warning(f"Rate limit exceeded for {key}: {count}/{self.times} in {self.seconds}s, retry after {retry_after}s")
                raise TooManyRequestsException(
                    retry_after=retry_after,
                    limit=self.times,
                    remaining=0,
                    message=f"Tần suất yêu cầu quá nhanh. Vui lòng thử lại sau {retry_after} giây."
                )
            
            # Attach successful rate limit headers to response
            response.headers["X-RateLimit-Limit"] = str(self.times)
            response.headers["X-RateLimit-Remaining"] = str(max(0, self.times - count))
            
        except TooManyRequestsException:
            raise
        except Exception as e:
            logger.error(f"Error executing rate limit check in Redis: {e}", exc_info=True)
            # Fail open
            return
