from fastapi import APIRouter
from task.api.v1.endpoints import tasks
from auth.api import user

api_router = APIRouter()
api_router.include_router(user.router, tags=["user"])
api_router.include_router(tasks.router, tags=["tasks"])