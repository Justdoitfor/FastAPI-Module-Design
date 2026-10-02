import csv
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from app.core.exceptions import ConflictError, NotFoundError
from app.domains.auth.models import User
from app.domains.tasks.models import Task, TaskStatus
from app.domains.tasks.repository import TaskRepository
from app.domains.exports.schemas import ExportStatus
from app.queue.names import JOB_EXPORT_TASKS
from app.queue.client import JobQueue, JobState, JobSnapshot
from app.worker.observability import instrumented_job

_STATE_TO_STATUS: dict[JobState, ExportStatus] = {
    JobState.QUEUED: "queued",
    JobState.RUNNING: "running",
    JobState.FAILED: "failed",
    JobState.SUCCEEDED: "succeeded",
}


class ExportService:
    def __init__(self, queue: JobQueue, export_dir: Path):
        self.queue = queue
        self.export_dir = export_dir

    async def request_export(self, user: User, status: TaskStatus | None) -> str:
        job_id = f"export:{user.id}:{uuid4().hex}"
        await self.queue.enqueue(
            JOB_EXPORT_TASKS,
            user.id,
            status.value if status else None,
            job_id=job_id,
        )
        return job_id

    async def get_export(self, user: User, job_id: str) -> ExportStatus:
        snapshot = await self._snapshot(user, job_id)
        return _STATE_TO_STATUS[snapshot.state]

    async def get_file(self, user: User, job_id: str) -> Path:
        snapshot = await self._snapshot(user, job_id)
        if snapshot.state is not JobState.SUCCEEDED:
            raise ConflictError("导出文件尚未生成")

        user_dir = (self.export_dir / str(user.id)).resolve()
        path = (self.export_dir / snapshot.result).resolve()
        if not path.is_relative_to(user_dir) or not path.is_file():
            raise NotFoundError("导出文件已过期")
        return path

    async def _snapshot(self, user: User, job_id: str) -> JobSnapshot:
        if not job_id.startswith(f"export:{user.id}"):
            raise NotFoundError("导出任务不存在或已过期")
        snapshot = await self.queue.get(job_id)
        if snapshot.state is JobState.NOT_FOUND:
            raise NotFoundError("导出任务不存在或已过期")
        return snapshot


_HEADERS = ["id", "title", "description", "status", "priority", "due_date", "created_at"]


def _safe_cell(value: object) -> str:
    text = str(value) if value is not None else ""
    # 标准防CSV注入：在开头加单引号前缀，Excel会当成纯文本
    if text.startswith(("=", "+", "-", "@")):
        text = "'" + text
    return text


class ExportBuilder:
    def __init__(self, session: AsyncSession, export_dir: Path):
        self.tasks = TaskRepository(session)
        self.export_dir = export_dir

    async def build(self, *, user_id: int, status: TaskStatus | None, job_id: str) -> str:
        rel = Path(str(user_id)) / f"{job_id.replace(":", "_")}.csv"
        final = self.export_dir / rel
        tmp = final.with_suffix(".csv.tmp")
        final.parent.mkdir(parents=True, exist_ok=True)

        with tmp.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(_HEADERS)
            async for batch in self.tasks.iter_by_owner(user_id, status=status):
                writer.writerows(self._row(t) for t in batch)
        tmp.replace(final)
        return rel.as_posix()

    @staticmethod
    def _row(t: Task) -> list[str]:
        return [
            _safe_cell(v) for v in (
                t.id,
                t.title,
                t.description,
                t.status.value,
                t.priority,
                t.due_date,
                t.created_at,
            )
        ]


@instrumented_job
async def export_tasks(ctx: dict, user_id: int, status: str | None = None) -> str:
    async with ctx["session_factory"]() as session:
        builder = ExportBuilder(session, Path("./exports"))
        return await builder.build(
            user_id=user_id,
            status=TaskStatus(status) if status else None,
            job_id=ctx["job_id"],
        )
