from contextlib import asynccontextmanager

from fastapi import FastAPI
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.core.redis import create_redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = create_redis(settings.REDIS_URL)
    yield
    await app.state.redis.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="FastAPI-Module-Design",
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
