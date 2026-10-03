import enum

from sqlalchemy import BigInteger, Enum, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SoftDeleteMixin, TimestampMixin


class AttachmentStatus(str, enum.Enum):
    PENDING = "pending"
    READY = "ready"


class Attachment(Base, SoftDeleteMixin, TimestampMixin):
    __tablename__ = "attachments"
    __table_args__ = (
        Index(
            "ix_attachment_pending_created",
            "created_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    object_key: Mapped[str] = mapped_column(String(512), unique=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[AttachmentStatus] = mapped_column(
        Enum(
            AttachmentStatus,
            native_enum=False,
            length=20,
            values_callable=lambda e: [m.value for m in e]
        ),
        default=AttachmentStatus.PENDING,
    )
