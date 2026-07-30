"""
负责接口数据模型
例如：
{
 username:"",
 password:""
}
---------------->
对应：
class UserCreate(BaseModel):
    username:str
    password:str
"""

from .auth import UserRegisterRequest, UserLoginRequest
from .user import UserResponse

__all__ = [
    "UserRegisterRequest",
    "UserLoginRequest",
    "UserResponse",
]
