import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SoftDeleteMixin, TimestampMixin


class TaskStatus(enum.Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class TaskPriority(enum.IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class Task(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_owner_status", "owner_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus,
             native_enum=False,
             length=20,
             values_callable=lambda e: [c.value for c in e]),
        default=TaskStatus.TODO,
    )
    priority: Mapped[int] = mapped_column(SmallInteger, default=TaskPriority.MEDIUM)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
