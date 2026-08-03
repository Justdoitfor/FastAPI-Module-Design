from typing import Any
from auth.schemas.response import ResponseModel
from auth.core.error_codes import ErrorCode


def success(data: Any = None, message: str = "success") -> ResponseModel:
    return ResponseModel(code=0, data=data, message=message)


def error_response(error_code: ErrorCode):
    return ResponseModel(code=error_code.code, message=error_code.message, data=None)
