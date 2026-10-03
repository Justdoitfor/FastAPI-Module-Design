# FastAPI 生产级模块实战 ⑤：文件上传与对象存储

> 前置：①`domains/auth`、②`domains/tasks`、③`app/cache`、④`app/queue` + `domains/reminders` + `domains/exports` 均已就绪。
> 本篇引入对象存储，新增 `domains/attachments`（任务附件），并把④的导出功能从本地磁盘迁移到对象存储。

---

## 0. 本篇在整体结构里的位置

```
app/
├── core/
│   ├── config.py               ← 新增 S3_* 配置
│   └── exceptions.py            ← 新增 PayloadTooLargeError / UnsupportedMediaTypeError
├── storage/                     ← 👈 新增顶层包：对象存储是技术能力，不属于任何业务域
│   ├── base.py                   # ObjectStorage 协议
│   └── s3.py                     # aioboto3 实现
├── domains/
│   ├── auth/                     ← 不变
│   ├── tasks/                     ← 新增 get_owned（③已用到，此处补充说明）
│   ├── reminders/                 ← 不变
│   ├── attachments/               ← 👈 新增：任务附件域，依赖 tasks 与 storage
│   │   ├── models.py
│   │   ├── schemas.py
│   │   ├── repository.py
│   │   ├── service.py
│   │   └── router.py
│   └── exports/                   ← 本篇修改：本地目录 → 对象存储
│       └── service.py
├── worker/
│   ├── jobs.py                    ← 追加 cleanup_attachments
│   └── settings.py                 ← lifespan 新增 storage 创建/销毁 + 新 cron
├── api/
│   ├── deps.py                     ← 新增 get_storage
│   └── v1/router.py                 ← 追加 attachments 路由
└── main.py                          ← lifespan 新增 S3Storage 创建（AsyncExitStack）
```

**判断依据**：对象存储与 `app/cache/`、`app/queue/` 同一逻辑——多个域共享的技术能力，独立成顶层包。`domains/attachments/` 是本系列第一个**同时依赖两个基础设施包（`storage/`）和一个业务域（`tasks/`）** 的域：附件的归属校验要问"这个任务是不是我的"（依赖 `tasks`），附件的存储要问"文件放哪、怎么签发下载链接"（依赖 `storage`）。依赖方向依然单向：`attachments → tasks`、`attachments → storage`，`tasks` 和 `storage` 都不会反过来依赖 `attachments`。

---

## 1. 技术栈与核心设计

```bash
uv add aioboto3 filetype python-multipart
```

- 数据库只存元数据（对象键、文件名、大小、类型、状态），字节在对象存储里。
- 两种上传路径：**服务端中转**（一个接口，适合小文件）与**预签名直传**（init → 客户端直传 → complete，文件不经过 API）。
- 安全模型：对象键服务端生成，文件类型以魔数嗅探为准，桶保持私有，下载走限时预签名链接。
- 附件状态机 `PENDING → READY`，配合定时清理（复用④的 Worker cron）。

---

## 2. 项目级基础设施

### 2.1 配置

```python
# app/core/config.py（追加）
class Settings(BaseSettings):
    ...
    S3_ENDPOINT_URL: str | None = None
    S3_PUBLIC_ENDPOINT_URL: str | None = None
    S3_REGION: str = "us-east-1"
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_KEY: str | None = None
    S3_BUCKET: str = "app-files"
    # EXPORT_DIR 不再需要，本篇会移除对它的依赖
```

### 2.2 异常扩展

```python
# app/core/exceptions.py（追加）
class PayloadTooLargeError(AppException):
    status_code = 413
    code = "payload_too_large"
    message = "文件过大"


class UnsupportedMediaTypeError(AppException):
    status_code = 415
    code = "unsupported_media_type"
    message = "不支持的文件类型"
```

### 2.3 对象存储抽象（`app/storage/`）

```python
# app/storage/base.py
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol


@dataclass(frozen=True, slots=True)
class ObjectMeta:
    size: int
    content_type: str


@dataclass(frozen=True, slots=True)
class PresignedPost:
    url: str
    fields: dict[str, str]


class ObjectStorage(Protocol):
    async def upload_fileobj(self, key: str, fileobj: BinaryIO, *, content_type: str) -> None: ...
    async def upload_file(self, key: str, path: Path, *, content_type: str) -> None: ...
    async def head(self, key: str) -> ObjectMeta | None: ...
    async def read_head(self, key: str, size: int = 2048) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def presign_get(self, key: str, *, expires: int, filename: str | None = None) -> str: ...
    async def presign_post(
        self, key: str, *, content_type: str, max_size: int, expires: int
    ) -> PresignedPost: ...
```

