import asyncio
import pytest
import pytest_asyncio
from fastapi import APIRouter, Depends
from httpx import AsyncClient, ASGITransport

from main import app
from infra.redis_client import redis_client
from core.middlewares import RateLimiter
from core.responses import success_response

# ─────────────────────────────────────────────────────────────
# Test Router Setup for Rate Limiter verification
# ─────────────────────────────────────────────────────────────

rate_limit_test_router = APIRouter(prefix="/api/v1/test-ratelimit", tags=["Test Rate Limit"])

@rate_limit_test_router.get("/ip", dependencies=[Depends(RateLimiter(times=3, seconds=2, name="test_ip", use_ip=True))])
async def test_limit_by_ip():
    return success_response({"msg": "success ip"})

@rate_limit_test_router.get("/general", dependencies=[Depends(RateLimiter(times=3, seconds=2, name="test_general"))])
async def test_limit_general():
    return success_response({"msg": "success general"})

# Include test router into the app for testing
app.include_router(rate_limit_test_router)


@pytest_asyncio.fixture(autouse=True)
async def setup_redis():
    await redis_client.connect()
    # Flush db before each test to have a clean environment
    if redis_client._client:
        await redis_client._client.flushdb()
    yield
    # Cleanup keys
    if redis_client._client:
        await redis_client._client.flushdb()
    await redis_client.close()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        yield ac


@pytest.mark.asyncio
async def test_ip_rate_limiting_sliding_window(client):
    """Test that rate limiting works on IP-based endpoints and clears after time window."""
    # 1. First request should succeed and have correct remaining count
    res = await client.get("/api/v1/test-ratelimit/ip")
    assert res.status_code == 200
    assert res.headers["X-RateLimit-Limit"] == "3"
    assert res.headers["X-RateLimit-Remaining"] == "2"

    # 2. Next 2 requests should also succeed
    for remaining in ["1", "0"]:
        res = await client.get("/api/v1/test-ratelimit/ip")
        assert res.status_code == 200
        assert res.headers["X-RateLimit-Limit"] == "3"
        assert res.headers["X-RateLimit-Remaining"] == remaining

    # 3. 4th request must exceed the limit and return 429 along with Retry-After header
    res = await client.get("/api/v1/test-ratelimit/ip")
    assert res.status_code == 429
    assert "Retry-After" in res.headers
    assert int(res.headers["Retry-After"]) >= 1
    assert res.headers["X-RateLimit-Limit"] == "3"
    assert res.headers["X-RateLimit-Remaining"] == "0"

    data = res.json()
    assert data["success"] is False
    assert data["error"]["code"] == "TOO_MANY_REQUESTS"
    assert data["error"]["reason"] == 42901

    # 4. Wait for sliding window to expire (2.1s)
    await asyncio.sleep(2.1)

    # 5. Next request should succeed again
    res = await client.get("/api/v1/test-ratelimit/ip")
    assert res.status_code == 200
    assert res.headers["X-RateLimit-Remaining"] == "2"


@pytest.mark.asyncio
async def test_rate_limiting_fail_open(client):
    """Test that rate limiting fails open when Redis is disconnected."""
    # 1. Disconnect Redis client model
    original_client = redis_client._client
    redis_client._client = None

    try:
        # 2. Try to hit endpoint 5 times (limit is 3), all should succeed (fail open)
        for _ in range(5):
            res = await client.get("/api/v1/test-ratelimit/ip")
            assert res.status_code == 200
    finally:
        # 3. Restore redis client
        redis_client._client = original_client
