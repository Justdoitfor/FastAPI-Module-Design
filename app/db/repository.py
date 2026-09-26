from typing import Generic, TypeVar
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

ModelT = TypeVar('ModelT', bound=Base)


class BaseRepository(Generic[ModelT]):
    """通用仓储：封装基础CRUD。只进行flush，不commit——事务边界由Service层进行决策"""
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _select(self) -> Select:
        return select(self.model)

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
