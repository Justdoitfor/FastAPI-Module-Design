import pytest

from app.core.config import settings

BASE = "/api/v1/auth"
CREDENTIALS = {"email": "a@example.com", "password": "passw0rd"}


@pytest.fixture
def enable_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)


async def test_login_is_rate_limited(client, enable_rate_limit):
    await client.post(f"{BASE}/register", json=CREDENTIALS)
    for _ in range(5):
        await client.post(f"{BASE}/login", json={**CREDENTIALS, "password": "wrong"})

    resp = await client.post(f"{BASE}/login", json=CREDENTIALS)
    assert resp.status_code == 429