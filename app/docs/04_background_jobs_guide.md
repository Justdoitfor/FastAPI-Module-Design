# FastAPI 生产级模块实战 ④：后台任务与队列

> 前置：①`domains/auth`、②`domains/tasks`、③`app/cache`（含 Redis 客户端）均已就绪。
> 本篇引入 arq 队列，新增两个业务域：`domains/reminders`（到期提醒，纯后台功能，无对外 API）与 `domains/exports`（异步导出 CSV，API + Worker 两侧协作），并建立独立的 Worker 进程入口。

---

## 0. 本篇在整体结构里的位置

```
app/
├── core/
│   ├── config.py              ← 新增 SMTP_*、EMAIL_FROM 等配置
│   └── exceptions.py           ← 新增 QueueUnavailableError
├── queue/                      ← 新增顶层包：队列是技术能力，不属于任何业务域
│   ├── client.py                # JobQueue 抽象 + arq 实现
│   └── names.py
├── integrations/                ← 新增顶层包：外部第三方服务适配器
│   └── email.py
├── domains/
│   ├── auth/                    ← 不变
│   ├── tasks/
│   │   ├── models.py            ← 新增 reminded_at 字段
│   │   ├── repository.py        ← 新增两个查询方法
│   │   └── service.py           ← update_task 新增"截止时间变更重置提醒状态"逻辑
│   ├── reminders/                ← 新增：到期提醒域（纯后台功能，无 router.py）
│   │   └── service.py
│   └── exports/                  ← 新增：导出域（API 侧 + Worker 侧协作）
│       ├── schemas.py
│       ├── service.py
│       └── router.py
├── worker/                       ← 新增顶层包：Worker 进程的入口装配
│   ├── jobs.py                    # 汇总各域暴露的任务函数，统一注册
│   ├── observability.py
│   └── settings.py
├── api/
│   └── deps.py                   ← 新增 get_job_queue
└── main.py                       ← lifespan 新增 arq 连接池
```

**判断依据**：队列是"多个域共享的技术能力"（`reminders`、`exports` 都要用它投递/查询任务），归入顶层 `app/queue/`，与 `app/cache/` 同一逻辑。`domains/reminders/` 没有 `router.py`——它是纯后台功能，从不被 HTTP 请求直接调用，域文件夹里"缺哪个文件"完全取决于这个域实际需要什么，不必凑齐一套模板。

---

## 1. 技术栈与部署形态

```bash
uv add "arq>=0.26" aiosmtplib
```

- **API 与 Worker 是同一份代码、两个进程**：`entrypoint.sh api` / `entrypoint.sh worker`（⑦篇会讲完整部署）；本篇先给出可以直接跑的两条启动命令。
- **Service 依赖 `JobQueue` 抽象，不直接 import arq**：测试换成内存 Fake，不需要真实 Redis。
- **Worker 任务函数保持"薄"**：创建会话 → 调 Service → 翻译重试语义，业务判断一行都不写在 `worker/` 里。

---

## 2. 项目级基础设施

### 2.1 配置

```python
# app/core/config.py（追加）
class Settings(BaseSettings):
    ...
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    EMAIL_FROM: str = "noreply@example.com"
```

### 2.2 异常扩展

```python
# app/core/exceptions.py（追加）
class QueueUnavailableError(AppException):
    status_code = 503
    code = "queue_unavailable"
    message = "后台任务服务暂不可用，请稍后再试"
```

### 2.3 邮件适配器

