import pytest_asyncio
import structlog
from httpx import ASGITransport, AsyncClient
from prometheus_client import REGISTRY
from structlog.testing import capture_logs

from app.api.deps import get_db, get_redis, get_storage
from app.core.logging import redact_sensitive
from app.domains.auth.deps import get_current_user
from app.main import app
from app.queue.client import ArqJobQueue
from app.worker.observability import instrumented_job


@pytest_asyncio.fixture
async def authed_client(session_factory, redis, user,storage):
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

def sample(name: str, labels: dict) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_request_id_generated_echoed_and_sanitized(client):
    resp = await client.get("/api/v1/tasks")
    assert len(resp.headers["X-Request-ID"]) == 32

    resp = await client.get("/api/v1/tasks", headers={"X-Request-ID": "upstream-abc-12345"})
    assert resp.headers["X-Request-ID"] == "upstream-abc-12345"

    resp = await client.get("/api/v1/tasks", headers={"X-Request-ID": "bad id!"})
    assert resp.headers["X-Request-ID"] != "bad id!"


async def test_probe_paths_are_not_logged(client):
    with capture_logs() as logs:
        await client.get("/livez")
    assert not [e for e in logs if e["event"] == "http_request"]


async def test_http_metrics_use_route_template(authed_client):
    task_id = (await authed_client.post("/api/v1/tasks", json={"title": "t"})).json()["id"]
    labels = {"method": "GET", "route": "/api/v1/tasks/{task_id}", "status": "200"}
    before = sample("http_requests_total", labels)
    await authed_client.get(f"/api/v1/tasks/{task_id}")
    assert sample("http_requests_total", labels) == before + 1


async def test_cache_metrics_from_tasks_domain(authed_client):
    task_id = (await authed_client.post("/api/v1/tasks", json={"title": "t"})).json()["id"]
    hit = {"name": "task_detail", "result": "hit"}
    miss = {"name": "task_detail", "result": "miss"}
    h0, m0 = sample("cache_requests_total", hit), sample("cache_requests_total", miss)

    await authed_client.get(f"/api/v1/tasks/{task_id}")
    await authed_client.get(f"/api/v1/tasks/{task_id}")

    assert sample("cache_requests_total", miss) == m0 + 1
    assert sample("cache_requests_total", hit) == h0 + 1


@app.get("/__boom", include_in_schema=False)
async def _boom():
    raise RuntimeError("secret internal detail")


async def test_unhandled_exception_is_safe_and_traceable():
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/__boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "internal_error"
    assert "secret internal detail" not in resp.text
    assert body["request_id"] == resp.headers["X-Request-ID"]


async def test_livez_and_readyz(authed_client):
    assert (await authed_client.get("/livez")).status_code == 200
    resp = await authed_client.get("/readyz")
    assert resp.status_code == 200 and resp.json()["status"] == "ok"


async def test_readyz_degraded_when_redis_down_but_still_ready(authed_client, redis, monkeypatch):
    async def down(*a, **k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(redis, "ping", down)
    resp = await authed_client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


async def test_readyz_unavailable_when_database_down(authed_client):
    class BrokenSession:
        async def execute(self, *a, **k):
            raise ConnectionError("db down")

    async def broken_db():
        yield BrokenSession()

    app.dependency_overrides[get_db] = broken_db
    resp = await authed_client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["status"] == "unavailable"


async def test_instrumented_job_records_success_and_binds_context():
    @instrumented_job
    async def sample_job(ctx, x):
        return x * 2

    labels = {"job_name": "sample_job", "status": "succeeded"}
    before = sample("jobs_total", labels)

    with capture_logs() as logs:
        result = await sample_job({"job_id": "j1", "job_try": 1}, 21, obs_ctx={"request_id": "req-1"})

    assert result == 42
    assert sample("jobs_total", labels) == before + 1
    assert [e["event"] for e in logs] == ["job_started", "job_finished"]


async def test_enqueue_propagates_request_id():
    captured = {}

    class FakePool:
        async def enqueue_job(self, function, *args, **kwargs):
            captured.update(kwargs)
            from types import SimpleNamespace
            return SimpleNamespace(job_id="j3")

    structlog.contextvars.bind_contextvars(request_id="req-xyz")
    try:
        await ArqJobQueue(FakePool()).enqueue("export_tasks", 1)
    finally:
        structlog.contextvars.clear_contextvars()

    assert captured["obs_ctx"]["request_id"] == "req-xyz"


def test_redact_sensitive_fields():
    event = redact_sensitive(None, "info", {"event": "login", "password": "hunter2", "user_id": 1})
    assert event["password"] == "***"
    assert event["user_id"] == 1