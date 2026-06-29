import pytest
import pytest_asyncio
from fastapi import APIRouter, Depends
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from main import app
from infra.database import get_db
from infra.config import settings
from infra.redis_client import redis_client
from models.user import User
from models.role import Role
from services.auth_service import auth_service
from api.middleware.auth import require_authority, get_current_user
from api.responses import success_response

# ─────────────────────────────────────────────────────────────
# Test Router Setup for RBAC verification
# ─────────────────────────────────────────────────────────────

auth_test_router = APIRouter(prefix="/api/v1/test-rbac", tags=["Test RBAC"])

@auth_test_router.get("/admin-only")
async def admin_only(user: User = Depends(require_authority(["1"]))):
    return success_response({"msg": f"Welcome Admin {user.username}"})

@auth_test_router.get("/user-only")
async def user_only(user: User = Depends(require_authority(["2"]))):
    return success_response({"msg": f"Welcome User {user.username}"})

@auth_test_router.get("/any-role")
async def any_role(user: User = Depends(require_authority(["1", "2"]))):
    return success_response({"msg": f"Welcome {user.username}"})

# Include test router into the app for testing
app.include_router(auth_test_router)


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
    # Create test engine and sessionmaker bound to the current event loop with NullPool
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
        # Override get_db dependency
        async def override_get_db():
            yield session
        
        app.dependency_overrides[get_db] = override_get_db
        yield session
        app.dependency_overrides.clear()
        
        # Cleanup test users
        result = await session.execute(
            select(User).where(User.username.like("test_auth_user_%"))
        )
        test_users = result.scalars().all()
        for u in test_users:
            await session.delete(u)
        await session.commit()

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_user_registration_and_login(client, db_session):
    """Test user registration, login, and token generation."""
    # 1. Register a new user
    reg_data = {
        "username": "test_auth_user_john",
        "email": "test_auth_user_john@example.com",
        "password": "securepassword123"
    }
    response = await client.post("/api/v1/auth/register", json=reg_data)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["username"] == "test_auth_user_john"
    assert data["data"]["email"] == "test_auth_user_john@example.com"
    assert "hashed_password" not in data["data"]  # Password hash must be hidden

    # Check database record and role
    result = await db_session.execute(
        select(User)
        .options(selectinload(User.role))
        .where(User.username == "test_auth_user_john")
    )
    db_user = result.scalar_one_or_none()
    assert db_user is not None
    assert db_user.role is not None
    assert db_user.role.authority == "2"  # Default is 'User'

    # 2. Duplicate registration attempt
    response = await client.post("/api/v1/auth/register", json=reg_data)
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]

    # 3. Login
    login_data = {
        "username_or_email": "test_auth_user_john",
        "password": "securepassword123"
    }
    response = await client.post("/api/v1/auth/login", json=login_data)
    assert response.status_code == 200
    login_res = response.json()["data"]
    assert "access_token" in login_res
    assert "refresh_token" in login_res
    assert login_res["user"]["username"] == "test_auth_user_john"


@pytest.mark.asyncio
async def test_token_refresh_and_blacklist(client, db_session):
    """Test Dual-token refresh rotation and blacklist enforcement."""
    # Register & Login
    reg_data = {
        "username": "test_auth_user_jane",
        "email": "test_auth_user_jane@example.com",
        "password": "securepassword123"
    }
    await client.post("/api/v1/auth/register", json=reg_data)
    
    login_data = {
        "username_or_email": "test_auth_user_jane@example.com",
        "password": "securepassword123"
    }
    login_res = (await client.post("/api/v1/auth/login", json=login_data)).json()["data"]
    access_token = login_res["access_token"]
    refresh_token = login_res["refresh_token"]

    # 1. Use refresh token to get new tokens
    refresh_response = await client.post(
        "/api/v1/auth/refresh-token",
        json={"refresh_token": refresh_token}
    )
    assert refresh_response.status_code == 200
    refresh_res = refresh_response.json()["data"]
    assert "access_token" in refresh_res
    assert "refresh_token" in refresh_res
    
    new_access_token = refresh_res["access_token"]
    new_refresh_token = refresh_res["refresh_token"]

    # 2. Reuse the old refresh token (should be blacklisted now)
    reuse_response = await client.post(
        "/api/v1/auth/refresh-token",
        json={"refresh_token": refresh_token}
    )
    assert reuse_response.status_code == 401
    assert "revoked" in reuse_response.json()["detail"]


