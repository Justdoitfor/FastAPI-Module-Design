from task.models import Task
from task.repositories.base import BaseRepository
from task.schemas.task import TaskQuery

_SORT_COLUMNS = {
    "created_at": Task.created_at,
    "due_date": Task.due_date,
    "priority": Task.priority,
}


class TaskRepository(BaseRepository[Task]):
    model = Task

    async def list_by_owner(
            self,
            owner_id: int,
            query: TaskQuery,
    ) -> tuple[list[Task], int]:
        stmt = self._select().where(Task.owner_id == owner_id)
        if query.status:
            stmt = stmt.where(Task.status == query.status)
        if query.priority:
            stmt = stmt.where(Task.priority == query.priority)
        if query.keyword:
            stmt = stmt.where(Task.title.icontains(query.keyword, autoescape=True))
        column = _SORT_COLUMNS[query.sort_by]
        order = column.desc() if query.order == "desc" else column.asc()
        stmt = stmt.order_by(order, Task.id.desc())

        return await self.paginate(stmt, offset=query.offset, limit=query.size)
