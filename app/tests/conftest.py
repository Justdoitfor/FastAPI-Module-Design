import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # 保证所有 Model 在 create_all() 之前被加载
from app.db.base import Base
from app.domains.auth.models import User
from app.domains.auth.security import hash_password


async def _make_user(session_factory, email: str) -> User:
    async with session_factory() as session:
        user = User(email=email, hashed_password=hash_password("passw0rd"))
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def user(session_factory) -> User:
    return await _make_user(session_factory, "owner@example.com")


@pytest_asyncio.fixture
async def other_user(session_factory) -> User:
    return await _make_user(session_factory, "other@example.com")


@pytest_asyncio.fixture
async def client(session_factory):  # 不再依赖 user，也不再覆盖 get_current_user
    from httpx import ASGITransport, AsyncClient

    from app.api.deps import get_db
    from app.main import app

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
