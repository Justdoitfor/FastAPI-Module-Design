from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_db, get_job_queue, get_redis
from app.core.config import settings
from app.domains.auth.deps import get_current_user
from app.main import app


@pytest.fixture
def export_dir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(settings, "EXPORT_DIR", str(tmp_path))
    return tmp_path


@pytest_asyncio.fixture
async def client(session_factory, redis, job_queue, user):
    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_job_queue] = lambda: job_queue
    app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()