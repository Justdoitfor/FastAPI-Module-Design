import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_db, get_redis
from app.domains.auth.deps import get_current_user
from app.main import app


@pytest_asyncio.fixture
async def client(session_factory, redis, user):
    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_current_user] = lambda: user  # 只对 tasks 目录生效
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
