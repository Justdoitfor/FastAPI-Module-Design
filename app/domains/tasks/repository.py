from app.db.repository import BaseRepository
from app.domains.tasks.models import Task, TaskStatus
from app.domains.tasks.schemas import TaskQuery

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

_SORT_COLUMNS = {
    "created_at": Task.created_at,
    "due_date": Task.due_date,
    "priority": Task.priority,
}


class TaskRepository(BaseRepository[Task]):
    model = Task

    async def get_owned(self, task_id: int, owner_id: int) -> Task | None:
        stmt = self._select().where(Task.id == task_id, Task.owner_id == owner_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_owner(self, owner_id: int, query: TaskQuery) -> tuple[list[Task], int]:
        stmt = self._select().where(Task.owner_id == owner_id)

        if query.status:
            stmt = stmt.where(Task.status == Task.status)
        if query.priority:
            stmt = stmt.where(Task.priority == Task.priority)
        if query.keyword:
            stmt = stmt.where(Task.title.icontains(query.keyword, autoescape=True))

        column = _SORT_COLUMNS[query.sort_by]
        order = column.desc() if query.order == "desc" else column.asc()
        stmt = stmt.order_by(order, Task.id.desc())

        return await self.paginate(stmt, offset=query.offset, limit=query.size)

    async def list_due_unreminded(self, *, within: timedelta, limit: int) -> list[Task]:
        now = datetime.now(timezone.utc)
        stmt = (self._select()
                .where(Task.due_date.is_not(None),
                       Task.due_date > now,
                       Task.due_date <= now + within,
                       Task.status != TaskStatus.DONE,
                       Task.reminded_at.is_(None), )
                .order_by(Task.due_date)
                .limit(limit)
                )
        return list((await self.session.scalars(stmt)).all())

    async def iter_by_owner(
            self,
            owner_id: int,
            *,
            status: TaskStatus | None = None,
            batch_size: int = 1000,
    ) -> AsyncIterator[list[Task]]:
        last_id = 0
        while True:
            stmt = (
                self._select()
                .where(Task.owner_id == owner_id, Task.id > last_id)
                .order_by(Task.id)
                .limit(batch_size)
            )
            if status:
                stmt = stmt.where(Task.status == status)
            rows = list((await self.session.scalars(stmt)).all())
            if not rows:
                return
            yield rows
            last_id = rows[-1].id

