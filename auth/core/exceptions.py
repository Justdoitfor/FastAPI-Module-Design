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