```python
# app/storage/s3.py
from contextlib import AsyncExitStack
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

import aioboto3
from aiobotocore.config import AioConfig
from botocore.exceptions import ClientError

from app.core.config import settings
from app.storage.base import ObjectMeta, PresignedPost


class S3Storage:
    def __init__(self, client, signer, bucket: str) -> None:
        self._client = client   # 内部地址：真实读写
        self._signer = signer   # 公网地址：仅用于生成预签名
        self._bucket = bucket

    @classmethod
    async def create(cls, stack: AsyncExitStack) -> "S3Storage":
        session = aioboto3.Session()
        config = AioConfig(
            signature_version="s3v4", s3={"addressing_style": "path"},
            connect_timeout=3, read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
        )
        common = dict(
            region_name=settings.S3_REGION,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            config=config,
        )
        client = await stack.enter_async_context(
            session.client("s3", endpoint_url=settings.S3_ENDPOINT_URL, **common)
        )
        signer = await stack.enter_async_context(
            session.client(
                "s3", endpoint_url=settings.S3_PUBLIC_ENDPOINT_URL or settings.S3_ENDPOINT_URL, **common
            )
        )
        return cls(client, signer, settings.S3_BUCKET)

    async def upload_fileobj(self, key: str, fileobj: BinaryIO, *, content_type: str) -> None:
        await self._client.upload_fileobj(
            fileobj, self._bucket, key, ExtraArgs={"ContentType": content_type}
        )

    async def upload_file(self, key: str, path: Path, *, content_type: str) -> None:
        await self._client.upload_file(
            str(path), self._bucket, key, ExtraArgs={"ContentType": content_type}
        )

    async def head(self, key: str) -> ObjectMeta | None:
        try:
            resp = await self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return ObjectMeta(size=resp["ContentLength"], content_type=resp.get("ContentType", ""))

    async def read_head(self, key: str, size: int = 2048) -> bytes:
        resp = await self._client.get_object(
            Bucket=self._bucket, Key=key, Range=f"bytes=0-{size - 1}"
        )
        async with resp["Body"] as body:
            return await body.read()

    async def delete(self, key: str) -> None:
        await self._client.delete_object(Bucket=self._bucket, Key=key)

    async def presign_get(self, key: str, *, expires: int, filename: str | None = None) -> str:
        params = {"Bucket": self._bucket, "Key": key}
        if filename:
            params["ResponseContentDisposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
        return await self._signer.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=expires
        )

    async def presign_post(
        self, key: str, *, content_type: str, max_size: int, expires: int
    ) -> PresignedPost:
        resp = await self._signer.generate_presigned_post(
            Bucket=self._bucket, Key=key, Fields={"Content-Type": content_type},
            Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, max_size]],
            ExpiresIn=expires,
        )
        return PresignedPost(url=resp["url"], fields=resp["fields"])
```

```python
# app/main.py（lifespan 内新增；用 AsyncExitStack 统一管理资源生命周期）
from contextlib import AsyncExitStack, asynccontextmanager

from app.storage.s3 import S3Storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        app.state.redis = create_redis(settings.REDIS_URL)
        stack.push_async_callback(app.state.redis.aclose)

        app.state.queue_pool = await create_pool(
            RedisSettings.from_dsn(settings.REDIS_URL), default_queue_name=QUEUE_NAME
        )
        stack.push_async_callback(app.state.queue_pool.aclose)

        app.state.storage = await S3Storage.create(stack)
        yield
```

```python
# app/api/deps.py（追加）
from app.storage.base import ObjectStorage


def get_storage(request: Request) -> ObjectStorage:
    return request.app.state.storage


StorageDep = Annotated[ObjectStorage, Depends(get_storage)]
```

---

## 3. `domains/tasks/` 的一处小补充

```python
# app/domains/tasks/repository.py
async def get_owned(self, task_id: int, owner_id: int) -> Task | None:
    stmt = self._select().where(Task.id == task_id, Task.owner_id == owner_id)
    return (await self.session.execute(stmt)).scalar_one_or_none()
```

