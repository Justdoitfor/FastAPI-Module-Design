from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from task.core.exceptions import AppException

def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code":exc.code, "message": exc.message})

