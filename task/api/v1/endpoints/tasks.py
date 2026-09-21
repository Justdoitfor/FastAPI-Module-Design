from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from auth.api.user import get_current_user
from db.session import get_db
from auth.models import User
from task.schemas.common import Page
from task.schemas.task import TaskCreate, TaskQuery, TaskUpdate, TaskRead
from task.services.task import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])


def get_task_service(session: Annotated[AsyncSession, Depends(get_db)]) -> TaskService:
    return TaskService(session)


TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(data: TaskCreate, user: CurrentUser, service: TaskServiceDep):
    return await service.create_task(user, data)


@router.get("", response_model=Page[TaskRead])
async def list_tasks(
        query: Annotated[TaskQuery, Query()],  # FastAPI ≥0.115：Pydantic 模型作为查询参数
        user: CurrentUser,
        service: TaskServiceDep,
):
    tasks, total = await service.list_tasks(user, query)
    return Page[TaskRead].build(items=tasks, total=total, params=query)


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(task_id: int, user: CurrentUser, service: TaskServiceDep):
    return await service.get_task(user, task_id)


@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(
        task_id: int, data: TaskUpdate, user: CurrentUser, service: TaskServiceDep
):
    return await service.update_task(user, task_id, data)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: int, user: CurrentUser, service: TaskServiceDep):
    await service.delete_task(user, task_id)