`attachments` 域会通过它校验"这个附件所属的任务是否属于当前用户"——附件继承任务的权限，而不是自己再实现一套归属判断逻辑。

---

## 4. `domains/attachments/`

```
app/domains/attachments/
├── __init__.py
├── models.py
├── schemas.py
├── repository.py
├── service.py
└── router.py
```

### Step 1：Model

```python
# app/domains/attachments/models.py
import enum

from sqlalchemy import BigInteger, Enum, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SoftDeleteMixin, TimestampMixin


class AttachmentStatus(str, enum.Enum):
    PENDING = "pending"
    READY = "ready"


class Attachment(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "attachments"
    __table_args__ = (
        Index(
            "ix_attachments_pending_created", "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # 跨域外键：直接引用 tasks 域的表名 "tasks"，与 Task.owner_id 引用 "users" 是同一种做法
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    object_key: Mapped[str] = mapped_column(String(512), unique=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[AttachmentStatus] = mapped_column(
        Enum(AttachmentStatus, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e]),
        default=AttachmentStatus.PENDING,
    )
```

```python
# app/models/__init__.py（追加一行）
from app.domains.attachments.models import Attachment  # noqa: F401
```

```bash
alembic revision --autogenerate -m "add attachments"
alembic upgrade head
```

### Step 2：Schemas

```python
# app/domains/attachments/schemas.py
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domains.attachments.models import AttachmentStatus


class AttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    filename: str
    content_type: str
    size: int
    status: AttachmentStatus
    created_at: datetime


class UploadInitRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(max_length=100)


class UploadInitResponse(BaseModel):
    attachment_id: int
    url: str
    fields: dict[str, str]
    expires_in: int
```

```python
# app/schemas/common.py（追加：DownloadLink 是跨域通用的响应结构，attachments 和 exports 都会用）
class DownloadLink(BaseModel):
    url: str
    expires_in: int
```

### Step 3：Repository

```python
# app/domains/attachments/repository.py
from datetime import datetime

from sqlalchemy import func, select

from app.db.repository import BaseRepository
from app.domains.attachments.models import Attachment, AttachmentStatus


class AttachmentRepository(BaseRepository[Attachment]):
    model = Attachment

    async def get_in_task(self, attachment_id: int, task_id: int) -> Attachment | None:
        stmt = self._select().where(Attachment.id == attachment_id, Attachment.task_id == task_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_ready_by_task(self, task_id: int) -> list[Attachment]:
        stmt = (
            self._select()
            .where(Attachment.task_id == task_id, Attachment.status == AttachmentStatus.READY)
            .order_by(Attachment.id)
        )
        return list((await self.session.scalars(stmt)).all())

    async def count_by_task(self, task_id: int) -> int:
        stmt = (
            select(func.count()).select_from(Attachment)
            .where(Attachment.task_id == task_id, Attachment.deleted_at.is_(None))
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def list_stale_pending(self, *, before: datetime, limit: int) -> list[Attachment]:
        stmt = (
            self._select()
            .where(Attachment.status == AttachmentStatus.PENDING, Attachment.created_at < before)
            .limit(limit)
        )
        return list((await self.session.scalars(stmt)).all())

    async def list_purgeable(self, *, before: datetime, limit: int) -> list[Attachment]:
        stmt = (
            self._select(include_deleted=True)
            .where(Attachment.deleted_at.is_not(None), Attachment.deleted_at < before)
            .limit(limit)
        )
        return list((await self.session.scalars(stmt)).all())

```

### Step 4：Service

