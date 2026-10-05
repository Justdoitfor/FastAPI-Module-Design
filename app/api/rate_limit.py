import math
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request, Response
from app.api.deps import LimiterDep
from app.core.config import settings
from app.core.exceptions import RateLimitError
from app.domains.auth.deps import get_current_user
from app.domains.auth.models import User

from app.core.metrics import RATE_LIMIT_DECISIONS


async def ip_identifier(request: Request) -> str:
    return f"ip:{request.client.host if request.client else 'unknown'}"


async def user_identifier(user: Annotated[User, Depends(get_current_user)]) -> str:
    return f"user:{user.id}"


def rate_limit(
        *,
        limit: int,
        window: int,
        scope: str,
        identifier: Callable = ip_identifier,
        fail_open: bool = True,
):
    async def dependency(
            response: Response,
            limiter: LimiterDep,
            ident: Annotated[str, Depends(identifier)],
    ) -> None:
        if not settings.RATE_LIMIT_ENABLED:
            return

        result = await limiter.hit(
            f"{scope}:{ident}",
            limit=limit,
            window=window,
            fail_open=fail_open,
        )

        headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(result.remaining),
        }

        if not result.allowed:
            headers["Retry-After"] = str(max(1, math.ceil(result.retry_after_ms / 1000)))
            raise RateLimitError(headers=headers)

        response.headers.update(headers)

        RATE_LIMIT_DECISIONS.labels(scope=scope, decision="allowed" if result.allowed else "blocked").inc()

    return dependency
