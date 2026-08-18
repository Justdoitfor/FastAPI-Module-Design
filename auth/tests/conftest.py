import pytest_asyncio

from auth.core.redis import redis_client


@pytest_asyncio.fixture
async def clean_redis():
    yield

    await redis_client.flushdb()
