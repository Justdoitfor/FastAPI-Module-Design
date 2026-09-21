class AppException(Exception):
    """所有业务的基类，Service层只抛出这类异常"""
    status_code: int = 400
    code: str = "app_error"
    message: str = "Request Fail"

    def __init__(self, message: str = None) -> None:
        self.message = message
        super().__init__(self.message)

class NotFoundError(AppException):
    status_code: int = 404
    code: str = "not_found"
    message: str = "Resource Not Found"

class PermissionDeniedError(AppException):
    status_code: int = 403
    code: str = "permission_denied"
    message: str = "Permission Denied"

class ConflictError(AppException):
    status_code: int = 409
    code: str = "conflict"
    message: str = "Resource Conflict"

class BusinessError(AppException):
    """违反业务规则（参数合法，业务不允许）"""
    status_code: int = 422
    code: str = "business_rule_violation"
    message: str = "Business Rule Violation"