```python
# app/integrations/email.py
import asyncio
import logging
from email.message import EmailMessage
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """邮件发送失败（可重试的瞬时错误）。"""


class EmailSender(Protocol):
    async def send(self, *, to: str, subject: str, body: str) -> None: ...


class ConsoleEmailSender:
    async def send(self, *, to: str, subject: str, body: str) -> None:
        logger.info("[email] to=%s subject=%s\n%s", to, subject, body)


class SmtpEmailSender:
    async def send(self, *, to: str, subject: str, body: str) -> None:
        import aiosmtplib

        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = settings.EMAIL_FROM, to, subject
        msg.set_content(body)
        try:
            await aiosmtplib.send(
                msg, hostname=settings.SMTP_HOST, port=settings.SMTP_PORT,
                username=settings.SMTP_USER, password=settings.SMTP_PASSWORD,
                start_tls=True, timeout=10,
            )
        except (aiosmtplib.SMTPException, OSError, asyncio.TimeoutError) as exc:
            raise EmailDeliveryError(str(exc)) from exc


def build_email_sender() -> EmailSender:
    return SmtpEmailSender() if settings.SMTP_HOST else ConsoleEmailSender()
```

> `app/integrations/` 与 `app/queue/`、`app/cache/` 同一性质：外部服务的适配层，不代表任何业务概念，任何域都可能需要发邮件，因此留在项目顶层。

### 2.4 队列抽象（`app/queue/`）

```python
# app/queue/names.py
QUEUE_NAME = "app:queue"

JOB_SEND_DUE_REMINDER = "send_due_reminder"
JOB_EXPORT_TASKS = "export_tasks"
```

```python
# app/queue/client.py
import logging
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Any, Protocol

from arq.connections import ArqRedis
from arq.jobs import Job, JobStatus
from redis.exceptions import RedisError

from app.core.exceptions import QueueUnavailableError

logger = logging.getLogger(__name__)


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NOT_FOUND = "not_found"


@dataclass
class JobSnapshot:
    state: JobState
    result: Any = None


class JobQueue(Protocol):
    async def enqueue(
            self,
            function: str,
            *args: Any,
            job_id: str | None = None,
            defer_by: timedelta | None = None,
    ) -> str | None:
        ...

    async def get(
            self,
            job_id: str,
    ) -> JobSnapshot:
        ...


class ArqJobQueue:
    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    async def enqueue(
            self,
            function: str,
            *args: Any,
            job_id: str | None = None,
            defer_by: timedelta | None = None,
    ) -> str | None:
        try:
            job = await self._pool.enqueue_job(
                function,
                *args,
                _job_id=job_id,
                _defer_by=defer_by,
            )
        except RedisError as e:
            logger.error(f"enqueue failed:{function}", exc_info=True)
            raise QueueUnavailableError() from e
        return job.job_id if job else None

    async def get(self, job_id: str) -> JobSnapshot:
        try:
            job = Job(job_id, self._pool, _queue_name=self._pool.default_queue_name)
            status = await job.status()
            if status is JobStatus.not_found:
                return JobSnapshot(JobState.NOT_FOUND)
            if status is JobStatus.in_progress:
                return JobSnapshot(JobState.RUNNING)
            if status is JobStatus.complete:
                info = await job.result_info()
                if info is None:
                    return JobSnapshot(JobState.NOT_FOUND)
                state = JobState.SUCCEEDED if info.success else JobState.FAILED
                return JobSnapshot(state, info.result if info.success else None)
            return JobSnapshot(JobState.QUEUED)
        except RedisError as e:
            raise QueueUnavailableError() from e


```

```python
# app/main.py（lifespan 内新增）
from contextlib import asynccontextmanager

from fastapi import FastAPI
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.core.redis import create_redis

from arq import create_pool # 新增
from arq.connections import RedisSettings # 新增

from app.queue.names import QUEUE_NAME # 新增


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = create_redis(settings.REDIS_URL)
    # 新增
    app.state.queue_pool = await create_pool(
        RedisSettings.from_dsn(settings.REDIS_URL),
        default_queue_name=QUEUE_NAME,
    )
    yield
    await app.state.queue_pool.aclose() # 新增
    await app.state.redis.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="FastAPI-Module-Design",
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()

```

```python
# app/api/deps.py（追加）
from app.queue.client import ArqJobQueue, JobQueue


def get_job_queue(request: Request) -> JobQueue:
    return ArqJobQueue(request.app.state.queue_pool)


JobQueueDep = Annotated[JobQueue, Depends(get_job_queue)]
```

---

