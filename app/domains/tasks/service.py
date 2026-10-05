from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessError, NotFoundError
from app.domains.auth.models import User
from app.domains.tasks.models import Task
from app.domains.tasks.repository import TaskRepository
from app.domains.tasks.schemas import TaskCreate, TaskQuery, TaskUpdate, TaskRead


import hashlib
from app.cache.cache import Cache
from app.schemas.common import Page


class TaskService:
    DETAIL_TTL = 300
    LIST_TTL = 60

    def __init__(self, session: AsyncSession, cache: Cache) -> None:
        self.session = session
        self.cache = cache
        self.tasks = TaskRepository(session)

    async def list_tasks(self, user: User, query: TaskQuery) -> Page[TaskRead]:
        version = await self.cache.version(self._list_scope(user.id))
        key = self.cache.key("list", user.id, f"v{version}", self._digest(query))

        async def load() -> Page[TaskRead]:
            tasks, total = await self.tasks.list_by_owner(user.id, query)
            return Page[TaskRead].build(items=tasks, total=total, params=query)

        page = await self.cache.get_or_load(key, Page[TaskRead], load, ttl=self.LIST_TTL, name="task_list")
        assert page is not None
        return page

    async def get_task(self, user: User, task_id: int) -> TaskRead:
        async def load() -> TaskRead | None:
            task = await self.tasks.get(task_id)
            return TaskRead.model_validate(task) if task else None

        task = await self.cache.get_or_load(
            self.cache.key("task", task_id),
            TaskRead,
            load,
            ttl=self.DETAIL_TTL,
            name="task_detail",
        )
        if task is None or task.owner_id != user.id:
            raise NotFoundError("任务不存在")
        return task

    async def create_task(self, user: User, data: TaskCreate) -> TaskRead:
        self._validate_due_date(data.due_date)
        task = Task(**data.model_dump(), owner_id=user.id)
        await self.tasks.create(task)
        await self.session.commit()
        await self._invalidate(user.id, task.id)
        return TaskRead.model_validate(task)

    async def update_task(self, user: User, task_id: int, data: TaskUpdate) -> TaskRead:
        task = await self._get_owned_task(user, task_id)
        values = data.model_dump(exclude_unset=True)
        if "due_date" in values:
            self._validate_due_date(values["due_date"])
            values["reminded_at"] = None
        await self.tasks.update(task, values)
        await self.session.commit()
        await self._invalidate(user.id, task.id)

        return TaskRead.model_validate(task)

    async def delete_task(self, user: User, task_id: int) -> None:
        task = await self._get_owned_task(user, task_id)
        await self.tasks.soft_delete(task)
        await self.session.commit()
        await self._invalidate(user.id, task.id)

    async def _get_owned_task(self, user: User, task_id: int) -> Task:
        task = await self.tasks.get_owned(task_id, user.id)
        if task is None:
            raise NotFoundError("任务不存在")
        return task

    @staticmethod
    def _validate_due_date(due_date: datetime | None) -> None:
        if due_date is not None and due_date <= datetime.now(timezone.utc):
            raise BusinessError("截至时间不许晚于当前时间")

    async def _invalidate(self, user_id: int, task_id: int) -> None:
        await self.cache.delete(self.cache.key("task", task_id))
        await self.cache.bump_version(self._list_scope(user_id))

    @staticmethod
    def _list_scope(user_id: int) -> str:
        return f"tasks:{user_id}"

    @staticmethod
    def _digest(query: TaskQuery) -> str:
        return hashlib.sha256(query.model_dump_json().encode()).hexdigest()[:16]
