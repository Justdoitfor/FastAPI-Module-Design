import logging
from dataclasses import dataclass
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local member = ARGV[3]
local t = redis.call("TIME")
local now = t[1] * 1000 + math.floor(t[2] / 1000)

redis.call("ZREMRANGEBYSCORE", key, 0, now - window)
local count = redis.call("ZCARD", key)

if count < limit then
    redis.call("ZADD", key, now, member)
    redis.call("PEXPIRE", key, window)
    return {1, limit - count - 1, 0}
end

local oldest = redis.call("ZRANGE", key, 0, 0, "WITHSCORES")
local retry = window - (now - tonumber(oldest[2]))
return {0, 0, retry}
"""


@dataclass(frozen=True, slots=True)
class RateLimiterResult:
    allowed: bool
    remaining: int
    retry_after_ms: int = 0

class RateLimiter:
    def __init__(
            self,
            redis: Redis,
            prefix: str = "app: rl",
    ) -> None:
        self._prefix = prefix
        self._script = redis.register_script(_SLIDING_WINDOW_LUA)

    async def hit(
            self,
            key: str,
            *,
            limit: int,
            window: int,
            fail_open: bool = True,
    ) -> RateLimiterResult:
        try:
            allowed, remaining, retry_ms = await self._script(
                keys=[f"{self._prefix}:{key}"],
                args=[window * 1000, limit, uuid4().hex],
            )
        except RedisError:
            logger.error("rate limiter unavailable", exc_info=True)
            if fail_open:
                return RateLimiterResult(allowed=False, remaining=limit)
            return RateLimiterResult(allowed=False, remaining=0, retry_after_ms=1000)
        return RateLimiterResult(allowed=bool(allowed), remaining=int(remaining), retry_after_ms=int(retry_ms))
