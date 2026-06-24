import pytest
import pytest_asyncio
from uuid import uuid4
from sqlalchemy import select

from infra.database import get_db_context
from infra.redis_client import redis_client
from models import PullRequest, Snapshot, SnapshotStatus
from services.queue_service import QueueService
from models.review_job import JobType


@pytest_asyncio.fixture
async def db():
    async with get_db_context() as session:
        yield session


@pytest_asyncio.fixture
async def redis():
    await redis_client.connect()
    yield redis_client
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