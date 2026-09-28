from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbDep
from app.domains.auth.deps import ActiveUser
from app.domains.tasks.schemas import TaskCreate, TaskUpdate, TaskQuery, TaskRead
from app.domains.tasks.service import TaskService
from app.schemas.common import Page

router = APIRouter(prefix="/tasks", tags=["tasks"])


def get_task_service(session: DbDep) -> TaskService:
    return TaskService(session)


ServiceDep = Annotated[TaskService, Depends(get_task_service)]


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(data: TaskCreate, user: ActiveUser, service: ServiceDep):
    return await service.create_task(user, data)


@router.get("", response_model=Page[TaskRead])
async def list_tasks(query: Annotated[TaskQuery, Query()], user: ActiveUser, service: ServiceDep):
    tasks, total = await service.list_tasks(user, query)
    return Page[TaskRead].build(items=tasks, total=total, params=query)


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(task_id: int, user: ActiveUser, service: ServiceDep):
    return await service.get_task(user, task_id)


@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(task_id: int, data: TaskUpdate, user: ActiveUser, service: ServiceDep):
    return await service.update_task(user, task_id, data)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: int, user: ActiveUser, service: ServiceDep):
    await service.delete_task(user, task_id)
