import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from main import app
from infra.database import get_db
from infra.config import settings
from infra.redis_client import redis_client
from apps.auth.models.user import User
from apps.auth.services.auth_service import auth_service
from apps.repositories.models.repository import Repository
from apps.repositories.services.repository_service import RepositoryService
from apps.activities.models.activity import Activity
from apps.activities.services.activity_service import activity_service


@pytest_asyncio.fixture(autouse=True)
async def setup_redis():
    await redis_client.connect()
    yield
    await redis_client.close()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        yield ac


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
        async def override_get_db():
            yield session
        
        app.dependency_overrides[get_db] = override_get_db
        yield session
        app.dependency_overrides.clear()
        
        # Cleanup
        await session.execute(select(Activity))
        # Clear out any activities, repos and users created during test
        # We delete in correct cascade order
        activities = (await session.execute(select(Activity).where(Activity.title.like("Test %")))).scalars().all()
        for act in activities:
            await session.delete(act)
        
        repos = (await session.execute(select(Repository).where(Repository.name.like("test-repo-%")))).scalars().all()
        for repo in repos:
            await session.delete(repo)
            
        users = (await session.execute(select(User).where(User.username.like("test_activities_user_%")))).scalars().all()
        for user in users:
            await session.delete(user)
            
        await session.commit()


