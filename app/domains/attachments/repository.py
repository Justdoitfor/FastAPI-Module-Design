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