```python
# app/domains/attachments/service.py
import logging
import os
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePath
from typing import BinaryIO
from uuid import uuid4

import filetype
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BusinessError,
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError
)

from app.domains.attachments.models import Attachment, AttachmentStatus
from app.domains.attachments.repository import AttachmentRepository
from app.domains.attachments.schemas import AttachmentRead, UploadInitRequest, UploadInitResponse
from app.domains.auth.models import User
from app.domains.tasks.models import Task
from app.domains.tasks.repository import TaskRepository
from app.schemas.common import DownloadLink
from app.storage.base import ObjectStorage
from app.worker.observability import instrumented_job

logger = logging.getLogger(__name__)

ALLOWED_TYPES = {
    "image/png": ".png", "image/jpeg": ".jpg",
    "image/webp": ".webp", "application/pdf": ".pdf",
}
MAX_UPLOAD_SIZE = 10 * 1024 * 1024
MAX_ATTACHMENTS_PER_TASK = 10
SNIFF_BYTES = 2048
UPLOAD_URL_TTL = 600
DOWNLOAD_URL_TTL = 300
PENDING_TTL = timedelta(hours=1)
DELETED_RETENTION = timedelta(days=7)
CLEANUP_BATCH = 200


@dataclass
class IncomingFile:
    fileobj: BinaryIO
    filename: str


def sanitize_filename(name: str) -> str:
    name = PurePath(name.replace("\\", "/")).name
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = name.strip().strip(".")[:255]
    return name or "file"


class AttachmentService:
    def __init__(self, session: AsyncSession, storage: ObjectStorage):
        self.session = session
        self.storage = storage
        self.tasks = TaskRepository(session)
        self.attachments = AttachmentRepository(session)

    async def upload(self, user: User, task_id: int, file: IncomingFile) -> AttachmentRead:
        task = await self._get_owned_task(user, task_id)
        await self._ensure_quota(task.id)
        size = self._measure(file.fileobj)
        if size == 0:
            raise BusinessError("文件为空")
        if size > MAX_UPLOAD_SIZE:
            raise PayloadTooLargeError(f"文件大小不能超过 {MAX_UPLOAD_SIZE // 1024 // 1024} MB")
        head = file.fileobj.read(SNIFF_BYTES)
        file.fileobj.seek(0)
        mime = self._sniff(head)

        key = self._make_key(user.id, task_id, mime)
        await self.storage.upload_fileobj(key, file.fileobj, content_type=mime)
        try:
            attachment = await self.attachments.create(
                Attachment(
                    task_id=task_id,
                    owner_id=user.id,
                    object_key=key,
                    filename=sanitize_filename(file.filename),
                    content_type=mime,
                    size=size,
                    status=AttachmentStatus.READY,
                )
            )
            await self.session.commit()
        except Exception:
            await self._delete_object_quietly(key)
            raise
        return AttachmentRead.model_validate(attachment)

    async def init_upload(
            self,
            user: User,
            task_id: int,
            data: UploadInitRequest
    ) -> UploadInitResponse:
        task = await self._get_owned_task(user, task_id)
        await self._ensure_quota(task.id)
        if data.content_type not in ALLOWED_TYPES:
            raise UnsupportedMediaTypeError("仅支持 PNG / JPEG / WebP / PDF 类型文件")
        key = self._make_key(user.id, task_id, data.content_type)
        attachment = await self.attachments.create(
            Attachment(
                task_id=task_id,
                owner_id=user.id,
                object_key=key,
                filename=sanitize_filename(data.filename),
                content_type=data.content_type,
            )
        )
        await self.session.commit()

        post = await self.storage.presign_post(
            key,
            content_type=data.content_type,
            max_size=MAX_UPLOAD_SIZE,
            expires=UPLOAD_URL_TTL,
        )
        return UploadInitResponse(
            attachment_id=attachment.id,
            url=post.url,
            fields=post.fields,
            expires_in=UPLOAD_URL_TTL,
        )

    async def complete_upload(
            self,
            user: User,
            task_id: int,
            attachment_id: int,
    ) -> AttachmentRead:
        attachment = await self._get_owned_attachment(user, task_id, attachment_id)
        if attachment.status is AttachmentStatus.READY:
            return AttachmentRead.model_validate(attachment)
        meta = await self.storage.head(attachment.object_key)
        if meta is None:
            raise ConflictError("文件尚未上传完成")
        if meta.size == 0 or meta.size > MAX_UPLOAD_SIZE:
            await self._reject(attachment, PayloadTooLargeError("文件大小不合法"))

        head = await self.storage.read_head(attachment.object_key, SNIFF_BYTES)
        try:
            mime = self._sniff(head)
        except UnsupportedMediaTypeError as e:
            await self._reject(attachment, e)
            raise

        if mime != attachment.content_type:
            await self._reject(attachment, UnsupportedMediaTypeError("文件内容与声明的类型不符"))

        attachment = await self.attachments.update(
            attachment,
            {"size": meta.size, "status": AttachmentStatus.READY},
        )
        await self.session.commit()
        return AttachmentRead.model_validate(attachment)

    async def list_attachments(self, user: User, task_id: int) -> list[AttachmentRead]:
        await self._get_owned_task(user, task_id)
        rows = await self.attachments.list_ready_by_task(task_id)
        return [AttachmentRead.model_validate(r) for r in rows]

    async def get_download(self, user: User, task_id: int, attachment_id: int) -> DownloadLink:
        attachment = await self._get_owned_attachment(user, task_id, attachment_id)
        if attachment.status is not AttachmentStatus.READY:
            raise NotFoundError("附件不存在")
        url = await self.storage.presign_get(
            attachment.object_key,
            expires=DOWNLOAD_URL_TTL,
            filename=attachment.filename,
        )
        return DownloadLink(url=url, expires_in=DOWNLOAD_URL_TTL)

    async def delete(self, user: User, task_id: int, attachment_id: int) -> None:
        attachment = await self._get_owned_attachment(user, task_id, attachment_id)
        await self.attachments.soft_delete(attachment)
        await self.session.commit()

    async def cleanup(self) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        stale = await self.attachments.list_stale_pending(before=now - PENDING_TTL, limit=CLEANUP_BATCH)
        purge = await self.attachments.list_purgeable(before=now - DELETED_RETENTION, limit=CLEANUP_BATCH)

        for attachment in [*stale, *purge]:
            await self.storage.delete(attachment.object_key)
            await self.attachments.hard_delete(attachment)
        await self.session.commit()
        return {"stale_pending": len(stale), "purged": len(purge)}

    async def _get_owned_task(self, user: User, task_id: int) -> Task:
        task = await self.tasks.get_owned(task_id, user.id)
        if task is None:
            raise NotFoundError("任务不存在")
        return task

    async def _get_owned_attachment(self, user: User, task_id: int, attachment_id: int) -> Attachment:
        await self._get_owned_task(user, task_id)
        attachment = await self.attachments.get_in_task(attachment_id, task_id)
        if attachment is None:
            raise NotFoundError("附件不存在")
        return attachment

    async def _ensure_quota(self, task_id: int) -> None:
        if await self.attachments.count_by_task(task_id) >= MAX_ATTACHMENTS_PER_TASK:
            raise BusinessError(f"每个任务允许最多 {MAX_ATTACHMENTS_PER_TASK} 个附件")

    async def _reject(self, attachment: Attachment, exc: Exception) -> None:
        await self._delete_object_quietly(attachment.object_key)
        await self.attachments.soft_delete(attachment)
        await self.session.commit()
        raise exc

    @staticmethod
    def _measure(fileobj: BinaryIO) -> int:
        pos = fileobj.tell()
        fileobj.seek(0, os.SEEK_END)
        size = fileobj.tell()
        fileobj.seek(pos)
        return size

    async def _delete_object_quietly(self, key: str) -> None:
        try:
            await self.storage.delete(key)
        except Exception:
            logger.warning(f"failed to delete object {key}, will be cleaned later", exc_info=True)

    @staticmethod
    def _sniff(head: bytes) -> str:
        mime = filetype.guess_mime(head)
        if mime not in ALLOWED_TYPES:
            raise UnsupportedMediaTypeError("仅支持 PNG / JPEG / WebP / PDF 类型文件")
        return mime

    @staticmethod
    def _make_key(owner_id: int, task_id: int, mime: str) -> str:
        return f"attachments/{owner_id}/{task_id}/{uuid4().hex}{ALLOWED_TYPES[mime]}"


@instrumented_job
async def cleanup_attachments(ctx: dict) -> dict:
    async with ctx["session_factory"]() as session:
        return await AttachmentService(session, ctx["storage"]).cleanup()

```

