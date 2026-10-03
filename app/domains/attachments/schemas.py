from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

from app.domains.attachments.models import AttachmentStatus


class AttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    filename: str
    content_type: str
    size: int
    status: AttachmentStatus
    created_at: datetime


class UploadInitRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(max_length=100)


class UploadInitResponse(BaseModel):
    attachment_id: int
    url: str
    fields: dict[str, str]
    expires_in: int

