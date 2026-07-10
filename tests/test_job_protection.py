import asyncio
from datetime import datetime
from uuid import uuid4
import pytest
import pytest_asyncio
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from core.exceptions import ConflictException
from infra.redis_client import redis_client
from infra.database import Base
from infra.config import settings

from apps.auth.models.user import User
from apps.repositories.models.repository import Repository
from apps.ai_reviewer.models.pull_request import PullRequest
from apps.ai_reviewer.models.snapshot import Snapshot
from apps.ai_reviewer.models.review_job import JobType
from apps.ai_reviewer.integrations.queue_service import QueueService


@pytest_asyncio.fixture(autouse=True)
async def setup_redis():
    await redis_client.connect()
    if redis_client._client:
        await redis_client._client.flushdb()
    yield
    if redis_client._client:
        await redis_client._client.flushdb()
    await redis_client.close()


@pytest_asyncio.fixture
async def db_session():
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
        # Create test models
        user = User(
            id=str(uuid4()),
            username=f"test_jp_{uuid4().hex[:6]}",
            email=f"test_jp_{uuid4().hex[:6]}@example.com",
            hashed_password="hashed",
            is_active=True,
        )
        session.add(user)
        await session.flush()

        repo = Repository(
            id=str(uuid4()),
            provider="github",
            provider_repo_id=987654,
            owner_id=user.id,
            full_name=f"test_jp/repo_{uuid4().hex[:6]}",
            name="jp-repo",
            clone_url="https://github.com/test_jp/repo.git",
            is_active=True,
        )
        session.add(repo)
        await session.flush()

        pr = PullRequest(
            id=str(uuid4()),
            repository_id=repo.id,
            pr_number=42,
            title="Job Protection Test PR",
            status="open",
            source_branch="feature",
            target_branch="main",
        )
        session.add(pr)
        await session.flush()

        snapshot1 = Snapshot(
            id=str(uuid4()),
            pull_request_id=pr.id,
            commit_sha="a" * 40,
            status="pending",
        )
        snapshot2 = Snapshot(
            id=str(uuid4()),
            pull_request_id=pr.id,
            commit_sha="b" * 40,
            status="pending",
        )
        session.add_all([snapshot1, snapshot2])
        await session.flush()
        await session.commit()

        yield session, repo, pr, snapshot1, snapshot2

        # Cleanup
        await session.delete(snapshot1)
        await session.delete(snapshot2)
        await session.delete(pr)
        await session.delete(repo)
        await session.delete(user)
        await session.commit()

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_redis_lock_duplicate_job_prevention(db_session):
    """Test that enqueuing multiple CONTEXT jobs for same commit SHA fails on lock."""
    db, repo, pr, snapshot1, _ = db_session
    queue_service = QueueService(db)

    # 1. First enqueue should succeed
    job_id = await queue_service.enqueue(JobType.CONTEXT, snapshot1.id)
    assert job_id is not None

    # Verify lock exists in Redis
    lock_key = f"review_lock:repo:{repo.id}:pr:{pr.pr_number}:sha:{snapshot1.commit_sha}"
    assert await redis_client.exists(lock_key)

    # 2. Second enqueue for the same snapshot should raise ConflictException
    with pytest.raises(ConflictException) as exc_info:
        await queue_service.enqueue(JobType.CONTEXT, snapshot1.id)
    assert "đang được xử lý" in str(exc_info.value)


@pytest.mark.asyncio
async def test_queue_depth_limit(db_session):
    """Test that enqueuing fails when CONTEXT queue depth limit (20) is reached."""
    db, repo, pr, snapshot1, _ = db_session
    queue_service = QueueService(db)

    # 1. Fill queue with 20 dummy jobs
    queue_key = queue_service._queue_key(JobType.CONTEXT.value)
    for i in range(20):
        await redis_client._client.zadd(queue_key, {f"dummy_job_{i}": 1})

    # 2. Try to enqueue, should raise ConflictException due to queue full
    with pytest.raises(ConflictException) as exc_info:
        await queue_service.enqueue(JobType.CONTEXT, snapshot1.id)
    assert "đầy" in str(exc_info.value)


@pytest.mark.asyncio
async def test_concurrency_limit_by_repository(db_session):
    """Test that concurrent limit per repository defers job dequeues and allows fair scheduling."""
    db, repo, pr, snapshot1, snapshot2 = db_session
    queue_service = QueueService(db)

    # Enqueue both snapshots
    job1_id = await queue_service.enqueue(JobType.CONTEXT, snapshot1.id)
    # Temporary clear lock for snapshot2 so we can enqueue it under same repo (different commit SHA)
    job2_id = await queue_service.enqueue(JobType.CONTEXT, snapshot2.id)

    # Simulatately set running count = 2 for this repository in Redis
    running_key = redis_client._key(f"repo:{repo.id}:running_jobs")
    await redis_client._client.sadd(running_key, "active_snap_1", "active_snap_2")

    # Dequeue should return None because the repo is running 2 jobs
    job = await queue_service.dequeue(JobType.CONTEXT)
    assert job is None

    # Decrement running jobs count to 1
    await redis_client._client.srem(running_key, "active_snap_1")

    # Dequeue should now succeed and return the first job
    job = await queue_service.dequeue(JobType.CONTEXT)
    assert job is not None
    assert job["job_id"] == job1_id


@pytest.mark.asyncio
async def test_lock_release_on_completion_and_failure(db_session):
    """Test lock is released when PUBLISH completes or when job permanently fails."""
    db, repo, pr, snapshot1, _ = db_session
    queue_service = QueueService(db)

    # 1. Test release on complete (PUBLISH)
    # Enqueue context job, which sets lock_key in metadata
    job_id = await queue_service.enqueue(JobType.CONTEXT, snapshot1.id)
    job = await queue_service.get_job(job_id)
    lock_key = job["metadata"]["lock_key"]

    # Mock this job as a PUBLISH job completing
    job["job_type"] = JobType.PUBLISH.value
    await redis_client._client.set(queue_service._job_key(job_id), json_dumps(job))

    # Complete the PUBLISH job
    await queue_service.complete(job_id)

    # Verify lock is deleted
    assert not await redis_client.exists(lock_key)
    assert not await redis_client.exists(f"repo:{repo.id}:running_jobs")

    # 2. Test release on permanent failure (DLQ)
    job_id2 = await queue_service.enqueue(JobType.CONTEXT, snapshot1.id)
    job2 = await queue_service.get_job(job_id2)
    lock_key2 = job2["metadata"]["lock_key"]

    # Simulate failing permanently by calling fail with max_attempts reached (or retry=False)
    # Let's set attempt = 3 so it goes to DLQ on next failure
    job2["attempt"] = 3
    await redis_client._client.set(queue_service._job_key(job_id2), json_dumps(job2))

    await queue_service.fail(job_id2, "Unrecoverable error", retry=True)

    # Verify lock is deleted on permanent failure
    assert not await redis_client.exists(lock_key2)


def json_dumps(obj):
    import json
    return json.dumps(obj)