> `from app.domains.tasks.repository import TaskRepository`——`attachments` 域依赖 `tasks` 域的 Repository，用来做归属校验（`_get_owned_task`），这是本系列第一处"业务域依赖业务域"而不是"业务域依赖 auth"的例子。`attachments` 从未 import `tasks` 的 Service 或 Router，只用到它的 Repository（数据访问能力）——依赖粒度尽量小，减少两个域之间的耦合面。

### Step 5：Router

```python
# app/domains/attachments/router.py
from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbDep, StorageDep
from app.api.rate_limit import rate_limit, user_identifier
from app.domains.auth.deps import ActiveUser
from app.domains.attachments.schemas import AttachmentRead, UploadInitResponse, UploadInitRequest
from app.domains.attachments.service import AttachmentService, IncomingFile
from app.schemas.common import DownloadLink


def get_attachment_service(session: DbDep, storage: StorageDep) -> AttachmentService:
    return AttachmentService(session, storage)


ServiceDep = Annotated[AttachmentService, Depends(get_attachment_service)]

router = APIRouter(
    prefix="/tasks/{task_id}/attachments",
    tags=["attachments"],
    dependencies=[Depends(rate_limit(limit=60, window=60, scope="attachments", identifier=user_identifier))],
)


@router.post("", response_model=AttachmentRead, status_code=status.HTTP_201_CREATED)
async def upload_attachment(task_id: int, file: UploadFile, user: ActiveUser, service: ServiceDep):
    return await service.upload(user, task_id, IncomingFile(file.file, file.filename or "file"))


@router.post("/uploads", response_model=UploadInitResponse, status_code=status.HTTP_201_CREATED)
async def init_upload(task_id: int, data: UploadInitRequest, user: ActiveUser, service: ServiceDep):
    return await service.init_upload(user, task_id, data)


@router.post("/{attachment_id}/complete", response_model=AttachmentRead)
async def complete_upload(task_id: int, attachment_id: int, user: ActiveUser,
                          service: ServiceDep):
    return await service.complete_upload(user, task_id, attachment_id)


@router.get("", response_model=list[AttachmentRead])
async def list_attachments(task_id: int, user: ActiveUser, service: ServiceDep):
    return await service.list_attachments(user, task_id)


@router.get("/{attachment_id}/download", response_model=DownloadLink)
async def download_attachment(task_id: int, attachment_id: int, user: ActiveUser, service: ServiceDep):
    return await service.get_download(user, task_id, attachment_id)


@router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(task_id: int, attachment_id: int, user: ActiveUser, service: ServiceDep):
    await service.delete(user, task_id, attachment_id)

```

