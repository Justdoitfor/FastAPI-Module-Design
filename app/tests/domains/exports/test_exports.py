import pytest
from rsa import key

from app.domains.auth.deps import get_current_user
from app.domains.exports.service import ExportBuilder
from app.domains.tasks.models import Task
from app.main import app
from app.queue.client import JobSnapshot, JobState

BASE = "/api/v1/exports"


async def _add_tasks(session_factory, *tasks: Task) -> None:
    async with session_factory() as s:
        s.add_all(tasks)
        await s.commit()


async def test_export_builder_writes_safe_csv(session_factory, user, storage):
    await _add_tasks(
        session_factory,
        Task(title="normal", owner_id=user.id),
        Task(title='=HYPERLINK("http://evil")', owner_id=user.id),
    )
    async with session_factory() as s:
        key = await ExportBuilder(s, storage).build(
            user_id=user.id, status=None, job_id="export:1:abc"
        )
    content = storage.objects[key][0].decode(encoding="utf-8-sig")
    assert "normal" in content
    assert "'=HYPERLINK" in content


async def test_export_flow(client, job_queue, user, storage):
    resp = await client.post(f"{BASE}/tasks", json={})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    assert job_id.startswith(f"export:{user.id}:")

    assert (await client.get(f"{BASE}/{job_id}")).json()["status"] == "queued"

    key = f"exports/{user.id}/{job_id.replace(':', '_')}.csv"
    storage.objects[key] = (b"id,title\n1,a\n", "text/csv")
    job_queue.snapshots[job_id] = JobSnapshot(JobState.SUCCEEDED, key)

    body = (await client.get(f"{BASE}/{job_id}")).json()
    assert body["status"] == "succeeded"

    dl = await client.get(f"{BASE}/{job_id}/download")
    assert dl.status_code == 200
    assert dl.json()["url"].startswith("https://files.test/exports/")


async def test_cannot_access_others_export(client, other_user):
    job_id = (await client.post(f"{BASE}/tasks", json={})).json()["job_id"]
    app.dependency_overrides[get_current_user] = lambda: other_user
    assert (await client.get(f"{BASE}/{job_id}")).status_code == 404