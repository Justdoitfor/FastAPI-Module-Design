import functools
from arq.worker import Retry


def instrumented_job(fn):
    """占位，后续接入结构化日志，指标，链路追踪。这里统一入口形态"""

    @functools.wraps(fn)
    async def wrapper(ctx: dict, *args, **kwargs):
        return await fn(ctx, *args, **kwargs)

    return wrapper
