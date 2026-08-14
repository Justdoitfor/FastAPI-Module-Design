# 异常体系设计 + 统一响应规范
## 异常体系设计
```text
                  Exception
                      |
        --------------------------------
        |              |              |
BusinessException  RequestError   SystemError
        ↓              ↓              ↓
    业务错误       参数错误       系统错误
                       ↓
                  统一Response

```
### 错误码
auth/core/error_codes.py
```python
from enum import Enum


class ErrorCode(Enum):
    """
    系统错误码定义
    """

    # 通用错误
    SUCCESS = (
        0,
        "success",
        200
    )
    INVALID_PARAMS = (
        40001,
        "invalid params",
        422
    )

    # 用户模块
    USERNAME_EXISTS = (
        10001,
        "username already exists",
        409
    )
    EMAIL_EXISTS = (
        10002,
        "email exists",
        409
    )
    USER_NOT_EXISTS = (
        10003,
        "user not exists",
        404
    )

    # 认证模块
    INVALID_PASSWORD = (
        11001,
        "invalid password",
        401
    )

    # 系统异常
    INTERNAL_ERROR = (
        50000,
        "internal error",
        500
    )

    def __init__(
            self,
            code: int,
            message: str,
            status_code: int,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code

```
### 业务异常基类
auth/core/exceptions.py
```python
from auth.core.error_codes import ErrorCode

class BusinessException(Exception):
    """业务异常基类"""

    def __init__(
            self,
            error_code: ErrorCode,
    ):
        self.error_code = error_code
        super().__init__(error_code.message)

    @property
    def code(self):
        return self.error_code.code

    @property
    def message(self):
        return self.error_code.message

    @property
    def status_code(self):
        return self.error_code.status_code

class UsernameAlreadyExists(BusinessException):
    def __init__(self):
        super().__init__(
            ErrorCode.USERNAME_EXISTS
        )

class EmailAlreadyExists(BusinessException):
    def __init__(self):
        super().__init__(
            ErrorCode.EMAIL_EXISTS
        )
```

### 异常处理
auth/core/exception_handler.py
```python
from fastapi import Request
from fastapi.responses import JSONResponse
from auth.core.exceptions import BusinessException
from fastapi.exceptions import RequestValidationError
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

async def business_exception_handler(request: Request, exc: BusinessException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "data": None,
            "status_code": exc.status_code,
        }
    )

async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    message = errors[0]["msg"]
    return JSONResponse(
        status_code=422,
        content={
            "code": 40001,
            "message": message,
            "data": None,
        }
    )

async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.status_code,
            "message": exc.detail,
            "data": None,
        }
    )

async def database_exception_handler(request: Request, exc: IntegrityError):
    return JSONResponse(
        status_code=409,
        content={
            "code": 50001,
            "message": "database conflict",
            "data": None,
        }
    )

async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "code": 50000,
            "message": "internal server error",
            "data": None,
        }
    )
```

### 注册到main.py
main.py
```python
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

```

### 当前异常处理流程
用户名重复
```text
AuthService
↓
raise UsernameAlreadyExists()
↓
BusinessException Handler
↓
HTTP 409
↓
{
 code:10001,
 message:"username already exists"
}

```
系统错误
```text
Exception
↓
Global Handler
↓
HTTP500
```

## 统一响应模型
### 优化Response Schema
auth/schemas/response.py
```python
from typing import Generic, TypeVar
from pydantic import BaseModel


T = TypeVar('T')


class ResponseModel(BaseModel, Generic[T]):
    code: int = 0
    message: str = "success"
    data: T | None = None

```

### 创建响应工具类
auth/core/response.py
```python
from typing import Any
from auth.schemas.response import ResponseModel


def success(data: Any = None, message: str = "success") -> ResponseModel:
    return ResponseModel(code=0, data=data, message=message)

# 失败响应工具
def fail(code: int, message: str, data=None) -> ResponseModel:
    return ResponseModel(code=code, message=message, data=data)

```

### 修改注册接口
auth/api/auth.py
```python
from fastapi import Depends, APIRouter
from auth.schemas.auth import UserRegisterRequest
from auth.schemas.response import ResponseModel
from auth.schemas.user import UserResponse
from auth.services.auth_service import AuthService
from auth.dependencies.services import get_auth_service
from auth.core.response import success

router = APIRouter(
    prefix="/auth",
    tags=["Auth"],
)


@router.post("/register", response_model=ResponseModel[UserResponse])
async def register(
        data: UserRegisterRequest,
        service: AuthService = Depends(get_auth_service),
):
    user = await service.register(data)
    return success(user)

```

### 优化错误响应
auth/core/error_codes.py
```python
from enum import Enum


class ErrorCode(Enum):
    """
    系统错误码定义
    """

    # 通用错误

    SUCCESS = (
        0,
        "success",
        200
    )
    INVALID_PARAMS = (
        40001,
        "invalid params",
        422
    )

    # 用户模块
    USERNAME_EXISTS = (
        10001,
        "username already exists",
        409
    )
    EMAIL_EXISTS = (
        10002,
        "email exists",
        409
    )
    USER_NOT_EXISTS = (
        10003,
        "user not exists",
        404
    )

    # 认证模块
    INVALID_PASSWORD = (
        11001,
        "invalid password",
        401
    )

    # 系统异常
    INTERNAL_ERROR = (
        50000,
        "internal error",
        500
    )

    DATABASE_ERROR = (
        50001,
        "database error",
        500
    )

    def __init__(
            self,
            code: int,
            message: str,
            status_code: int,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code

```
成功时返回 success(data)
失败时返回 error_response(error_code)

### 异常响应
auth/core/exception_handler.py
```python
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

```

