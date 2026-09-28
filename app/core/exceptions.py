class AppException(Exception):
    """所有业务异常的基类。Service层只抛出这类异常"""
    status_code: int = 400
    code: str = "app_error"
    message: str = "请求失败"

    def __init__(
            self,
            message: str | None = None,
            *,
            headers: dict[str, str] | None = None,
    ):
        self.message = message or self.message
        self.headers = headers
        super().__init__(self.message)


class NotFoundError(AppException):
    status_code: int = 404
    code: str = "not_found"
    message = "资源不存在"


class ConflictError(AppException):
    status_code: int = 409
    code: str = "conflict"
    message = "资源冲突"


class BusinessError(AppException):
    status_code: int = 422
    code: str = "business_rule_violation"
    message = "不满足业务规则"

class RateLimitError(AppException):
    status_code: int = 429
    code: str = "rate_limited"
    message = "请求过于频繁，请稍后重试"