## 3. `domains/tasks/` 的增量修改（支撑提醒功能）

```python
# app/domains/tasks/models.py（Task 新增字段与索引）
from sqlalchemy import Index, text

class Task(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_owner_status", "owner_id", "status"),
        Index(
            "ix_tasks_due_pending", "due_date",
            postgresql_where=text("reminded_at IS NULL AND deleted_at IS NULL"),
        ),
    )
    ...
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
```

```bash
alembic revision --autogenerate -m "add reminded_at to tasks"
alembic upgrade head
```

```python
# app/domains/tasks/repository.py（追加两个查询方法）
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

from app.domains.tasks.models import Task, TaskStatus


class TaskRepository(BaseRepository[Task]):
    ...

    async def list_due_unreminded(self, *, within: timedelta, limit: int) -> list[Task]:
        now = datetime.now(timezone.utc)
        stmt = (
            self._select()
            .where(
                Task.due_date.is_not(None), Task.due_date > now,
                Task.due_date <= now + within, Task.status != TaskStatus.DONE,
                Task.reminded_at.is_(None),
            )
            .order_by(Task.due_date)
            .limit(limit)
        )
        return list((await self.session.scalars(stmt)).all())

    async def iter_by_owner(
        self, owner_id: int, *, status: TaskStatus | None = None, batch_size: int = 1000,
    ) -> AsyncIterator[list[Task]]:
        last_id = 0
        while True:
            stmt = (
                self._select()
                .where(Task.owner_id == owner_id, Task.id > last_id)
                .order_by(Task.id).limit(batch_size)
            )
            if status:
                stmt = stmt.where(Task.status == status)
            rows = list((await self.session.scalars(stmt)).all())
            if not rows:
                return
            yield rows
            last_id = rows[-1].id
```

```python
# app/domains/tasks/service.py（update_task 内新增一行）
if "due_date" in values:
    self._validate_due_date(values["due_date"])
    values["reminded_at"] = None   # 截止时间变了，重置提醒状态，允许再次提醒
```

> `iter_by_owner` 用游标分批读取，供 `exports` 域使用；`list_due_unreminded` 供 `reminders` 域使用。两个方法都定义在 `tasks` 域的 Repository 里——**因为它们查询的是 `Task` 表**，属于"谁的数据"决定"放哪个域"的判断标准，即便调用方是别的域。

---

## 4. `domains/reminders/`：到期提醒（纯后台功能）

```
app/domains/reminders/
├── __init__.py
└── service.py
```

没有 `models.py`（复用 `tasks` 域的 `Task` 表）、没有 `schemas.py`（不对外暴露接口）、没有 `router.py`（无 HTTP 入口）。这个域唯一的产出物是给 Worker 用的任务函数。

```python
# app/domains/reminders/service.py
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.email import EmailSender
from app.domains.auth.repository import UserRepository
from app.domains.tasks.models import TaskStatus
from app.domains.tasks.repository import TaskRepository
from app.queue.client import JobQueue
from app.queue.names import JOB_SEND_DUE_REMINDER

logger = logging.getLogger(__name__)

REMINDER_WINDOW = timedelta(hours=1)
SCAN_BATCH = 500


class ReminderService:
    def __init__(self, session: AsyncSession, email: EmailSender, queue: JobQueue) -> None:
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
            subject=f"任务即将到期：{task.title}",
            body=f"你的任务「{task.title}」将于 {task.due_date:%Y-%m-%d %H:%M} (UTC) 到期，请尽快处理。",
        )
        await self.tasks.update(task, {"reminded_at": datetime.now(timezone.utc)})
        await self.session.commit()
        return "sent"
```

> `from app.domains.tasks.repository import TaskRepository` 与 `from app.domains.auth.repository import UserRepository`——`reminders` 域同时依赖了 `tasks` 和 `auth` 两个域。方向依然单向：`reminders → tasks`、`reminders → auth`，反过来 `tasks`/`auth` 都不会 import `reminders` 的任何东西。这类"纯后台的编排型域"依赖多个业务域是正常现象，只要方向不形成环路即可。

