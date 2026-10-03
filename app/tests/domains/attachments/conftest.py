import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_db, get_redis, get_storage
from app.domains.auth.deps import get_current_user
from app.main import app


@pytest_asyncio.fixture
async def client(session_factory, redis, storage, user):
    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def task_id(client) -> int:
    return (await client.post("/api/v1/tasks", json={"title": "t"})).json()["id"]
