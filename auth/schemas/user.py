from pydantic import EmailStr
from auth.schemas.base import BaseSchema


class UserResponse(BaseSchema):
    """用户响应信息"""
    id: int
    username: str
    email: EmailStr
    avatar: str | None = None
    role: str
    is_active: bool
