from fastapi import APIRouter, Depends
from app.domains.auth.router import router as auth_router
from app.domains.tasks.router import router as task_router

from app.api.rate_limit import rate_limit

api_router = APIRouter(
    dependencies=[Depends(rate_limit(limit=300, window=60, scope="global"))],
)
api_router.include_router(auth_router)
api_router.include_router(task_router)
