import pytest
import pytest_asyncio
import json
import asyncio
from uuid import uuid4
from fastapi import Depends
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from main import app
from infra.database import get_db
from infra.config import settings
from infra.redis_client import redis_client
from apps.auth.models.user import User
from apps.auth.api.middleware import get_current_user
from apps.repositories.models.repository import Repository
from apps.ai_reviewer.models.pull_request import PullRequest
from apps.ai_reviewer.models.snapshot import Snapshot, SnapshotStatus


@pytest_asyncio.fixture(autouse=True)
async def setup_redis():
    await redis_client.connect()
    yield
    if redis_client._client:
        await redis_client._client.aclose()


@pytest_asyncio.fixture
async def test_ctx():
    """Setup test user, repo, PR, and snapshot, and clean them up after."""
    test_engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
    )
    test_session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with test_session_factory() as db:
        # Create a test user
        user = User(
            id=str(uuid4()),
            username=f"test_sse_{uuid4().hex[:6]}",
            email=f"test_sse_{uuid4().hex[:6]}@example.com",
            hashed_password="hashed",
            is_active=True,
        )
        db.add(user)
        await db.flush()

        # Create a test repository
        repo = Repository(
            id=str(uuid4()),
            provider="github",
            provider_repo_id=123457,
            owner_id=user.id,
            full_name=f"test_sse/repo_{uuid4().hex[:6]}",
            name="sse-repo",
            clone_url="https://github.com/test_sse/repo.git",
            is_active=True,
        )
        db.add(repo)
        await db.flush()

        # Create a test Pull Request
        pr = PullRequest(
            id=str(uuid4()),
            repository_id=repo.id,
            pr_number=1,
            title="SSE Test PR",
            status="open",
            source_branch="feature",
            target_branch="main",
        )
        db.add(pr)
        await db.flush()

        # Create a test Snapshot
        snapshot = Snapshot(
            id=str(uuid4()),
            pull_request_id=pr.id,
            commit_sha="a" * 40,
            status="pending",
        )
        db.add(snapshot)
        await db.flush()
        await db.commit()

        # Override get_db dependency
        async def override_get_db():
            yield db
        
        # Override get_current_user dependency
        async def override_current_user():
            return user

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = override_current_user

        yield db, user, repo, pr, snapshot

        app.dependency_overrides.clear()

        # Cleanup
        await db.delete(snapshot)
        await db.delete(pr)
        await db.delete(repo)
        await db.delete(user)
        await db.commit()

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_publish_snapshot_status():
    # Test that publish_snapshot_status publishes to the correct channel
    snapshot_id = str(uuid4())
    channel = f"codelingerk:v1:snapshot:{snapshot_id}:events"
    
    pubsub = redis_client._client.pubsub()
    await pubsub.subscribe(channel)
    
    # Run publish in task or simple await
    await redis_client.publish_snapshot_status(snapshot_id, "processing", "Some error")
    
    # Check pubsub message
    confirm = await pubsub.get_message(timeout=1.0)
    assert confirm is not None
    assert confirm['type'] == 'subscribe'
    
    # Now read the published message
    message = await pubsub.get_message(timeout=1.0)
    assert message is not None
    assert message['type'] == 'message'
    
    data = json.loads(message['data'])
    assert data['snapshot_id'] == snapshot_id
    assert data['status'] == "processing"
    assert data['error_message'] == "Some error"
    
    await pubsub.unsubscribe(channel)
    await pubsub.aclose()


@pytest.mark.asyncio
async def test_sse_endpoint(test_ctx):
    db, user, repo, pr, snapshot = test_ctx
    
    # Task to publish an update to Redis after a short delay
    async def publish_update_later():
        await asyncio.sleep(0.5)
        # Re-connect/ensure connection for the background task context
        await redis_client.publish_snapshot_status(snapshot.id, "completed")
    
    pub_task = asyncio.create_task(publish_update_later())
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # We start the stream and read the first event (initial status)
        async with client.stream("GET", f"/api/v1/snapshots/{snapshot.id}/events") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            
            events = []
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    events.append(json.loads(line[5:]))
                    if len(events) == 2:
                        break
            
            assert len(events) == 2
            assert events[0]["snapshot_id"] == snapshot.id
            assert events[0]["status"] == "pending"
            assert events[1]["snapshot_id"] == snapshot.id
            assert events[1]["status"] == "completed"
            
    await pub_task


