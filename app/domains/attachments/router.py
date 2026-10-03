from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbDep, StorageDep
from app.api.rate_limit import rate_limit, user_identifier
from app.domains.auth.deps import ActiveUser
from app.domains.attachments.schemas import AttachmentRead, UploadInitResponse, UploadInitRequest
from app.domains.attachments.service import AttachmentService, IncomingFile
from app.schemas.common import DownloadLink


def get_attachment_service(session: DbDep, storage: StorageDep) -> AttachmentService:
    return AttachmentService(session, storage)


ServiceDep = Annotated[AttachmentService, Depends(get_attachment_service)]

router = APIRouter(
    prefix="/tasks/{task_id}/attachments",
    tags=["attachments"],
    dependencies=[Depends(rate_limit(limit=60, window=60, scope="attachments", identifier=user_identifier))],
)


@router.post("", response_model=AttachmentRead, status_code=status.HTTP_201_CREATED)
async def upload_attachment(task_id: int, file: UploadFile, user: ActiveUser, service: ServiceDep):
    return await service.upload(user, task_id, IncomingFile(file.file, file.filename or "file"))


@router.post("/uploads", response_model=UploadInitResponse, status_code=status.HTTP_201_CREATED)
async def init_upload(task_id: int, data: UploadInitRequest, user: ActiveUser, service: ServiceDep):
    return await service.init_upload(user, task_id, data)


@router.post("/{attachment_id}/complete", response_model=AttachmentRead)
async def complete_upload(task_id: int, attachment_id: int, user: ActiveUser,
                          service: ServiceDep):
    return await service.complete_upload(user, task_id, attachment_id)


@router.get("", response_model=list[AttachmentRead])
async def list_attachments(task_id: int, user: ActiveUser, service: ServiceDep):
    return await service.list_attachments(user, task_id)


@router.get("/{attachment_id}/download", response_model=DownloadLink)
async def download_attachment(task_id: int, attachment_id: int, user: ActiveUser, service: ServiceDep):
    return await service.get_download(user, task_id, attachment_id)


@router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(task_id: int, attachment_id: int, user: ActiveUser, service: ServiceDep):
    await service.delete(user, task_id, attachment_id)
