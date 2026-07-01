import pytest
import pytest_asyncio
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from infra.config import settings
from apps.auth.models.user import User
from apps.repositories.models.repository import Repository
from apps.ai_reviewer.models.pull_request import PullRequest, PullRequestStatus
from apps.ai_reviewer.models.snapshot import Snapshot, SnapshotStatus
from apps.ai_reviewer.models.review import Review
from apps.ai_reviewer.services.snapshot_service import SnapshotService
from apps.ai_reviewer.services.ai_review_service import AIReviewService

class DummyGitProvider:
    async def get_pr(self, repo_identifier, pr_number):
        return {
            'title': 'Deduplication Test PR',
            'source_branch': 'dedup-feature',
            'target_branch': 'main',
            'html_url': 'http://github.com/test/repo/pull/1',
        }
    async def get_pr_files(self, repo_identifier, pr_number):
        return [{'filename': 'main.py', 'additions': 5, 'deletions': 1, 'patch': '@@ -1 +1 @@\n-old\n+new'}]

@pytest_asyncio.fixture
async def test_ctx():
    """Setup test user, repo, and PR, and clean them up after."""
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
            username=f"test_dedup_{uuid4().hex[:6]}",
            email=f"test_dedup_{uuid4().hex[:6]}@example.com",
            hashed_password="hashed",
            is_active=True,
        )
        db.add(user)
        await db.flush()

        # Create a test repository
        repo = Repository(
            id=str(uuid4()),
            provider="github",
            provider_repo_id=123456,
            owner_id=user.id,
            full_name=f"test_dedup/repo_{uuid4().hex[:6]}",
            name="dedup-repo",
            clone_url="https://github.com/test_dedup/repo.git",
            is_active=True,
        )
        db.add(repo)
        await db.flush()

        yield db, user, repo

        # Cleanup
        # Delete reviews, snapshots, pull requests, repo, user
        # (Cascade delete-orphan on pull request will handle snapshots,
        # but we also delete reviews explicitly because of set null or FK constraints)
        result = await db.execute(select(Review).where(Review.repository_id == repo.id))
        reviews = result.scalars().all()
        for rev in reviews:
            await db.delete(rev)
        await db.flush()

        result = await db.execute(select(PullRequest).where(PullRequest.repository_id == repo.id))
        prs = result.scalars().all()
        for pr in prs:
            # Delete related snapshots
            snap_result = await db.execute(select(Snapshot).where(Snapshot.pull_request_id == pr.id))
            snaps = snap_result.scalars().all()
            for snap in snaps:
                await db.delete(snap)
            await db.delete(pr)
        await db.flush()

        await db.delete(repo)
        await db.delete(user)
        await db.commit()

    await test_engine.dispose()

@pytest.mark.asyncio
async def test_snapshot_idempotency_created_flag(test_ctx):
    db, user, repo = test_ctx
    git_provider = DummyGitProvider()
    service = SnapshotService(db, git_provider)

    pr_number = 999
    commit_sha = "abcdef0123456789abcdef0123456789abcdef01"

    # First call: should create snapshot and return created=True
    snap1, created1 = await service.create_snapshot(
        repository=repo,
        pr_number=pr_number,
        commit_sha=commit_sha,
    )
    assert snap1 is not None
    assert created1 is True

    # Second call: should return existing snapshot and created=False
    snap2, created2 = await service.create_snapshot(
        repository=repo,
        pr_number=pr_number,
        commit_sha=commit_sha,
    )
    assert snap2.id == snap1.id
    assert created2 is False

@pytest.mark.asyncio
async def test_get_review_for_snapshot_returns_latest(test_ctx):
    db, user, repo = test_ctx
    git_provider = DummyGitProvider()
    service = SnapshotService(db, git_provider)

    pr_number = 999
    commit_sha = "abcdef0123456789abcdef0123456789abcdef02"

    snap, _ = await service.create_snapshot(
        repository=repo,
        pr_number=pr_number,
        commit_sha=commit_sha,
    )

    # We need a pull request for AI review service
    result = await db.execute(select(PullRequest).where(PullRequest.id == snap.pull_request_id))
    pr = result.scalar_one()

    # Create two reviews for this snapshot
    rev1 = Review(
        repository_id=repo.id,
        snapshot_id=snap.id,
        pull_request_number=pr_number,
        commit_sha=commit_sha,
        review_type='pull_request',
        status='completed',
        verdict='approved',
        summary='First review',
    )
    db.add(rev1)
    await db.flush()

    rev2 = Review(
        repository_id=repo.id,
        snapshot_id=snap.id,
        pull_request_number=pr_number,
        commit_sha=commit_sha,
        review_type='pull_request',
        status='completed',
        verdict='changes_requested',
        summary='Second review (latest)',
    )
    db.add(rev2)
    await db.flush()

    ai_service = AIReviewService(db)
    fetched_review = await ai_service.get_review_for_snapshot(snap.id)

    assert fetched_review is not None
    # Since rev2 was inserted after rev1, it should have a later created_at timestamp and be returned
    assert fetched_review.summary == 'Second review (latest)'
