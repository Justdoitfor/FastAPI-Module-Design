from typing import Literal

from pydantic import BaseModel

from app.domains.tasks.models import TaskStatus

ExportStatus = Literal["queued", "running", "failed", "succeeded"]


class ExportRequest(BaseModel):
    status: TaskStatus | None = None


class ExportJobRead(BaseModel):
    job_id: str
    status: ExportStatus
    download_url: str | None = None
