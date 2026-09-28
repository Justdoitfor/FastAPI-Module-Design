from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbDep, CacheDep
from app.domains.auth.deps import ActiveUser
from app.domains.tasks.schemas import TaskCreate, TaskUpdate, TaskQuery, TaskRead
from app.domains.tasks.service import TaskService
from app.schemas.common import Page

from app.api.rate_limit import rate_limit, user_identifier

router = APIRouter(
    prefix="/tasks",
    dependencies=[Depends(rate_limit(limit=120, window=60, scope="tasks", identifier=user_identifier))],
    tags=["tasks"])


def get_task_service(session: DbDep, cache: CacheDep) -> TaskService:
    return TaskService(session, cache)


ServiceDep = Annotated[TaskService, Depends(get_task_service)]


@router.post(
    "",
    response_model=TaskRead,
    dependencies=[Depends(rate_limit(limit=20, window=60, scope="tasks:create", identifier=user_identifier))],
    status_code=status.HTTP_201_CREATED)
async def create_task(data: TaskCreate, user: ActiveUser, service: ServiceDep):
    return await service.create_task(user, data)


@router.get("", response_model=Page[TaskRead])
async def list_tasks(query: Annotated[TaskQuery, Query()], user: ActiveUser, service: ServiceDep):
    return await service.list_tasks(user, query)


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(task_id: int, user: ActiveUser, service: ServiceDep):
    return await service.get_task(user, task_id)


@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(task_id: int, data: TaskUpdate, user: ActiveUser, service: ServiceDep):
    return await service.update_task(user, task_id, data)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: int, user: ActiveUser, service: ServiceDep):
    await service.delete_task(user, task_id)
