from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from task.core.exceptions import BusinessError, NotFoundError
from task.models.task import Task
from auth.models.user import User
from task.repositories.task import TaskRepository
from task.schemas.task import TaskCreate, TaskQuery, TaskUpdate


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tasks = TaskRepository(session)

    async def list_tasks(
            self,
            user: User,
            query: TaskQuery,
    ) -> tuple[list[Task], int]:
        return await self.tasks.list_by_owner(user.id, query)

    async def get_task(
            self,
            user: User,
            task_id: int
    ) -> Task:
        return await self._get_owned_task(user, task_id)

    async def create_task(self, user: User, data: TaskCreate) -> Task:
        self._validate_due_date(data.due_date)
        task = Task(**data.model_dump(), owner_id=user.id)
        await self.tasks.create(task)
        await self.session.commit()
        return task

    async def update_task(
            self,
            user: User,
            task_id: int,
            data: TaskUpdate,
    ) -> Task:
        task = await self._get_owned_task(user, task_id)
        values = data.model_dump(exclude_unset=True)
        if "due_date" in values:
            self._validate_due_date(values["due_date"])
        await self.tasks.update(task, values)
        await self.session.commit()
        return task

    async def delete_task(
            self,
            user: User,
            task_id: int,
    ) -> None:
        task = await self._get_owned_task(user, task_id)
        await self.tasks.soft_delete(task)
        await self.session.commit()

    # ---------------------- 内部规则 ----------------------
    async def _get_owned_task(self, user, task_id) -> Task:
        task = await self.tasks.get(task_id)
        if task is None or task.owner_id != user.id:
            raise NotFoundError("任务不存在！")
        return task

    @staticmethod
    def _validate_due_date(due_date: datetime | None) -> None:
        if due_date is not None and due_date < datetime.now(timezone.utc):
            raise BusinessError("截止时间必须晚于当前时间！")