---

## 5. `domains/exports/`：异步导出（API 侧 + Worker 侧协作）

```
app/domains/exports/
├── __init__.py
├── schemas.py
├── service.py    # 包含 ExportService（API 侧）与 ExportBuilder（Worker 侧）
└── router.py
```

```python
# app/domains/exports/schemas.py
from typing import Literal

from pydantic import BaseModel

from app.domains.tasks.models import TaskStatus

ExportStatus = Literal["queued", "running", "succeeded", "failed"]


class ExportRequest(BaseModel):
    status: TaskStatus | None = None


class ExportJobRead(BaseModel):
    job_id: str
    status: ExportStatus
    download_url: str | None = None
```

```python
# app/domains/exports/service.py
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


```

> `ExportService` 和 `ExportBuilder` 同放一个文件——它们是"同一个导出功能的两个视角"（API 侧发起/查询，Worker 侧真正生成），紧密相关，拆成两个文件反而增加跳转成本。`from app.domains.tasks.repository import TaskRepository` 是 `exports → tasks` 的单向依赖，同样合规。

```python
# app/domains/exports/router.py
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import FileResponse

from app.api.deps import JobQueueDep
from app.api.rate_limit import rate_limit, user_identifier
from app.core.config import settings
from app.domains.auth.deps import ActiveUser
from app.domains.exports.schemas import ExportJobRead, ExportRequest
from app.domains.exports.service import ExportService

router = APIRouter(prefix="/exports", tags=["exports"])


def get_export_service(queue: JobQueueDep) -> ExportService:
    return ExportService(queue, Path(settings.EXPORT_DIR))


ServiceDep = Annotated[ExportService, Depends(get_export_service)]


@router.post("/tasks", response_model=ExportJobRead, status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(rate_limit(limit=3, window=3600, scope="export", identifier=user_identifier))])
async def create_task_export(
        body: ExportRequest,
        request: Request,
        response: Response,
        user: ActiveUser,
        service: ServiceDep,
):
    job_id = await service.request_export(user, body.status)
    response.headers["Location"] = str(request.url_for("get_export", job_id=job_id))
    return ExportJobRead(job_id=job_id, status="queued")


@router.get("/{job_id}", response_model=ExportJobRead, name="get_export")
async def get_export(job_id: str, request: Request, user: ActiveUser, service: ServiceDep):
    job_status = await service.get_export(user, job_id)
    url = (str(request.url_for("download_export", job_id=job_id)) if job_status == "succeeded" else None)
    return ExportJobRead(job_id=job_id, status=job_status, download_url=url)

@router.get("/{job_id}/download", name="download_export")
async def download_export(job_id: str, user: ActiveUser, service: ServiceDep):
    path = await service.get_file(user, job_id)
    return FileResponse(path, media_type="text/csv", filename="tasks.csv")

```

```python
# app/core/config.py（追加）
class Settings(BaseSettings):
    ...
    EXPORT_DIR: str = "./exports"
```

```python
# app/api/v1/router.py（追加一行）
from app.domains.exports.router import router as exports_router

api_router.include_router(exports_router)
```

---

## 6. Worker 进程入口

```python
# app/worker/observability.py
import functools
from arq.worker import Retry


def instrumented_job(fn):
    """占位，后续接入结构化日志，指标，链路追踪。这里统一入口形态"""

    @functools.wraps(fn)
    async def wrapper(ctx: dict, *args, **kwargs):
        return await fn(ctx, *args, **kwargs)

    return wrapper

```

```python
# app/domains/reminders/service.py（末尾追加任务函数，作为该域的 Worker 侧公开入口）
from app.queue.client import ArqJobQueue
from app.worker.observability import instrumented_job


@instrumented_job
async def scan_due_tasks(ctx: dict) -> int:
    async with ctx["session_factory"]() as session:
        service = ReminderService(session, ctx["email"], ArqJobQueue(ctx["redis"]))
        return await service.scan_and_enqueue()


@instrumented_job
async def send_due_reminder(ctx: dict, task_id: int) -> str:
    from arq.worker import Retry

    async with ctx["session_factory"]() as session:
        service = ReminderService(session, ctx["email"], ArqJobQueue(ctx["redis"]))
        try:
            return await service.send_reminder(task_id)
        except EmailDeliveryError as exc:
            attempt = ctx["job_try"]
            if attempt >= 5:
                raise
            raise Retry(defer=attempt**2 * 10) from exc
```

