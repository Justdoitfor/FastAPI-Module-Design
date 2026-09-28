import pytest

from app.core.config import settings

URL = "/api/v1/tasks"


@pytest.fixture
def enable_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)


async def test_create_is_rate_limited(client, enable_rate_limit):
    for _ in range(20):
        assert (await client.post(URL, json={"title": "x"})).status_code == 201

    resp = await client.post(URL, json={"title": "x"})
    assert resp.status_code == 429
    assert resp.json()["code"] == "rate_limited"
    assert int(resp.headers["Retry-After"]) >= 1