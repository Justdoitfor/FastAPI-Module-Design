from fastapi import FastAPI
from task.api.v1.router import api_router
from task.core.exception_handlers import register_exception_handlers


def get_app() -> FastAPI:
    app = FastAPI(title="FastAPI-Module-Design-Tasks")
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = get_app()
