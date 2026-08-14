from enum import Enum


class ErrorCode(Enum):
    """
    系统错误码定义
    """

    def __init__(
            self,
            code: int,
            message: str,
            status_code: int,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code

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
        "email already exists",
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

    INVALID_REFRESH_TOKEN = (
        11002,
        "invalid refresh token",
        401
    )

    Refresh_Token_Reuse = (
        11003,
        "refresh token reuse",
        401
    )
