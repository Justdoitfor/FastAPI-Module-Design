import logging
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Any, Protocol

from arq.connections import ArqRedis
from arq.jobs import Job, JobStatus
from redis.exceptions import RedisError

import structlog
from opentelemetry.propagate import inject

from app.core.exceptions import QueueUnavailableError

logger = logging.getLogger(__name__)


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NOT_FOUND = "not_found"


@dataclass
class JobSnapshot:
    state: JobState
    result: Any = None


class JobQueue(Protocol):
    async def enqueue(
            self,
            function: str,
            *args: Any,
            job_id: str | None = None,
            defer_by: timedelta | None = None,
    ) -> str | None:
        ...

    async def get(
            self,
            job_id: str,
    ) -> JobSnapshot:
        ...


class ArqJobQueue:
    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    async def enqueue(
            self,
            function: str,
            *args: Any,
            job_id: str | None = None,
            defer_by: timedelta | None = None,
    ) -> str | None:
        carrier: dict[str, str] = {}
        inject(carrier)
        obs_ctx = {
            "request_id": structlog.contextvars.get_contextvars().get("request_id"),
            "trace": carrier,
        }

        try:
            job = await self._pool.enqueue_job(
                function,
                *args,
                _job_id=job_id,
                _defer_by=defer_by,
                obs_ctx=obs_ctx,
            )
        except RedisError as e:
            logger.error(f"enqueue failed:{function}", exc_info=True)
            raise QueueUnavailableError() from e
        return job.job_id if job else None

    async def get(self, job_id: str) -> JobSnapshot:
        try:
            job = Job(job_id, self._pool, _queue_name=self._pool.default_queue_name)
            status = await job.status()
            if status is JobStatus.not_found:
                return JobSnapshot(JobState.NOT_FOUND)
            if status is JobStatus.in_progress:
                return JobSnapshot(JobState.RUNNING)
            if status is JobStatus.complete:
                info = await job.result_info()
                if info is None:
                    return JobSnapshot(JobState.NOT_FOUND)
                state = JobState.SUCCEEDED if info.success else JobState.FAILED
                return JobSnapshot(state, info.result if info.success else None)
            return JobSnapshot(JobState.QUEUED)
        except RedisError as e:
            raise QueueUnavailableError() from e
