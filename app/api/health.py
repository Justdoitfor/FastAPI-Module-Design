import asyncio
from typing import Annotated

import structlog
from fastapi import Depends, APIRouter, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import dependency

from app.api.deps import DbDep, RedisDep, StorageDep

logger = structlog.get_logger(__name__)
router = APIRouter(
    tags=["health"],
    include_in_schema=False,
)

CHECK_TIMEOUT = 2.0
CRITICAL = {"database"}


async def _probe(name: str, call) -> tuple[str, bool]:
    try:
        await asyncio.wait_for(call(), CHECK_TIMEOUT)
        return name, True
    except Exception:
        logger.warning("readiness_check_failed", dependency=name, exc_info=True)
        return name, False


@router.get("/livez")
async def livez() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(
        response: Response,
        db: DbDep,
        redis: RedisDep,
        storage: StorageDep,
) -> dict:
    results = dict(await asyncio.gather(
        _probe("database", lambda: db.execute(text("SELECT 1"))),
        _probe("redis", redis.ping),
        _probe("storage", storage.ping),
    ))
    if not all(results[name] for name in CRITICAL):
        response.status_code = 503
        overall = "unavailable"
    elif not all(results.values()):
        overall = "degraded"
    else:
        overall = "ok"
    return {
        "status": overall,
        "checks": {k: "ok" if v else "fail" for k, v in results.items()},
    }