@pytest.mark.asyncio
async def test_logout_and_session_invalidation(client, db_session):
    """Test user logout and immediate token invalidation via last_logout."""
    # Register & Login
    reg_data = {
        "username": "test_auth_user_bob",
        "email": "test_auth_user_bob@example.com",
        "password": "securepassword123"
    }
    await client.post("/api/v1/auth/register", json=reg_data)
    
    login_data = {
        "username_or_email": "test_auth_user_bob",
        "password": "securepassword123"
    }
    login_res = (await client.post("/api/v1/auth/login", json=login_data)).json()["data"]
    access_token = login_res["access_token"]

    # Verify we can query /me with this token
    headers = {"Authorization": f"Bearer {access_token}"}
    me_response = await client.get("/api/v1/auth/me", headers=headers)
    assert me_response.status_code == 200
    assert me_response.json()["data"]["username"] == "test_auth_user_bob"

    # Logout
    logout_response = await client.post("/api/v1/auth/logout", headers=headers)
    assert logout_response.status_code == 200

    # Verify the token is now invalid (since it was blacklisted and last_logout updated)
    me_after_logout = await client.get("/api/v1/auth/me", headers=headers)
    assert me_after_logout.status_code == 401


@pytest.mark.asyncio
async def test_rbac_access_control(client, db_session):
    """Test Role-Based Access Control on endpoint authorizations."""
    # 1. Create a user
    reg_data = {
        "username": "test_auth_user_alice",
        "email": "test_auth_user_alice@example.com",
        "password": "securepassword123"
    }
    await client.post("/api/v1/auth/register", json=reg_data)
    
    login_data = {
        "username_or_email": "test_auth_user_alice",
        "password": "securepassword123"
    }
    login_res = (await client.post("/api/v1/auth/login", json=login_data)).json()["data"]
    access_token = login_res["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    # Alice is role User (authority '2')
    # Can access /user-only and /any-role
    res_user_only = await client.get("/api/v1/test-rbac/user-only", headers=headers)
    assert res_user_only.status_code == 200
    assert "Welcome User" in res_user_only.json()["data"]["msg"]

    res_any_role = await client.get("/api/v1/test-rbac/any-role", headers=headers)
    assert res_any_role.status_code == 200

    # Cannot access /admin-only (authority '1')
    res_admin_only = await client.get("/api/v1/test-rbac/admin-only", headers=headers)
    assert res_admin_only.status_code == 403
    assert "insufficient permissions" in res_admin_only.json()["detail"]

    # 2. Promote Alice to Admin (authority '1') directly in DB
    result = await db_session.execute(
        select(User).where(User.username == "test_auth_user_alice")
    )
    alice = result.scalar_one()
    
    # Get Admin role
    from models.role import Role
    admin_role = (await db_session.execute(
        select(Role).where(Role.authority == "1")
    )).scalar_one()
    
    alice.role_id = admin_role.id
    await db_session.commit()
    db_session.expire_all()

    # Get new token (needed to update claims with new roles_id)
    login_res = (await client.post("/api/v1/auth/login", json=login_data)).json()["data"]
    new_access_token = login_res["access_token"]
    new_headers = {"Authorization": f"Bearer {new_access_token}"}

    # Now Alice can access /admin-only
    res_admin_only_new = await client.get("/api/v1/test-rbac/admin-only", headers=new_headers)
    assert res_admin_only_new.status_code == 200
    assert "Welcome Admin" in res_admin_only_new.json()["data"]["msg"]

    # Alice (Admin, authority '1') cannot access /user-only (authority '2') anymore
    res_user_only_new = await client.get("/api/v1/test-rbac/user-only", headers=new_headers)
    assert res_user_only_new.status_code == 403
