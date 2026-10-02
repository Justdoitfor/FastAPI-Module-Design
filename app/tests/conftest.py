import pytest_asyncio
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from fakeredis import FakeAsyncRedis

import app.models  # 保证所有 Model 在 create_all() 之前被加载
from app.core.config import settings
from app.db.base import Base
from app.domains.auth.models import User
from app.domains.auth.security import hash_password

from app.integrations.email import EmailDeliveryError
from app.queue.client import JobSnapshot, JobState


async def _make_user(session_factory, email: str) -> User:
    async with session_factory() as session:
        user = User(email=email, hashed_password=hash_password("passw0rd"))
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest.fixture(autouse=True)
def _disable_rate_limit(monkeypatch):
    # 默认关闭限流：②里"连续创建 25 个任务"的用例会被 20 次/分钟的限制拦住
    # 限流相关测试再通过 打开
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)


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
async def redis():
    r = FakeAsyncRedis(decode_responses=True)
    yield r
    await r.flushall()
    await r.aclose()


@pytest_asyncio.fixture
async def user(session_factory) -> User:
    return await _make_user(session_factory, "owner@example.com")


@pytest_asyncio.fixture
async def other_user(session_factory) -> User:
    return await _make_user(session_factory, "other@example.com")

@pytest.fixture
def enable_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)

@pytest_asyncio.fixture
async def client(session_factory, redis):  # 不再依赖 user，也不再覆盖 get_current_user
    from httpx import ASGITransport, AsyncClient

    from app.api.deps import get_db, get_redis
    from app.main import app

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = lambda: redis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class FakeJobQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, tuple, str]] = []
        self.snapshots: dict[str, JobSnapshot] = {}

    async def enqueue(self, function, *args, job_id=None, defer_by=None):
        from uuid import uuid4
        if job_id and job_id in self.snapshots:
            return None
        job_id = job_id or uuid4().hex
        self.enqueued.append((function, args, job_id))
        self.snapshots[job_id] = JobSnapshot(JobState.QUEUED)
        return job_id

    async def get(self, job_id: str) -> JobSnapshot:
        return self.snapshots.get(job_id, JobSnapshot(JobState.NOT_FOUND))


class FakeEmailSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.fail = False

    async def send(self, *, to: str, subject: str, body: str) -> None:
        if self.fail:
            raise EmailDeliveryError("smtp down")
        self.sent.append((to, subject, body))


@pytest_asyncio.fixture
async def job_queue() -> FakeJobQueue:
    return FakeJobQueue()


@pytest_asyncio.fixture
async def email() -> FakeEmailSender:
    return FakeEmailSender()
