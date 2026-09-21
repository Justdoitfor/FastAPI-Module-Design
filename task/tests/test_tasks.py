from datetime import datetime, timedelta, timezone

from auth.api.user import get_current_user
from task.main import app

URL = "/api/v1/tasks"


async def test_create_and_get_task(client):
    resp = await client.post(URL, json={"title": "写教程", "priority": 3})
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "写教程"
    assert body["status"] == "todo"

    resp = await client.get(f"{URL}/{body['id']}")
    assert resp.status_code == 200


async def test_due_date_in_past_is_rejected(client):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    resp = await client.post(URL, json={"title": "x", "due_date": past})
    assert resp.status_code == 422
    assert resp.json()["code"] == "business_rule_violation"


async def test_pagination_and_filter(client):
    for i in range(25):
        await client.post(URL, json={"title": f"task-{i}"})

    resp = await client.get(URL, params={"page": 2, "size": 10})
    data = resp.json()
    assert data["total"] == 25
    assert data["pages"] == 3
    assert len(data["items"]) == 10

    resp = await client.get(URL, params={"keyword": "task-1"})
    assert resp.json()["total"] == 11  # task-1, task-10 ~ task-19


async def test_cannot_access_others_task(client, other_user):
    task_id = (await client.post(URL, json={"title": "私有"})).json()["id"]

    # 切换为另一个用户
    app.dependency_overrides[get_current_user] = lambda: other_user
    resp = await client.get(f"{URL}/{task_id}")
    assert resp.status_code == 404  # 不是 403，避免泄露资源存在性


async def test_soft_delete(client):
    task_id = (await client.post(URL, json={"title": "待删除"})).json()["id"]

    assert (await client.delete(f"{URL}/{task_id}")).status_code == 204
    assert (await client.get(f"{URL}/{task_id}")).status_code == 404
    assert (await client.get(URL)).json()["total"] == 0


async def test_patch_rejects_null_title(client):
    task_id = (await client.post(URL, json={"title": "t"})).json()["id"]
    resp = await client.patch(f"{URL}/{task_id}", json={"title": None})
    assert resp.status_code == 422