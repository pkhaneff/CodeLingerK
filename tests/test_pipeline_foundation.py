import pytest
import pytest_asyncio
from uuid import uuid4
from sqlalchemy import select

from infra.database import get_db_context
from infra.redis_client import redis_client
from apps.auth.models.user import User
from apps.repositories.models.repository import Repository
from apps.ai_reviewer.models.pull_request import PullRequest
from apps.ai_reviewer.models.snapshot import Snapshot, SnapshotStatus
from apps.ai_reviewer.integrations.queue_service import QueueService
from apps.ai_reviewer.models.review_job import JobType


from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool
from infra.config import settings


@pytest_asyncio.fixture
async def db():
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
    async with test_session_factory() as session:
        yield session
    await test_engine.dispose()


@pytest_asyncio.fixture
async def redis():
    await redis_client.connect()
    if redis_client._client:
        await redis_client._client.flushdb()
    yield redis_client
    if redis_client._client:
        await redis_client._client.flushdb()
    await redis_client.close()


@pytest.mark.asyncio
async def test_snapshot_model(db):
    """Test Snapshot model can be queried."""
    result = await db.execute(select(Snapshot).limit(1))
    # Should not raise
    assert True


@pytest.mark.asyncio
async def test_queue_enqueue_dequeue(redis):
    """Test queue operations."""
    queue = QueueService()
    test_snapshot_id = str(uuid4())

    # Enqueue
    job_id = await queue.enqueue(
        JobType.CONTEXT,
        snapshot_id=test_snapshot_id,
        priority=50,
    )
    assert job_id is not None

    # Stats should show 1 job
    stats = await queue.get_queue_stats()
    assert stats['queues']['context'] >= 1

    # Dequeue
    job = await queue.dequeue(JobType.CONTEXT, timeout=1)
    assert job is not None
    assert job['snapshot_id'] == test_snapshot_id


@pytest.mark.asyncio
async def test_queue_fail_non_existent_snapshot(redis, db):
    """Test that queue.fail disables retry if snapshot does not exist in DB."""
    queue = QueueService(db=db)
    non_existent_snapshot_id = str(uuid4())

    queue_no_db = QueueService(db=None)
    job_id = await queue_no_db.enqueue(
        JobType.CONTEXT,
        snapshot_id=non_existent_snapshot_id,
        priority=50,
    )
    assert job_id is not None

    job = await queue_no_db.dequeue(JobType.CONTEXT, timeout=1)
    assert job is not None

    # Call fail: should not raise integrity error and instead place in DLQ
    await queue.fail(job_id, "Snapshot not found error message", retry=True)

    # Check that job is in DLQ
    dlq_jobs = await redis._client.zrange(queue._dead_letter_key(), 0, -1)
    assert job_id in dlq_jobs