from datetime import datetime, timedelta, timezone

import pytest

from app.domains.auth.models import User
from app.domains.exports.service import ExportBuilder
from app.domains.reminders.service import ReminderService
from app.domains.tasks.models import Task, TaskStatus
from app.domains.tasks.repository import TaskRepository
from app.integrations.email import EmailDeliveryError


async def _add_tasks(session_factory, *tasks: Task) -> list[int]:
    async with session_factory() as s:
        s.add_all(tasks)
        await s.commit()
        return [t.id for t in tasks]


async def test_scan_enqueues_only_pending_due_soon(session_factory, user, job_queue, email):
    now = datetime.now(timezone.utc)
    soon = now + timedelta(minutes=30)
    await _add_tasks(
        session_factory,
        Task(title="soon", owner_id=user.id, due_date=soon),
        Task(title="far", owner_id=user.id, due_date=now + timedelta(days=3)),
        Task(title="done", owner_id=user.id, due_date=soon, status=TaskStatus.DONE),
        Task(title="no-due", owner_id=user.id),
    )
    async with session_factory() as s:
        service = ReminderService(s, email, job_queue)
        assert await service.scan_and_enqueue() == 1
        assert await service.scan_and_enqueue() == 0  # 重复扫描：job_id 去重


async def test_send_reminder_is_idempotent(session_factory, user, job_queue, email):
    (task_id,) = await _add_tasks(
        session_factory,
        Task(title="t", owner_id=user.id, due_date=datetime.now(timezone.utc) + timedelta(minutes=10)),
    )
    async with session_factory() as s:
        service = ReminderService(s, email, job_queue)
        assert await service.send_reminder(task_id) == "sent"
        assert await service.send_reminder(task_id) == "skipped"
    assert len(email.sent) == 1


async def test_email_failure_keeps_task_pending(session_factory, user, job_queue, email):
    (task_id,) = await _add_tasks(
        session_factory,
        Task(title="t", owner_id=user.id, due_date=datetime.now(timezone.utc) + timedelta(minutes=10)),
    )
    email.fail = True
    async with session_factory() as s:
        with pytest.raises(EmailDeliveryError):
            await ReminderService(s, email, job_queue).send_reminder(task_id)

    async with session_factory() as s:
        task = await TaskRepository(s).get(task_id)
        assert task.reminded_at is None
