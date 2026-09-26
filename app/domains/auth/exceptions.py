from app.core.exceptions import AppException


class InvalidCredentialsError(AppException):
    status_code = 401
    code = "invalid_credentials"
    message = "邮箱或密码错误"

class InvalidTokenError(AppException):
    status_code = 401
    code = "invalid_token"
    message = "登录状态无效，请重新登录"

class TokenReuseDetectedError(AppException):
    status_code = 401
    code = "token_reuse_detected"
    message = "检测到异常登录状态，已强制下线，请重新登录"

class InactiveUserError(AppException):
    status_code = 401
    code = "inactive_user"
    message = "账户已禁用"