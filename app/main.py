from contextlib import asynccontextmanager, AsyncExitStack

from fastapi import FastAPI
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.core.redis import create_redis

from arq import create_pool
from arq.connections import RedisSettings

from app.queue.names import QUEUE_NAME
from app.storage.s3 import S3Storage


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        app.state.redis = create_redis(settings.REDIS_URL)
        stack.push_async_callback(app.state.redis.aclose)
        app.state.queue_pool = await create_pool(
            RedisSettings.from_dsn(settings.REDIS_URL),
            default_queue_name=QUEUE_NAME,
        )
        stack.push_async_callback(app.state.queue_pool.aclose)
        app.state.storage = await S3Storage.create(stack)
        yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="FastAPI-Module-Design",
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
