import pytest

from app.core.config import settings
from app.domains.tasks.repository import TaskRepository

URL = "/api/v1/tasks"


@pytest.fixture
def db_calls(monkeypatch):
    counter = {"n": 0}
    original = TaskRepository.get

    async def spy(self, obj_id):
        counter["n"] += 1
        return await original(self, obj_id)

    monkeypatch.setattr(TaskRepository, "get", spy)
    return counter


async def test_detail_is_cached(client, db_calls):
    task_id = (await client.post(URL, json={"title": "t"})).json()["id"]
    await client.get(f"{URL}/{task_id}")
    await client.get(f"{URL}/{task_id}")
    assert db_calls["n"] == 1


async def test_detail_invalidated_on_update(client):
    task_id = (await client.post(URL, json={"title": "旧标题"})).json()["id"]
    await client.get(f"{URL}/{task_id}")
    await client.patch(f"{URL}/{task_id}", json={"title": "新标题"})
    resp = await client.get(f"{URL}/{task_id}")
    assert resp.json()["title"] == "新标题"


async def test_list_invalidated_on_create(client):
    assert (await client.get(URL)).json()["total"] == 0
    await client.post(URL, json={"title": "t"})
    assert (await client.get(URL)).json()["total"] == 1


async def test_cache_hit_still_checks_owner(client, other_user):
    from app.domains.auth.deps import get_current_user
    from app.main import app

    task_id = (await client.post(URL, json={"title": "私有"})).json()["id"]
    await client.get(f"{URL}/{task_id}")

    app.dependency_overrides[get_current_user] = lambda: other_user
    assert (await client.get(f"{URL}/{task_id}")).status_code == 404