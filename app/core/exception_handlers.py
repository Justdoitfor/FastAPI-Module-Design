from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import AppException

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import AppException

logger = structlog.get_logger(__name__)


def _request_id() -> str | None:
    return structlog.contextvars.get_contextvars().get("request_id")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        emit = logger.error if exc.status_code >= 500 else logger.info
        emit("app_exception", code=exc.code, status=exc.status_code, message=exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "request_id": _request_id()},
            headers=exc.headers,
        )
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", method=request.method, path=request.url.path)
        request_id = _request_id()
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal_error",
                "message": "服务器内部错误",
                "request_id": request_id,
            },
            headers={"X-Request-ID": request_id} if request_id else None,
        )
