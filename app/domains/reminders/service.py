import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from app.domains.auth.repository import UserRepository
from app.domains.tasks.models import TaskStatus
from app.domains.tasks.repository import TaskRepository
from app.integrations.email import EmailSender, EmailDeliveryError
from app.queue.client import JobQueue, ArqJobQueue
from app.queue.names import JOB_SEND_DUE_REMINDER
from app.worker.observability import instrumented_job

logger = logging.getLogger(__name__)

REMINDER_WINDOW = timedelta(hours=1)
SCAN_BATCH = 500


class ReminderService:
    def __init__(self, session: AsyncSession, email: EmailSender, queue: JobQueue):
        self.session = session
        self.email = email
        self.queue = queue
        self.tasks = TaskRepository(session)
        self.users = UserRepository(session)

    async def scan_and_enqueue(self) -> int:
        tasks = await self.tasks.list_due_unreminded(within=REMINDER_WINDOW, limit=SCAN_BATCH)
        enqueued = 0
        for task in tasks:
            job_id = f"reminder:{task.id}:{int(task.due_date.timestamp())}"
            if await self.queue.enqueue(JOB_SEND_DUE_REMINDER, task.id, job_id=job_id):
                enqueued += 1
        return enqueued

    async def send_reminder(self, task_id: int) -> str:
        task = await self.tasks.get(task_id)
        if task is None or task.status == TaskStatus.DONE or task.reminded_at is not None:
            return "skipped"
        user = await self.users.get(task.owner_id)
        if user is None:
            return "skipped"
        await self.email.send(
            to=user.email,
            subject=f"任务即将到期:{task.title}",
            body=f"你的任务 {task.title} 将于 {task.due_date:%Y-%m-%d %H:%M:%S}(UTC) 到期，请尽快处理。",
        )
        await self.tasks.update(task, {"reminded_at": datetime.now(timezone.utc)})
        await self.session.commit()
        return "sent"


@instrumented_job
async def scan_due_tasks(ctx: dict) -> int:
    async with ctx["session_factory"]() as session:
        service = ReminderService(session=session, email=ctx["email"], queue=ArqJobQueue(ctx["redis"]))
        return await service.scan_and_enqueue()


@instrumented_job
async def send_due_reminder(ctx: dict, task_id: int) -> str:
    from arq.worker import Retry
    async with ctx["session_factory"]() as session:
        service = ReminderService(session=session, email=ctx["email"], queue=ArqJobQueue(ctx["redis"]))
        try:
            return await service.send_reminder(task_id)
        except EmailDeliveryError as e:
            attempt = ctx["job_retry"]
            if attempt >= 5:
                raise
            raise Retry(defer=attempt ** 2 * 10) from e

