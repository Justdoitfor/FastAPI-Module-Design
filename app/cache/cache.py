import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)

_NULL = "__null__"
_LOCK_TTL = 10
_VERSION_TTL = 7 * 86400


class Cache:
    """
    Cache-Aside 封装。任何Redis 异常降级为直接回源， 缓存只作为优化，不是依赖
    """

    def __init__(self, redis: Redis, prefix: str = "app") -> None:
        self.redis = redis
        self.prefix = prefix

    def key(self, *parts: object) -> str:
        return ":".join((self.prefix, *map(str, parts)))

    async def get_or_load(
            self,
            key: str,
            model: type[M],
            loader: Callable[[], Awaitable[M | None]],
            *,
            ttl: int,
            null_ttl: int = 30,
    ) -> M | None:
        hit, value = await self._read(key, model)
        if hit:
            return value
        lock_key = f"{key}:lock"
        acquired = await self._acquire_lock(lock_key)
        try:
            if not acquired:
                for _ in range(20):
                    await asyncio.sleep(0.05)
                    hit, value = await self._read(key, model)
                    if hit:
                        return value
            value = await loader()
            await self._write(key, value, ttl, null_ttl)
            return value
        finally:
            if acquired:
                await self._release_lock(lock_key)

    async def delete(self, *keys: str) -> None:
        try:
            await self.redis.delete(*keys)
        except RedisError:
            logger.warning("cache delete failed: %s", keys, exc_info=True)

    async def version(self, scope: str) -> int:
        try:
            return int(await self.redis.get(self.key("ver", scope)) or 0)
        except RedisError:
            logger.warning("cache version read failed: %s", scope, exc_info=True)
            return 0

    async def bump_version(self, scope: str) -> None:
        key = self.key("ver", scope)
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, _VERSION_TTL)
                await pipe.execute()
        except RedisError:
            logger.warning("cache bump version failed: %s", scope, exc_info=True)

    async def _read(self, key: str, model: type[M]) -> tuple[bool, M | None]:
        try:
            raw = await self.redis.get(key)
        except RedisError:
            logger.warning("cache read failed: %s", key, exc_info=True)
            return False, None

        if raw is None:
            return False, None
        if raw == _NULL:
            return True, None
        try:
            return True, model.model_validate_json(raw)
        except ValidationError:
            logger.warning("cache payload invalid, treat as miss: %s", key)
            return False, None

    async def _write(self, key: str, value: BaseModel | None, ttl: int, null_ttl: int) -> None:
        if value is None:
            payload, expire = _NULL, null_ttl
        else:
            payload, expire = value.model_dump_json(), ttl
        expire += random.randint(0, max(1, expire // 10))
        try:
            await self.redis.set(key, payload, ex=expire)
        except RedisError:
            logger.warning("cache write failed: %s", key, exc_info=True)

    async def _acquire_lock(self, lock_key: str) -> bool:
        try:
            return bool(await self.redis.set(lock_key, "1", nx=True, ex=_LOCK_TTL))
        except RedisError:
            return True

    async def _release_lock(self, lock_key: str) -> None:
        try:
            await self.redis.delete(lock_key)
        except RedisError:
            pass
