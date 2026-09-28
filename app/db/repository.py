from typing import Generic, TypeVar
from sqlalchemy import Select, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone

from app.db.base import Base

ModelT = TypeVar('ModelT', bound=Base)


class BaseRepository(Generic[ModelT]):
    """通用仓储：封装基础CRUD。只进行flush，不commit——事务边界由Service层进行决策"""
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _select(self, *, include_deleted: bool = False) -> Select:
        # 增加自动过滤软删除记录（如果model有deleted_at字段），auth域中User/RefreshToken没有deleted_at,hasattr判断为False，
        # auth中行为一致，不会影响auth域中model
        stmt = select(self.model)
        if not include_deleted and hasattr(self.model, 'deleted_at'):
            stmt = stmt.where(self.model.deleted_at.is_(None))
        return stmt

    async def get(self, obj_id: int) -> ModelT | None:
        stmt = self._select().where(self.model.id == obj_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def update(self, obj: ModelT, values: dict) -> ModelT:
        for key, value in values.items():
            setattr(obj, key, value)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def soft_delete(self, obj: ModelT) -> None:
        obj.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def hard_delete(self, obj: ModelT) -> None:
        await self.session.delete(obj)
        await self.session.flush()

    async def paginate(self, stmt: Select, *, offset: int, limit: int) -> tuple[list[ModelT], int]:
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = (await self.session.execute(count_stmt)).scalar_one()
        rows = await self.session.scalars(stmt.offset(offset).limit(limit))
        return list(rows), total
