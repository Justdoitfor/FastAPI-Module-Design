import re
import time

from uuid import uuid4

import structlog

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.v1.router import API_PREFIX
from app.core.config import settings
from app.core.metrics import HTTP_DURATION, HTTP_IN_FLIGHT, HTTP_REQUESTS

logger = structlog.get_logger(__name__)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9\-_]{8,64}$")
_QUIET_PATH = {"/livez", "/readyz", "/metrics"}


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in _QUIET_PATH:
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid4().hex

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status_code = 500
        start = time.perf_counter()
        HTTP_IN_FLIGHT.inc()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - start
            HTTP_IN_FLIGHT.dec()

            route_path = getattr(scope.get("route"), "path", None)
            route = f"{API_PREFIX}{route_path}" if route_path is not None else "unmatched"

            method = scope["method"]
            HTTP_REQUESTS.labels(method, route, str(status_code)).inc()
            HTTP_DURATION.labels(method, route).observe(duration)

            slow = duration >= settings.SLOW_REQUEST_SECONDS
            (logger.warning if slow else logger.info)(
                "http_request",
                request_id=request_id,
                method=method,
                path=scope["path"],
                route=route,
                status=status_code,
                duration_ms=round(duration * 1000, 1),
                client_ip=(scope.get("client") or (None,))[0],
                slow=slow,
            )