```python
# app/api/v1/router.py（追加一行）
from app.domains.attachments.router import router as attachments_router

api_router.include_router(attachments_router)
```

---

## 5. `domains/exports/`：迁移到对象存储

```python
# app/domains/exports/service.py（ExportService/ExportBuilder 改用 ObjectStorage）
import csv
import tempfile
from pathlib import Path

from app.storage.base import ObjectStorage
from app.schemas.common import DownloadLink

DOWNLOAD_URL_TTL = 300

class ExportService:
    def __init__(self, queue: JobQueue, storage: ObjectStorage):
        self.queue = queue
        self.storage = storage

    async def get_download_url(self, user: User, job_id: str) -> DownloadLink:
        snapshot = await self._snapshot(user, job_id)
        if snapshot.state is not JobState.SUCCEEDED:
            raise ConflictError("导出文件尚未生成")

        key = snapshot.result
        if not key.startswith(f"exports/{user.id}/"):
            raise NotFoundError("导出文件不存在")
        url = await self.storage.presign_get(key, expires=DOWNLOAD_URL_TTL, filename="tasks.csv")
        return DownloadLink(url=url, expires_in=DOWNLOAD_URL_TTL)


class ExportBuilder:
    def __init__(self, session: AsyncSession, storage: ObjectStorage):
        self.tasks = TaskRepository(session)
        self.storage = storage

    async def build(self, *, user_id: int, status: TaskStatus | None, job_id: str) -> str:
        key = f"exports/{user_id}/{job_id.replace(":", "_")}.csv"

        tmp = tempfile.NamedTemporaryFile(
            "w",
            newline="",
            encoding="utf-8-sig",
            suffix=".csv",
            delete=False,
        )
        tmp_path = Path(tmp.name)
        try:
            writer = csv.writer(tmp)
            writer.writerow(_HEADERS)
            async for batch in self.tasks.iter_by_owner(user_id, status=status):
                writer.writerows(self._row(t) for t in batch)
            tmp.close()
            await self.storage.upload_file(key, tmp_path, content_type="text/csv; charset=utf-8")
        finally:
            tmp_path.unlink(missing_ok=True)

        return key
```

```python
# app/domains/exports/service.py（任务函数：export_tasks 改传 storage）
@instrumented_job
async def export_tasks(ctx: dict, user_id: int, status: str | None = None) -> str:
    async with ctx["session_factory"]() as session:
        builder = ExportBuilder(session, ctx["storage"])
        return await builder.build(
            user_id=user_id,
            status=TaskStatus(status) if status else None,
            job_id=ctx["job_id"],
        )
```

