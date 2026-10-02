from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory

from redis.asyncio import Redis
from app.cache.cache import Cache
from app.cache.rate_limiter import RateLimiter

from app.queue.client import ArqJobQueue, JobQueue


async def get_db():
    async with async_session_factory() as session:
        yield session


DbDep = Annotated[AsyncSession, Depends(get_db)]


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


RedisDep = Annotated[Redis, Depends(get_redis)]


def get_cache(redis: RedisDep) -> Cache:
    return Cache(redis)


CacheDep = Annotated[Cache, Depends(get_cache)]


def get_rate_limiter(redis: RedisDep) -> RateLimiter:
    return RateLimiter(redis)


LimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]

def get_job_queue(request: Request) -> JobQueue:
    return ArqJobQueue(request.app.state.queue_pool)

JobQueueDep = Annotated[JobQueue, Depends(get_job_queue)]
