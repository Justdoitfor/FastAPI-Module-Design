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
    AppException,
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

from app.core.metrics import UPLOADS

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
        try:
            attachment = await self._upload(user, task_id, file)
        except AppException as e:
            UPLOADS.labels(path="key", result=e.code).inc()
            raise
        UPLOADS.labels(path="relay", result="success").inc()
        return attachment

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

    async def _upload(self, user: User, task_id: int, file: IncomingFile) -> AttachmentRead:
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
