import asyncio
from sqlalchemy import select
from infra.database import init_db, get_db, AsyncSession
from apps.auth.models.user import User

async def main():
    await init_db()
    from infra.database import async_session_factory
    async with async_session_factory() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()
        print(f"Total users: {len(users)}")
        for u in users:
            print(f"ID: {u.id}, Username: {u.username}, Email: {u.email}, HashedPassword: {u.hashed_password}, IsActive: {u.is_active}")

if __name__ == "__main__":
    asyncio.run(main())