@pytest.mark.asyncio
async def test_publish_run_log():
    # Test that publish_run_log publishes to the correct logs channel
    snapshot_id = str(uuid4())
    channel = f"codelingerk:v1:snapshot:{snapshot_id}:logs"
    
    pubsub = redis_client._client.pubsub()
    await pubsub.subscribe(channel)
    
    # Run publish log
    await redis_client.publish_run_log(snapshot_id, "INFO", "Hello Log!")
    
    # Check pubsub message
    confirm = await pubsub.get_message(timeout=1.0)
    assert confirm is not None
    assert confirm['type'] == 'subscribe'
    
    # Now read the published message
    message = await pubsub.get_message(timeout=1.0)
    assert message is not None
    assert message['type'] == 'message'
    
    data = json.loads(message['data'])
    assert data['timestamp'] is not None
    assert data['level'] == "INFO"
    assert data['message'] == "Hello Log!"
    
    await pubsub.unsubscribe(channel)
    await pubsub.aclose()


@pytest.mark.asyncio
async def test_sse_logs_endpoint(test_ctx):
    db, user, repo, pr, snapshot = test_ctx
    
    # Task to publish logs to Redis after a short delay
    async def publish_logs_later():
        await asyncio.sleep(0.3)
        await redis_client.publish_run_log(snapshot.id, "INFO", "Step 1 complete")
        await asyncio.sleep(0.2)
        await redis_client.publish_run_log(snapshot.id, "INFO", "[EOF]")
    
    pub_task = asyncio.create_task(publish_logs_later())
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        # We start the stream and read events
        async with client.stream("GET", f"/api/v1/snapshots/{snapshot.id}/logs") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            
            events = []
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    events.append(json.loads(line[5:]))
                    if len(events) == 3:
                        break
            
            assert len(events) == 3
            assert events[0]["message"] == "Connected to log stream..."
            assert events[1]["message"] == "Step 1 complete"
            assert events[2]["message"] == "[EOF]"
            
    await pub_task


@pytest.mark.asyncio
async def test_sse_logs_redis_timeout(test_ctx):
    db, user, repo, pr, snapshot = test_ctx
    import redis.exceptions

    class MockPubSub:
        async def subscribe(self, *args, **kwargs):
            pass
        async def unsubscribe(self, *args, **kwargs):
            pass
        async def aclose(self, *args, **kwargs):
            pass
        async def listen(self):
            raise redis.exceptions.TimeoutError("Timeout reading from localhost:6379")
            yield

    original_pubsub = redis_client._client.pubsub
    redis_client._client.pubsub = lambda: MockPubSub()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            async with client.stream("GET", f"/api/v1/snapshots/{snapshot.id}/logs") as response:
                assert response.status_code == 200
                events = []
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        events.append(json.loads(line[5:]))
                
                assert len(events) == 2
                assert events[0]["message"] == "Connected to log stream..."
                assert events[1]["message"] == "Redis connection lost or timed out"
                assert events[1]["level"] == "ERROR"
    finally:
        redis_client._client.pubsub = original_pubsub


@pytest.mark.asyncio
async def test_sse_events_redis_timeout(test_ctx):
    db, user, repo, pr, snapshot = test_ctx
    import redis.exceptions

    class MockPubSub:
        async def subscribe(self, *args, **kwargs):
            pass
        async def unsubscribe(self, *args, **kwargs):
            pass
        async def aclose(self, *args, **kwargs):
            pass
        async def listen(self):
            raise redis.exceptions.TimeoutError("Timeout reading from localhost:6379")
            yield

    original_pubsub = redis_client._client.pubsub
    redis_client._client.pubsub = lambda: MockPubSub()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            async with client.stream("GET", f"/api/v1/snapshots/{snapshot.id}/events") as response:
                assert response.status_code == 200
                events = []
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        events.append(json.loads(line[5:]))
                
                assert len(events) == 2
                assert events[0]["status"] == "pending"
                assert events[1]["status"] == "failed"
                assert events[1]["error_message"] == "Redis connection lost or timed out"
    finally:
        redis_client._client.pubsub = original_pubsub
