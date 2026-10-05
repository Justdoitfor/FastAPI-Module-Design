import functools
import time
import structlog

from arq.worker import Retry
from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.trace import SpanKind

from app.core.metrics import JOB_DURATION, JOBS

logger = structlog.get_logger("app.worker")
tracer = trace.get_tracer("app.worker")


def instrumented_job(fn):
    """结构化日志，指标，链路追踪"""

    name = fn.__name__

    @functools.wraps(fn)
    async def wrapper(ctx: dict, *args, obs_ctx: dict | None = None, **kwargs):
        obs_ctx = obs_ctx or {}
        structlog.contextvars.clear_contextvars()
        bound = {"job_name": name, "job_id": ctx.get("job_id"), "job_try": ctx.get("job_try")}
        if obs_ctx.get("request_id"):
            bound["request_id"] = obs_ctx["request_id"]
        structlog.contextvars.bind_contextvars(**bound)

        parent = extract(obs_ctx.get("trace") or {})
        status = "succeeded"
        start = time.perf_counter()

        with tracer.start_as_current_span(
                f"job {name}", context=parent, kind=SpanKind.CONSUMER,
                attributes={"job.id": ctx.get("job_id") or "", "job.try": ctx.get("job_try") or 0},
        ):
            logger.info("job_started")
            try:
                return await fn(ctx, *args, **kwargs)
            except Retry:
                status = "retry"
                logger.warning("job_retry_scheduled")
                raise
            except Exception:
                status = "failed"
                logger.exception("job_failed")
                raise
            finally:
                elapsed = time.perf_counter() - start
                JOBS.labels(name, status).inc()
                JOB_DURATION.labels(name).observe(elapsed)
                logger.info("job_finished", status=status, duration_ms=round(elapsed * 1000, 1))

    return wrapper
