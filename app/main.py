from contextlib import asynccontextmanager, AsyncExitStack

from fastapi import FastAPI
from app.api.v1.router import api_router, API_PREFIX
from app.core.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.core.redis import create_redis

from arq import create_pool
from arq.connections import RedisSettings

from app.queue.names import QUEUE_NAME
from app.storage.s3 import S3Storage

import structlog
from prometheus_client import start_http_server
from app.api.health import router as health_router
from app.api.middleware import RequestContextMiddleware
from app.core.logging import configure_logging
from app.core.tracing import instrument_app, instrument_libraries, setup_tracing
from app.db.session import engine

configure_logging(
    level=settings.LOG_LEVEL,
    json_logs=settings.LOG_JSON,
    service=f"{settings.SERVICE_NAME}-api",
    env=settings.ENV,
    release=settings.RELEASE,
)

logger = structlog.get_logger(__name__)
tracer_provider = setup_tracing(f"{settings.SERVICE_NAME}-api") if settings.OTEL_ENABLED else None


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

        if tracer_provider:
            stack.callback(tracer_provider.shutdown)
        if settings.METRIC_PORT:
            start_http_server(settings.METRIC_PORT)
        logger.info("app_started", env=settings.ENV, release=settings.RELEASE)
        yield
        logger.info("app_stopping")


def create_app() -> FastAPI:
    app = FastAPI(
        title="FastAPI-Module-Design",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router, prefix=API_PREFIX)
    if tracer_provider:
        instrument_libraries(engine)
        instrument_app(app)
    return app


app = create_app()