```python
# app/domains/exports/router.py（download 改为返回预签名链接）
from app.api.deps import JobQueueDep, StorageDep


def get_export_service(queue: JobQueueDep, storage: StorageDep) -> ExportService:
    return ExportService(queue, storage)


@router.get("/{job_id}/download", response_model=DownloadLink, name="download_export")
async def download_export(job_id: str, user: ActiveUser, service: ServiceDep):
    return await service.get_download_url(user, job_id)
```

```python
# app/worker/settings.py（startup 内新增 storage；追加清理任务的 cron）
from contextlib import AsyncExitStack

from app.storage.s3 import S3Storage


async def startup(ctx: dict) -> None:
    engine = create_async_engine(settings.DATABASE_URL, pool_size=5, max_overflow=5, pool_pre_ping=True)
    ctx["engine"] = engine
    ctx["session_factory"] = async_sessionmaker(engine, expire_on_commit=False)
    ctx["email"] = build_email_sender()

    from app.core.redis import create_redis
    ctx["redis"] = create_redis(settings.REDIS_URL)

    ctx["stack"] = AsyncExitStack()
    ctx["storage"] = await S3Storage.create(ctx["stack"])


async def shutdown(ctx: dict) -> None:
    await ctx["stack"].aclose()
    await ctx["redis"].aclose()
    await ctx["engine"].dispose()
```

```python
# app/domains/attachments/service.py（末尾追加任务函数，供 Worker 调用）
from app.worker.observability import instrumented_job


@instrumented_job
async def cleanup_attachments(ctx: dict) -> dict:
    async with ctx["session_factory"]() as session:
        return await AttachmentService(session, ctx["storage"]).cleanup()
```

```python
# app/worker/jobs.py（追加）
from app.domains.attachments.service import cleanup_attachments

ALL_JOBS = [send_due_reminder, scan_due_tasks, export_tasks, cleanup_attachments]
```

```python
# app/worker/settings.py（cron_jobs 追加一行）
cron_jobs = [
    cron(scan_due_tasks, minute=set(range(0, 60, 5)), run_at_startup=False),
    cron(cleanup_attachments, minute=17),
]
```

---

## 6. 测试

```
tests/
├── conftest.py
└── domains/
    └── attachments/
        ├── conftest.py
        └── test_attachments.py
```

```python
# tests/conftest.py（追加）
from pathlib import Path

import pytest

from app.api.deps import get_storage
from app.storage.base import ObjectMeta, PresignedPost


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def upload_fileobj(self, key, fileobj, *, content_type):
        self.objects[key] = (fileobj.read(), content_type)

    async def upload_file(self, key, path, *, content_type):
        self.objects[key] = (Path(path).read_bytes(), content_type)

    async def head(self, key):
        obj = self.objects.get(key)
        return ObjectMeta(len(obj[0]), obj[1]) if obj else None

    async def read_head(self, key, size=2048):
        return self.objects[key][0][:size]

    async def delete(self, key):
        self.objects.pop(key, None)

    async def presign_get(self, key, *, expires, filename=None):
        return f"https://files.test/{key}?exp={expires}"

    async def presign_post(self, key, *, content_type, max_size, expires):
        return PresignedPost("https://files.test/upload", {"key": key, "Content-Type": content_type})


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


# 在 client fixture 中追加：app.dependency_overrides[get_storage] = lambda: storage
```

```python
# app/tests/domains/attachments/conftest.py
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_db, get_redis, get_storage
from app.domains.auth.deps import get_current_user
from app.main import app


@pytest_asyncio.fixture
async def client(session_factory, redis, storage, user):
    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def task_id(client) -> int:
    return (await client.post("/api/v1/tasks", json={"title": "t"})).json()["id"]

```

```python
# tests/domains/attachments/test_attachments.py
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
```

```bash
pytest tests/domains/ -v
```

---

## 7. 依赖关系全景图（截至本篇）

```
              auth
                ▲
        ┌───────┼────────┐
        │       │        │
      tasks   reminders  (被多个域依赖的基础身份域)
        ▲       ▲
        │       │
  attachments  exports
        │       │
        └───┬───┘
         storage / queue / cache（顶层基础设施，不依赖任何域）
```

每个箭头代表"谁依赖谁"，全部单向、无环。

---
