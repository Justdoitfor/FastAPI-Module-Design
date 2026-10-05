from arq import cron, func
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.domains.attachments.service import cleanup_attachments
from app.integrations.email import build_email_sender
from app.queue.names import QUEUE_NAME
from app.worker.jobs import ALL_JOBS
from app.domains.reminders.service import scan_due_tasks, report_queue_depth
from app.storage.s3 import S3Storage

from contextlib import AsyncExitStack
from prometheus_client import start_http_server
from app.core.logging import configure_logging
from app.core.tracing import instrument_libraries, setup_tracing


async def startup(ctx: dict) -> None:
    configure_logging(
        level=settings.LOG_LEVEL,
        json_logs=settings.LOG_JSON,
        service=f"{settings.SERVICE_NAME}-worker",
        env=settings.ENV,
        release=settings.RELEASE,
    )
    engine = create_async_engine(
        settings.DATABASE_URL,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
    )
    ctx["engine"] = engine
    ctx["session_factory"] = async_sessionmaker(engine, expire_on_commit=False)
    ctx["email"] = build_email_sender()

    from app.core.redis import create_redis
    ctx["redis"] = create_redis(settings.REDIS_URL)

    ctx["stack"] = AsyncExitStack()
    ctx["storage"] = await S3Storage.create(ctx["stack"])

    if settings.OTEL_ENABLED:
        ctx["tracer_provider"] = setup_tracing(f"{settings.SERVICE_NAME}-worker")
        instrument_libraries(engine)
    if settings.WORKER_METRICS_PORT:
        start_http_server(settings.WORKER_METRICS_PORT)


async def shutdown(ctx: dict) -> None:
    if provider := ctx.get("tracer_provider"):
        provider.shutdown()
    await ctx["stack"].aclose()
    await ctx["redis"].aclose()
    await ctx["engine"].dispose()


class WorkerSettings:
    functions = [func(job, max_tries=5) for job in ALL_JOBS]
    cron_jobs = [
        cron(scan_due_tasks, minute=set(range(0, 60, 5)), run_at_startup=False),
        cron(cleanup_attachments, minute=17),
        cron(report_queue_depth, second={0, 15, 30, 45}, unique=False),
    ]

    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    queue_name = QUEUE_NAME
    on_startup = startup
    on_shutdown = shutdown

    max_jobs = 10
    job_timeout = 300
    keep_result = 86400
    health_check_interval = 30
