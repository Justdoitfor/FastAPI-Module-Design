# FastAPI Dependency注入和完整注册接口实现

## 创建Repository Dependency
auth/dependencies/repositories.py
```python
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from auth.database.session import get_db
from auth.repositories.user_repository import UserRepository


def get_user_repository(db: AsyncSession = Depends(get_db)):
    return UserRepository(db)

```

## 创建Service Dependency
auth/dependencies/services.py
```python
from fastapi import Depends

from auth.repositories.user_repository import UserRepository
from auth.dependencies.repositories import get_user_repository
from auth.services.auth_service import AuthService


def get_auth_service(user_repository: UserRepository = Depends(get_user_repository)):
    return AuthService(user_repository)

```

## 创建Auth Router
auth/api/auth.py
```python
from fastapi import Depends, APIRouter
from auth.schemas.auth import UserRegisterRequest
from auth.schemas.user import UserResponse
from auth.services.auth_service import AuthService
from auth.dependencies.services import get_auth_service

router = APIRouter(
    prefix="/auth",
    tags=["Auth"],
)


@router.post("/register", response_model=UserResponse)
async def register(
        data: UserRegisterRequest,
        service: AuthService = Depends(get_auth_service),
):
    user = await service.register(data)
    return user

```

## 注册Router到main.py
main.py
```python
from auth.api import auth

app.include_router(auth.router, prefix="/api/v1")
```

## 启动测试
```bash
uvicorn app.main:app --reload
```

## 验证数据库
```bash
docker exec -it auth_postgres psql -U postgres -d auth_system
```
查询数据库 
```sql
select * from users;
```