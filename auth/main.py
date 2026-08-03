from fastapi import FastAPI
from auth.database.session import engine
from auth.api import auth
from auth.core.exceptions import BusinessException
from auth.core.exception_handler import (
    business_exception_handler,
    validation_exception_handler,
    http_exception_handler,
    database_exception_handler,
    global_exception_handler,
)
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError

app = FastAPI(
    title="Auth System",
    version="1.0.0",
)
app.include_router(auth.router, prefix="/api/v1")
app.add_exception_handler(BusinessException, business_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(IntegrityError, database_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)


@app.get("/")
async def root():
    return {"message": "Server Running..."}


@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        print("database connected!")
