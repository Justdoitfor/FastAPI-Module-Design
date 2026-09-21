from datetime import datetime
from typing import Literal

from pydantic import BaseModel, AwareDatetime, ConfigDict, Field, field_validator

from task.models.task import TaskPriority, TaskStatus
from task.schemas.common import PageParams


class TaskBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    priority: TaskPriority = TaskPriority.MEDIUM
    due_date: AwareDatetime | None = None


class TaskCreate(TaskBase):
    pass


class TaskUpdate(TaskBase):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_date: AwareDatetime | None = None

    @field_validator("title", "status", "priority")
    @classmethod
    def not_null_when_provided(cls, v):
        # 这些列在数据库中 NOT NULL：允许不传，但不允许显式传 null
        if v is None:
            raise ValueError("该字段不能为 null")
        return v


class TaskRead(TaskBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: TaskStatus
    owner_id: int
    created_at: datetime
    updated_at: datetime


class TaskQuery(PageParams):
    """列表查询参数：分页 + 过滤 + 排序。"""

    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    keyword: str | None = Field(default=None, max_length=100)
    sort_by: Literal["created_at", "due_date", "priority"] = "created_at"
    order: Literal["asc", "desc"] = "desc"
