import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from auth.api.user import get_current_user
from db.session import get_db
from db.base import Base
from task.main import app
from auth.models.user import User


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,  # 内存库必须共用同一连接
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _make_user(session_factory, username: str, email: str) -> User:
    async with session_factory() as session:
        user = User(username=username,email=email, password_hash="x")  # 按你的 User 模型调整字段
        session.add(user)
        await session.commit()
        return user


@pytest_asyncio.fixture
async def user(session_factory) -> User:
    return await _make_user(session_factory, "user1","a@example.com")


@pytest_asyncio.fixture
async def other_user(session_factory) -> User:
    return await _make_user(session_factory, "user2","b@example.com")


@pytest_asyncio.fixture
async def client(session_factory, user):
    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()