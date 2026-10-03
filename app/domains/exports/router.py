from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import JobQueueDep, StorageDep
from app.api.rate_limit import rate_limit, user_identifier
from app.domains.auth.deps import ActiveUser
from app.domains.exports.schemas import ExportJobRead, ExportRequest
from app.domains.exports.service import ExportService
from app.schemas.common import DownloadLink

router = APIRouter(prefix="/exports", tags=["exports"])


def get_export_service(queue: JobQueueDep, storage: StorageDep) -> ExportService:
    return ExportService(queue, storage)


ServiceDep = Annotated[ExportService, Depends(get_export_service)]


@router.post("/tasks", response_model=ExportJobRead, status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(rate_limit(limit=3, window=3600, scope="export", identifier=user_identifier))])
async def create_task_export(
        body: ExportRequest,
        request: Request,
        response: Response,
        user: ActiveUser,
        service: ServiceDep,
):
    job_id = await service.request_export(user, body.status)
    response.headers["Location"] = str(request.url_for("get_export", job_id=job_id))
    return ExportJobRead(job_id=job_id, status="queued")


@router.get("/{job_id}", response_model=ExportJobRead, name="get_export")
async def get_export(job_id: str, request: Request, user: ActiveUser, service: ServiceDep):
    job_status = await service.get_export(user, job_id)
    url = (str(request.url_for("download_export", job_id=job_id)) if job_status == "succeeded" else None)
    return ExportJobRead(job_id=job_id, status=job_status, download_url=url)


@router.get("/{job_id}/download", response_model=DownloadLink, name="download_export")
async def download_export(job_id: str, user: ActiveUser, service: ServiceDep):
    path = await service.get_download_url(user, job_id)
    return await service.get_download_url(user, job_id)
