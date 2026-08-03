from fastapi import Request
from fastapi.responses import JSONResponse

from auth.core.error_codes import ErrorCode
from auth.core.exceptions import BusinessException
from auth.core.response import error_response
from fastapi.exceptions import RequestValidationError
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from auth.schemas.response import ResponseModel


async def business_exception_handler(request: Request, exc: BusinessException):
    return JSONResponse(
        status_code=exc.status_code,
        content=error_response(exc.error_code).model_dump()
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=error_response(ErrorCode.INVALID_PARAMS).model_dump()
    )


async def http_exception_handler(request: Request, exc: HTTPException):
    response = ResponseModel(
        code=exc.status_code,
        message=exc.detail,
        data=None
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=response.model_dump()
    )


async def database_exception_handler(request: Request, exc: IntegrityError):
    return JSONResponse(
        status_code=500,
        content=error_response(ErrorCode.DATABASE_ERROR).model_dump()
    )


async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content=error_response(ErrorCode.INTERNAL_ERROR).model_dump()
    )
