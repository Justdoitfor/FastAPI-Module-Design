from typing import Annotated
from pydantic import EmailStr, Field
from auth.schemas.base import BaseSchema

Username = Annotated[
    str,
    Field(
        min_length=3,
        max_length=20,
        description="用户名"
    )
]

Password = Annotated[
    str,
    Field(
        min_length=8,
        max_length=32,
        description="密码"
    )
]


class UserRegisterRequest(BaseSchema):
    """用户注册请求"""
    username: Username
    email: EmailStr
    password: Password


class UserLoginRequest(BaseSchema):
    """用户登录请求"""
    username: str
    password: str
