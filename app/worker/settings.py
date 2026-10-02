from arq import cron, func
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.integrations.email import build_email_sender
from app.queue.names import QUEUE_NAME
from app.worker.jobs import ALL_JOBS
from app.domains.reminders.service import scan_due_tasks


async def startup(ctx: dict) -> None:
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


async def shutdown(ctx: dict) -> None:
    await ctx["redis"].aclose()
    await ctx["engine"].dispose()


class WorkerSettings:
    functions = [func(job, max_tries=5) for job in ALL_JOBS]
    cron_jobs = [cron(scan_due_tasks, minute=set(range(0, 60, 5)), run_at_startup=False)]

    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    queue_name = QUEUE_NAME
    on_startup = startup
    on_shutdown = shutdown

    max_jobs = 10
    job_timeout = 300
    keep_result = 86400
    health_check_interval = 30
