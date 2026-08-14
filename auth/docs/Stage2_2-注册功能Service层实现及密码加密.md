# Service层实现
## Servic层职责
```text
用户请求
   ↓
Router(API层)
   ↓
AuthService(业务层)
   ↓
UserRepository(数据层)
   ↓
PostgreSQL
```

| 层          | 负责什么      |
| ---------- | --------- |
| Router     | 接收请求、返回响应 |
| Schema     | 数据校验      |
| Service    | 业务流程      |
| Repository | 数据库操作     |
| Model      | 数据结构      |

## 实现密码加密工具
security.py
```python
from passlib.context import CryptContext


pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
)

def hash_password(
    password: str
) -> str:
    """
    密码加密
    """
    
    return pwd_context.hash(password)

def verify_password(
    plain_password: str,
    hashed_password: str,
) -> bool:
    """
    校验密码
    """
    
    return pwd_context.verify(
        plain_password,
        hashed_password
    )
```
单独提取security.py方便后续安全相关扩展，例如：
- JWT
- Token解析
- 加密算法
- 权限校验

## 创建AuthService
service/auth_service.py
```python
from auth.repositories.user_repository import UserRepository
from auth.schemas.auth import UserRegisterRequest
from auth.models.user import User


class AuthService:

    def __init__(
        self,
        user_repository: UserRepository
    ):

        self.user_repository = user_repository

    async def register(
        self,
        data: UserRegisterRequest
    ) -> User:

        pass
```
Service 通过构造函数接收Repository通过依赖注入实现

## 完善register()处理流程
```python
from auth.core.security import hash_password
from auth.repositories.user_repository import UserRepository
from auth.schemas.auth import UserRegisterRequest
from auth.models.user import User


class AuthService:
    def __init__(self, user_repository: UserRepository):
        self.user_repository = user_repository

    async def register(
            self,
            data: UserRegisterRequest
    ) -> User:
        # 检查用户名是否存在
        exist_user = await self.user_repository.get_by_username(data.username)
        if exist_user:
            raise ValueError(
                "username already exists!"
            )
        # 检查邮箱是否存在
        exist_user = await self.user_repository.get_by_email(data.email)
        if exist_user:
            raise ValueError(
                "email already exists!"
            )
        # 密码加密
        password_hash = hash_password(data.password)
        # 创建ORM对象
        user = User(
            username=data.username,
            email=data.email,
            password_hash=password_hash,
        )
        return await self.user_repository.create(user)

```
