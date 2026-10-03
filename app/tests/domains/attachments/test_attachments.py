from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from app.domains.auth.deps import get_current_user
from app.domains.attachments.models import Attachment
from app.domains.attachments.service import AttachmentService
from app.main import app

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"0" * 64
EXE = b"MZ\x90\x00" + b"\x00" * 64


@pytest.fixture
async def task_id(client) -> int:
    return (await client.post("/api/v1/tasks", json={"title": "t"})).json()["id"]


def url(task_id: int, suffix: str = "") -> str:
    return f"/api/v1/tasks/{task_id}/attachments{suffix}"


async def test_upload_png(client, task_id, storage):
    resp = await client.post(url(task_id), files={"file": ("photo.png", PNG, "image/png")})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "ready" and body["content_type"] == "image/png"
    key = next(iter(storage.objects))
    assert key.startswith("attachments/") and "photo" not in key


async def test_disguised_file_is_rejected(client, task_id, storage):
    resp = await client.post(url(task_id), files={"file": ("evil.png", EXE, "image/png")})
    assert resp.status_code == 415
    assert storage.objects == {}


async def test_quota_per_task(client, task_id):
    for _ in range(10):
        assert (await client.post(url(task_id), files={"file": ("a.png", PNG, "image/png")})).status_code == 201
    resp = await client.post(url(task_id), files={"file": ("a.png", PNG, "image/png")})
    assert resp.status_code == 422


async def test_direct_upload_flow(client, task_id, storage):
    init = (await client.post(url(task_id, "/uploads"),
                              json={"filename": "doc.pdf", "content_type": "application/pdf"})).json()
    key = init["fields"]["key"]

    assert (await client.post(url(task_id, f"/{init['attachment_id']}/complete"))).status_code == 409

    storage.objects[key] = (PDF, "application/pdf")
    resp = await client.post(url(task_id, f"/{init['attachment_id']}/complete"))
    assert resp.status_code == 200 and resp.json()["status"] == "ready"


async def test_direct_upload_content_mismatch_is_purged(client, task_id, storage):
    init = (await client.post(url(task_id, "/uploads"),
                              json={"filename": "a.png", "content_type": "image/png"})).json()
    key = init["fields"]["key"]
    storage.objects[key] = (EXE, "image/png")

    resp = await client.post(url(task_id, f"/{init['attachment_id']}/complete"))
    assert resp.status_code == 415
    assert key not in storage.objects


async def test_download_returns_presigned_url(client, task_id):
    att = (await client.post(url(task_id), files={"file": ("a.png", PNG, "image/png")})).json()
    resp = await client.get(url(task_id, f"/{att['id']}/download"))
    assert resp.status_code == 200
    assert resp.json()["url"].startswith("https://files.test/attachments/")


async def test_cannot_access_others_attachment(client, task_id, other_user):
    att = (await client.post(url(task_id), files={"file": ("a.png", PNG, "image/png")})).json()
    app.dependency_overrides[get_current_user] = lambda: other_user
    assert (await client.get(url(task_id, f"/{att['id']}/download"))).status_code == 404
    assert (await client.get(url(task_id))).status_code == 404


async def test_cleanup_removes_stale_pending_and_old_deleted(client, task_id, storage, session_factory):
    init = (await client.post(url(task_id, "/uploads"),
                              json={"filename": "a.png", "content_type": "image/png"})).json()
    storage.objects[init["fields"]["key"]] = (PNG, "image/png")
    att = (await client.post(url(task_id), files={"file": ("b.png", PNG, "image/png")})).json()
    await client.delete(url(task_id, f"/{att['id']}"))

    async with session_factory() as s:
        await s.execute(update(Attachment).where(Attachment.id == init["attachment_id"])
                        .values(created_at=datetime.now(timezone.utc) - timedelta(hours=2)))
        await s.execute(update(Attachment).where(Attachment.id == att["id"])
                        .values(deleted_at=datetime.now(timezone.utc) - timedelta(days=8)))
        await s.commit()

    async with session_factory() as s:
        result = await AttachmentService(s, storage).cleanup()
    assert result == {"stale_pending": 1, "purged": 1}
    assert storage.objects == {}