async def create_test_user(db: AsyncSession, suffix: str) -> User:
    username = f"test_activities_user_{suffix}"
    email = f"test_activities_{suffix}@example.com"
    user = User(
        username=username,
        email=email,
        hashed_password="hashedpassword123",
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def create_test_repo(db: AsyncSession, user: User, name: str, is_active: bool = False) -> Repository:
    repo = Repository(
        provider="github",
        provider_repo_id=12345 + hash(name) % 10000,
        owner_id=user.id,
        full_name=f"test-owner/{name}",
        name=name,
        clone_url=f"https://github.com/test-owner/{name}.git",
        is_active=is_active,
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return repo


@pytest.mark.asyncio
async def test_create_and_list_activities(db_session):
    user = await create_test_user(db_session, "svc")
    repo = await create_test_repo(db_session, user, "test-repo-svc", is_active=True)

    # 1. Create activity
    act = await activity_service.create_activity(
        db=db_session,
        owner_id=user.id,
        repo_id=repo.id,
        type="repo_connected",
        status="success",
        title="Test Repo Connected",
        description="Repository has been connected.",
        action_url="/scans/123",
    )

    assert act.id is not None
    assert act.type == "repo_connected"
    assert act.status == "success"

    # 2. List activities (active repo)
    activities, total = await activity_service.list_activities(
        db=db_session,
        owner_id=user.id,
        page=1,
        limit=10,
    )
    assert total == 1
    assert len(activities) == 1
    assert activities[0].id == act.id


@pytest.mark.asyncio
async def test_list_activities_filtering_inactive_repos(db_session):
    user = await create_test_user(db_session, "filter")
    active_repo = await create_test_repo(db_session, user, "test-repo-active", is_active=True)
    inactive_repo = await create_test_repo(db_session, user, "test-repo-inactive", is_active=False)

    # Create activity for active repo
    await activity_service.create_activity(
        db=db_session,
        owner_id=user.id,
        repo_id=active_repo.id,
        type="repo_connected",
        status="success",
        title="Test Active Repo Connected",
        description="Active repo connected.",
    )

    # Create activity for inactive repo
    await activity_service.create_activity(
        db=db_session,
        owner_id=user.id,
        repo_id=inactive_repo.id,
        type="webhook_added",
        status="success",
        title="Test Inactive Webhook Connected",
        description="Inactive repo webhook connected.",
    )

    # List activities: should only return active repo activity
    activities, total = await activity_service.list_activities(
        db=db_session,
        owner_id=user.id,
    )
    assert total == 1
    assert len(activities) == 1
    assert activities[0].title == "Test Active Repo Connected"


@pytest.mark.asyncio
async def test_repository_service_active_repo_invariants(db_session):
    user = await create_test_user(db_session, "inv")
    repo_service = RepositoryService(db_session, user)

    # Create first repo manually using ORM (simulating get_repo_by_id mock)
    # We test deactivation and remove invariants via repo_service
    repo1 = Repository(
        provider="github",
        provider_repo_id=77701,
        owner_id=user.id,
        full_name="test-owner/test-repo-inv1",
        name="test-repo-inv1",
        clone_url="https://github.com/test-owner/test-repo-inv1.git",
        is_active=False,
    )
    db_session.add(repo1)
    await db_session.commit()
    await db_session.refresh(repo1)

    # Check that when deactivating the only repository, it stays active!
    # Let's activate it first
    await repo_service.activate_repo(repo1.id)
    assert repo1.is_active is True

    # Try deactivating it
    await repo_service.deactivate_repo(repo1.id)
    # Since it is the only repo, it must remain active!
    await db_session.refresh(repo1)
    assert repo1.is_active is True

    # Add second repo
    repo2 = Repository(
        provider="github",
        provider_repo_id=77702,
        owner_id=user.id,
        full_name="test-owner/test-repo-inv2",
        name="test-repo-inv2",
        clone_url="https://github.com/test-owner/test-repo-inv2.git",
        is_active=False,
    )
    db_session.add(repo2)
    await db_session.commit()
    await db_session.refresh(repo2)

    # Activate repo2
    await repo_service.activate_repo(repo2.id)
    await db_session.refresh(repo1)
    await db_session.refresh(repo2)
    assert repo1.is_active is False
    assert repo2.is_active is True

    # Deactivate repo2 (active) -> repo1 (most recent other repo) should be auto-activated
    await repo_service.deactivate_repo(repo2.id)
    await db_session.refresh(repo1)
    await db_session.refresh(repo2)
    assert repo1.is_active is True
    assert repo2.is_active is False

    # Activate repo2 again
    await repo_service.activate_repo(repo2.id)
    # Remove repo2 (active) -> repo1 should be auto-activated
    await repo_service.remove_repo(repo2.id)
    await db_session.refresh(repo1)
    assert repo1.is_active is True


@pytest.mark.asyncio
async def test_api_activities_endpoint(db_session, client):
    user = await create_test_user(db_session, "api")
    repo = await create_test_repo(db_session, user, "test-repo-api", is_active=True)

    # Log several activities
    for i in range(15):
        await activity_service.create_activity(
            db=db_session,
            owner_id=user.id,
            repo_id=repo.id,
            type="security_scan",
            status="success",
            title=f"Test Security Scan {i}",
            description=f"Scan {i} description",
        )

    # Authenticate client
    token = auth_service.create_access_token(user)
    headers = {"Authorization": f"Bearer {token}"}

    # Call endpoint with pagination page=1, limit=10
    response = await client.get("/api/activities?page=1&limit=10", headers=headers)
    assert response.status_code == 200
    
    json_data = response.json()
    assert json_data["success"] is True
    assert len(json_data["data"]) == 10
    assert json_data["pagination"]["total"] == 15
    assert json_data["pagination"]["page"] == 1
    assert json_data["pagination"]["limit"] == 10
    assert json_data["pagination"]["has_more"] is True

    # Verify keys
    item = json_data["data"][0]
    assert "id" in item
    assert "type" in item
    assert "status" in item
    assert "title" in item
    assert "description" in item
    assert "repo_name" in item
    assert "created_at" in item
    assert "action_url" in item

    # Call endpoint page=2, limit=10
    response = await client.get("/api/activities?page=2&limit=10", headers=headers)
    assert response.status_code == 200
    
    json_data2 = response.json()
    assert len(json_data2["data"]) == 5
    assert json_data2["pagination"]["has_more"] is False