```python
# app/domains/exports/service.py（末尾追加任务函数）
from app.worker.observability import instrumented_job


@instrumented_job
async def export_tasks(ctx: dict, user_id: int, status: str | None = None) -> str:
    async with ctx["session_factory"]() as session:
        builder = ExportBuilder(session, Path("./exports"))
        return await builder.build(
            user_id=user_id,
            status=TaskStatus(status) if status else None,
            job_id=ctx["job_id"],
        )
```

> **任务函数定义在各自域内，而不是塞进 `worker/jobs.py`**——这与"Router 定义在域内、`api/v1/router.py` 只做汇总"是完全对称的设计。`worker/jobs.py` 的职责只是收集：

```python
# app/worker/jobs.py —— 汇总各域暴露的任务函数，供 WorkerSettings 统一注册
from app.domains.exports.service import export_tasks
from app.domains.reminders.service import scan_due_tasks, send_due_reminder

ALL_JOBS = [send_due_reminder, scan_due_tasks, export_tasks]
```

```python
# app/worker/settings.py
from arq import cron, func
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.integrations.email import build_email_sender
from app.queue.names import QUEUE_NAME
from app.worker.jobs import ALL_JOBS
from app.domains.reminders.service import scan_due_tasks


async def startup(ctx: dict) -> None:
    engine = create_async_engine(
        settings.DATABASE_URL, pool_size=5, max_overflow=5, pool_pre_ping=True
    )
    ctx["engine"] = engine
    ctx["session_factory"] = async_sessionmaker(engine, expire_on_commit=False)
    ctx["email"] = build_email_sender()

    from app.core.redis import create_redis
    ctx["redis"] = create_redis(settings.REDIS_URL)


async def shutdown(ctx: dict) -> None:
    await ctx["redis"].aclose()
    await ctx["engine"].dispose()


class WorkerSettings:
    functions = [func(job, max_tries=5) for job in ALL_JOBS]
    cron_jobs = [cron(scan_due_tasks, minute=set(range(0, 60, 5)), run_at_startup=False)]

    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    queue_name = QUEUE_NAME
    on_startup = startup
    on_shutdown = shutdown

    max_jobs = 10
    job_timeout = 300
    keep_result = 86400
    health_check_interval = 30
```

```bash
uvicorn app.main:app --reload    # 终端 1：API
arq app.worker.settings.WorkerSettings   # 终端 2：Worker
```

---

## 7. 测试

```
tests/
└── domains/
    ├── reminders/test_reminders.py
    └── exports/test_exports.py
```

```python
# tests/conftest.py（追加）
import pytest_asyncio
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from fakeredis import FakeAsyncRedis

import app.models  # 保证所有 Model 在 create_all() 之前被加载
from app.core.config import settings
from app.db.base import Base
from app.domains.auth.models import User
from app.domains.auth.security import hash_password

from app.integrations.email import EmailDeliveryError
from app.queue.client import JobSnapshot, JobState


async def _make_user(session_factory, email: str) -> User:
    async with session_factory() as session:
        user = User(email=email, hashed_password=hash_password("passw0rd"))
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest.fixture(autouse=True)
def _disable_rate_limit(monkeypatch):
    # 默认关闭限流：②里"连续创建 25 个任务"的用例会被 20 次/分钟的限制拦住
    # 限流相关测试再通过 打开
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def redis():
    r = FakeAsyncRedis(decode_responses=True)
    yield r
    await r.flushall()
    await r.aclose()


@pytest_asyncio.fixture
async def user(session_factory) -> User:
    return await _make_user(session_factory, "owner@example.com")


@pytest_asyncio.fixture
async def other_user(session_factory) -> User:
    return await _make_user(session_factory, "other@example.com")

@pytest.fixture
def enable_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)

@pytest_asyncio.fixture
async def client(session_factory, redis):  # 不再依赖 user，也不再覆盖 get_current_user
    from httpx import ASGITransport, AsyncClient

    from app.api.deps import get_db, get_redis
    from app.main import app

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = lambda: redis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class FakeJobQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, tuple, str]] = []
        self.snapshots: dict[str, JobSnapshot] = {}

    async def enqueue(self, function, *args, job_id=None, defer_by=None):
        from uuid import uuid4
        if job_id and job_id in self.snapshots:
            return None
        job_id = job_id or uuid4().hex
        self.enqueued.append((function, args, job_id))
        self.snapshots[job_id] = JobSnapshot(JobState.QUEUED)
        return job_id

    async def get(self, job_id: str) -> JobSnapshot:
        return self.snapshots.get(job_id, JobSnapshot(JobState.NOT_FOUND))


class FakeEmailSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.fail = False

    async def send(self, *, to: str, subject: str, body: str) -> None:
        if self.fail:
            raise EmailDeliveryError("smtp down")
        self.sent.append((to, subject, body))


@pytest_asyncio.fixture
async def job_queue() -> FakeJobQueue:
    return FakeJobQueue()


@pytest_asyncio.fixture
async def email() -> FakeEmailSender:
    return FakeEmailSender()



# 在 client fixture 中补充：app.dependency_overrides[get_job_queue] = lambda: job_queue
```

```python
# tests/domains/exports/conftest.py
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_db, get_job_queue, get_redis
from app.core.config import settings
from app.domains.auth.deps import get_current_user
from app.main import app


@pytest.fixture
def export_dir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(settings, "EXPORT_DIR", str(tmp_path))
    return tmp_path


@pytest_asyncio.fixture
async def client(session_factory, redis, job_queue, user):
    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_job_queue] = lambda: job_queue
    app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
```

```python
# tests/domains/reminders/test_reminders.py
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
        assert await service.scan_and_enqueue() == 0   # 重复扫描：job_id 去重


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
```

```python
# tests/domains/exports/test_exports.py
import pytest

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


async def test_export_builder_writes_safe_csv(session_factory, user, tmp_path):
    await _add_tasks(
        session_factory,
        Task(title="normal", owner_id=user.id),
        Task(title='=HYPERLINK("http://evil")', owner_id=user.id),
    )
    async with session_factory() as s:
        rel = await ExportBuilder(s, tmp_path).build(
            user_id=user.id, status=None, job_id="export:1:abc"
        )
    content = (tmp_path / rel).read_text(encoding="utf-8-sig")
    assert "normal" in content
    assert "'=HYPERLINK" in content
    assert not list(tmp_path.rglob("*.tmp"))


async def test_export_flow(client, job_queue, user, export_dir):
    resp = await client.post(f"{BASE}/tasks", json={})
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    assert job_id.startswith(f"export:{user.id}:")

    assert (await client.get(f"{BASE}/{job_id}")).json()["status"] == "queued"

    rel = f"{user.id}/{job_id.replace(':', '_')}.csv"
    file = export_dir / rel
    file.parent.mkdir(parents=True)
    file.write_text("id,title\n1,a\n", encoding="utf-8")
    job_queue.snapshots[job_id] = JobSnapshot(JobState.SUCCEEDED, rel)

    body = (await client.get(f"{BASE}/{job_id}")).json()
    assert body["status"] == "succeeded"

    dl = await client.get(f"{BASE}/{job_id}/download")
    assert dl.status_code == 200


async def test_cannot_access_others_export(client, other_user):
    job_id = (await client.post(f"{BASE}/tasks", json={})).json()["job_id"]
    app.dependency_overrides[get_current_user] = lambda: other_user
    assert (await client.get(f"{BASE}/{job_id}")).status_code == 404
```

```bash
pytest tests/domains/ -v
```

---
