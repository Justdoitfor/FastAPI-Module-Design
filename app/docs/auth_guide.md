# FastAPI 生产级模块实战 ①：认证系统

## 0. 整体结构

```
app/
├── core/               ← 项目级基础设施：本篇会建立 exceptions.py 的通用部分
├── db/                 ← 项目级基础设施：本篇会建立 base.py / session.py / repository.py
├── domains/
│   └── auth/           ← 业务代码
│       ├── models.py
│       ├── schemas.py
│       ├── repository.py
│       ├── security.py       # 密码哈希 + JWT：认证域私有实现细节，不放 core
│       ├── exceptions.py     # 认证专属异常
│       ├── service.py
│       ├── deps.py           # get_current_user：其他域会来 import 这个
│       └── router.py
├── api/
│   ├── deps.py         ← 只放 get_db 等全局共享依赖
│   └── v1/router.py    ← 汇总各域的 router（目前只有 auth 一个）
└── main.py
```

**判断依据回顾**：密码哈希、JWT 编解码只有 `auth` 域用得到，所以下沉进域内的 `security.py`；`BaseRepository`、`AppException` 是任何域都会用的技术能力，留在项目级的 `db/` 和 `core/`。之后②篇的 `tasks` 域接入时，会直接 `import app.db.repository.BaseRepository` 和 `import app.domains.auth.deps.ActiveUser`。

---

## 1. 技术选型

| 决策点        | 选择                                             |
| ------------- | ------------------------------------------------ |
| 密码哈希      | `pwdlib[argon2]`（Argon2id）                     |
| JWT 编解码    | `PyJWT`                                          |
| Access Token  | JWT，15 分钟，无状态                             |
| Refresh Token | 随机字符串，哈希后存库，一次性 + 轮换 + 重用检测 |

```bash
uv add "pwdlib[argon2]" pyjwt "python-multipart" sqlalchemy[asyncio] asyncpg alembic pydantic-settings email-validator
```

核心安全设计（轮换 + 重用检测的原理图）与上一版完全一致：每个 Refresh Token 只能用一次，换出的新 Token 与旧 Token 同属一个 `family_id`；若同一个已用 Token 被再次提交，判定为令牌被盗，撤销整条家族。不再重复展开，直接进入代码。

---

## 2. 项目级基础设施（供所有域复用）

### 2.1 通用异常基类

```python
# app/core/exceptions.py
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
```

> 这里只放**多个域通用**的异常。`InvalidCredentialsError` 这类只有 auth 域会抛出的异常，放进 `domains/auth/exceptions.py`（见 Step 4），不写进这个文件——这正是结构文档 3.3 节的判断标准。

```python
# app/core/exception_handlers.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import AppException


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
            headers=exc.headers,
        )
```

### 2.2 配置

```python
# app/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

    DATABASE_URL: str
    REDIS_URL: str
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = 'HS256'
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30


settings = Settings()
```

### 2.3 数据库基础设施

```python
# app/db/base.py
# 将每张表都包含的字段抽取为 Mixin
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
        index=True
    )
```

```python
# app/db/session.py
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession
)

from app.core.config import settings

# 使用异步engine，提高吞吐
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,  # 是否打印SQL调试
)
# 会话工厂
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)
```

### 2.4 泛型 Repository（全项目唯一一份）

```python
# app/db/repository.py
from typing import Generic, TypeVar
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

ModelT = TypeVar('ModelT', bound=Base)


class BaseRepository(Generic[ModelT]):
    """通用仓储：封装基础CRUD。只进行flush，不commit——事务边界由Service层进行决策"""
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _select(self) -> Select:
        return select(self.model)

    async def get(self, obj_id: int) -> ModelT | None:
        stmt = self._select().where(self.model.id == obj_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def update(self, obj: ModelT, values: dict) -> ModelT:
        for key, value in values.items():
            setattr(obj, key, value)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj
```

### 2.5 全局共享依赖

```python
# app/api/deps.py
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory


async def get_db():
    async with async_session_factory() as session:
        yield session


DbDep = Annotated[AsyncSession, Depends(get_db)]
```

> 这个文件今后只放"任何域都要用"的依赖（`get_db`、③篇起会加入的 `get_redis`/`get_storage`/`get_job_queue`）。`get_current_user` **不**放在这里，它是认证域的产出物，放进 `domains/auth/deps.py`——下一节会看到其他域将如何 import 它。

---

## 3. `domains/auth/` 内部实现

从这里开始，所有文件都在 `app/domains/auth/` 目录下，域内部依然是完整的 API → Service → Repository → Database 分层，只是收在了一个文件夹里。

```
app/domains/auth/
├── __init__.py
├── models.py
├── schemas.py
├── security.py       # 密码哈希 + JWT：域内私有实现细节
├── exceptions.py      # 认证专属异常
├── repository.py
├── service.py
├── deps.py            # get_current_user（其他域会来 import）
└── router.py
```

### Step 1：认证专属异常

```python
# app/domains/auth/exceptions.py
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
```

> 这四个异常只有 `auth` 域会抛出，所以放在域内，而不是塞进项目级的 `core/exceptions.py`——`core` 只留 `NotFoundError`/`ConflictError`/`BusinessError` 这类多个域通用的语义。全局异常处理器（`core/exception_handlers.py`）不需要知道这些子类的存在：它只依赖 `AppException` 这个基类接口，新增域异常不需要改动处理器代码。

### Step 2：数据模型

```python
# app/domains/auth/models.py
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
```

```python
# app/domains/auth/models.py（续，同一文件）
from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column


class RefreshToken(Base, TimestampMixin):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    family_id: Mapped[str] = mapped_column(String(36), index=True, default=lambda: uuid4().hex)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
```

> `User` 和 `RefreshToken` 是"一个域内紧耦合的两张表"，同放一个 `models.py` 文件完全合适（域内不需要像项目级那样一个概念一个文件）；如果将来这个域的表变多到五六张，可以再拆成 `models/user.py` + `models/refresh_token.py` 子模块，是否拆分取决于文件长度，不影响本篇结构原则。

```python
# app/models/__init__.py —— 项目级汇总，仅供 Alembic 发现所有 Model
from app.domains.auth.models import RefreshToken, User  # noqa: F401
```

```bash
alembic init alembic
# alembic/env.py 中：import app.models; target_metadata = Base.metadata
alembic revision --autogenerate -m "create users and refresh_tokens"
alembic upgrade head
```

### Step 3：安全工具（密码哈希 + JWT，域内私有）

```python
# app/domains/auth/security.py
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum

import jwt
from pwdlib import PasswordHash

from app.core.config import settings
from app.domains.auth.exceptions import InvalidTokenError

password_hasher = PasswordHash.recommended()


def hash_password(plain_password: str) -> str:
    return password_hasher.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hasher.verify(plain_password, hashed_password)


def need_rehash(hashed_password: str) -> bool:
    return not password_hasher.verify_and_update(hashed_password, hashed_password)[0]

class TokenType(Enum):
    ACCESS = 'access'
    REFRESH = 'refresh'

def create_access_token(*, user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": TokenType.ACCESS.value,
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )

def decode_access_token(token: str) -> int:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM]
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError() from exc

    if payload.get('type') != TokenType.ACCESS.value:
        raise InvalidTokenError()

    try:
        return int(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise InvalidTokenError() from exc
```


### Step 4：Repository

```python
# app/domains/auth/repository.py
from datetime import datetime, timezone

from pydantic import EmailStr
from sqlalchemy import select, update

from app.db.repository import BaseRepository
from app.domains.auth.models import User, RefreshToken


class UserRepository(BaseRepository):
    model = User

    async def get_by_email(self, email: EmailStr) -> User | None:
        stmt = self._select().where(User.email == email)
        return (await self.session.execute(stmt)).scalar_one_or_none()


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    model = RefreshToken

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        stmt = self._select().where(RefreshToken.token_hash == token_hash)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def revoke_family(self, family_id: str) -> None:
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc))
        )
        await self.session.execute(stmt)

    async def revoke(self, token: RefreshToken) -> None:
        token.revoked_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def list_active_family_ids(self, user_id: int) -> set[str]:
        stmt = select(RefreshToken.family_id).where(RefreshToken.user_id == user_id,
                                                    RefreshToken.revoked_at.is_(None))
        return {
            row[0] for row in (await self.session.execute(stmt)).all()
        }
```

> 两个 Repository 都从 `app.db.repository.BaseRepository` 继承——这是项目里唯一一份泛型基类。②篇的 `TaskRepository` 会用一模一样的方式继承同一个类。

### Step 5：Schemas

```python
# app/domains/auth/schemas.py
import re

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
            raise ValueError("密码需同时包含字母和数字")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    is_active: bool


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str
```

> `UserRead` 是其他域将来会用到的类型（比如②篇 Task 的 `owner` 字段想展示用户信息时）。跨域引用 Schema 是允许的——**Schema 是数据契约，不是实现细节**，与"不能反向依赖"的规则不冲突（`tasks` 依赖 `auth.schemas.UserRead` 属于结构文档 3.4 节说的"单向依赖，方向向上"）。真正要严格隔离、不外泄的是 `security.py` 这类实现细节。

### Step 6：AuthService（业务逻辑核心，与上一版完全相同）

```python
# app/domains/auth/service.py
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from pydantic import EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ConflictError
from app.domains.auth.exceptions import (
    InactiveUserError,
    InvalidCredentialsError,
    InvalidTokenError,
    TokenReuseDetectedError,
)

from app.domains.auth.models import RefreshToken, User
from app.domains.auth.repository import RefreshTokenRepository, UserRepository
from app.domains.auth.schemas import TokenPair, UserCreate
from app.domains.auth.security import create_access_token, hash_password, need_rehash, verify_password


def _new_raw_token() -> str:
    return secrets.token_urlsafe(32)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


class AuthService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.users = UserRepository(session)
        self.refresh_tokens = RefreshTokenRepository(session)

    async def register(self, data: UserCreate) -> User:
        if await self.users.get_by_email(data.email) is not None:
            raise ConflictError("改邮箱已被注册")
        user = User(
            email=data.email,
            hashed_password=hash_password(data.password),
        )
        await self.users.create(user)
        await self.session.commit()
        return user

    async def login(self, email: EmailStr, password: str) -> TokenPair:
        user = await self.users.get_by_email(email)
        dummy_hash = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHRzYWx0$dGVzdGRpZ2VzdGRpZ2VzdA"
        if user is None:
            verify_password(password, dummy_hash)
            raise InvalidCredentialsError()
        if not verify_password(password, user.hashed_password):
            raise InvalidCredentialsError()
        if need_rehash(user.hashed_password):
            await self.users.update(user, {"hashed_password": hash_password(password)})
        return await self._issue_tokens(user.id, family_id=None)

    async def refresh(self, raw_token: str) -> TokenPair:
        token_hash = _hash_token(raw_token)
        stored = await self.refresh_tokens.get_by_hash(token_hash)

        if stored is None:
            raise InvalidTokenError()

        now = datetime.now(timezone.utc)
        if stored.revoked_at is not None:
            raise InvalidTokenError()

        if stored.expires_at.timestamp() < now.timestamp():
            raise InvalidTokenError()

        if stored.used_at is not None:
            await self.refresh_tokens.revoke_family(stored.family_id)
            await self.session.commit()
            raise TokenReuseDetectedError()

        stored.used_at = now
        await self.session.flush()
        return await self._issue_tokens(stored.user_id, family_id=stored.family_id)

    async def logout(self, raw_token: str) -> None:
        stored = await self.refresh_tokens.get_by_hash(_hash_token(raw_token))
        if stored is not None and stored.revoked_at is None:
            await self.refresh_tokens.revoke(stored)
        await self.session.commit()

    async def logout_all_devices(self, user_id: int) -> None:
        family_ids = await self.refresh_tokens.list_active_family_ids(user_id)
        for family_id in family_ids:
            await self.refresh_tokens.revoke_family(family_id)
        await self.session.commit()

    async def _issue_tokens(self, user_id: int, *, family_id: str | None) -> TokenPair:
        raw_token = _new_raw_token()
        record = RefreshToken(
            user_id=user_id,
            token_hash=_hash_token(raw_token),
            family_id=family_id or uuid.uuid4().hex,
            expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        )
        await self.refresh_tokens.create(record)
        await self.session.commit()
        return TokenPair(
            access_token=create_access_token(user_id=user_id),
            refresh_token=raw_token,
        )
```

> 注意 `list_active_family_ids` 是 Repository 方法，而不是在 Service 里直接写 `select` 语句——**Service 不直接碰 SQLAlchemy 的 `select`/`update`，一切数据库访问都通过 Repository**，这条规则在纵切结构下同样成立，甚至更值得强调：域内文件变多之后，"Service 里混入原始 SQL"的坏味道会更容易被忽略。

### Step 7：依赖注入（其他域会来 import 的部分）

```python
# app/domains/auth/deps.py
from typing import Annotated
from fastapi import Depends, HTTPException, status

from fastapi.security import OAuth2PasswordBearer
from app.api.deps import DbDep
from app.domains.auth.exceptions import InactiveUserError, InvalidTokenError
from app.domains.auth.models import User
from app.domains.auth.repository import UserRepository
from app.domains.auth.security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    auto_error=False,
)


async def get_current_user(
        session: DbDep,
        token: Annotated[str | None, Depends(oauth2_scheme)],
) -> User:
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证凭证",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = decode_access_token(token)
    user = await UserRepository(session).get(user_id)
    if user is None:
        raise InvalidTokenError
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_active_user(user: CurrentUser) -> User:
    if not user.is_active:
        raise InactiveUserError()
    return user


ActiveUser = Annotated[User, Depends(get_current_active_user)]

```

> **这是整个认证域向外暴露的"公共接口"**：以后 `domains/tasks/router.py` 只需要 `from app.domains.auth.deps import ActiveUser`，完全不需要知道认证内部是怎么做密码哈希、怎么签发 Token 的。这正是纵切结构的核心价值——**域与域之间通过精简的公共接口（`deps.py` 里的少数几个符号）交互，而不是互相翻对方的内部实现**。

### Step 8：Router

```python
# app/domains/auth/router.py
from typing import Annotated
from fastapi import APIRouter, Depends, status

from app.api.deps import DbDep
from app.domains.auth.deps import ActiveUser
from app.domains.auth.schemas import RefreshRequest, TokenPair, UserCreate, UserLogin, UserRead
from app.domains.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def get_auth_service(session: DbDep) -> AuthService:
    return AuthService(session)


ServiceDep = Annotated[AuthService, Depends(get_auth_service)]


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate, service: ServiceDep):
    return await service.register(data)


@router.post("/login", response_model=TokenPair, status_code=status.HTTP_200_OK)
async def login(data: UserLogin, service: ServiceDep):
    return await service.login(data.email, data.password)


@router.post("/refresh", response_model=TokenPair, status_code=status.HTTP_200_OK)
async def refresh(data: RefreshRequest, service: ServiceDep):
    return await service.refresh(data.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(data: RefreshRequest, service: ServiceDep):
    return await service.logout(data.refresh_token)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(user: ActiveUser, service: ServiceDep):
    await service.logout_all_devices(user.id)


@router.get("/me", response_model=UserRead)
async def get_me(user: ActiveUser):
    return user

```

---

## 4. 路由汇总与应用装配

```python
# app/api/v1/router.py —— 目前只有一个域；②篇接入后这里会再加一行 include_router
from fastapi import APIRouter

from app.domains.auth.router import router as auth_router

api_router = APIRouter()
api_router.include_router(auth_router)
```

```python
# app/main.py
from fastapi import FastAPI
from app.api.v1.router import api_router
from app.core.exception_handlers import register_exception_handlers


def create_app() -> FastAPI:
    app = FastAPI(
        title="FastAPI-Module-Design",
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()

```

---

## 5. 测试

测试目录同样按域组织：

```
tests/
├── conftest.py
└── domains/
    └── auth/
        └── test_auth.py
```

```python
# tests/conftest.py
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.db.base import Base
from app.main import app


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def client(session_factory):
    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
```

```python
# tests/domains/auth/test_auth.py
BASE = "/api/v1/auth"
CREDENTIALS = {"email": "a@example.com", "password": "passw0rd"}


async def _register_and_login(client) -> dict:
    await client.post(f"{BASE}/register", json=CREDENTIALS)
    resp = await client.post(f"{BASE}/login", json=CREDENTIALS)
    return resp.json()


# ---------------- 注册 ----------------
async def test_register_success(client):
    resp = await client.post(f"{BASE}/register", json=CREDENTIALS)
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == CREDENTIALS["email"]
    assert "password" not in body and "hashed_password" not in body


async def test_register_duplicate_email_rejected(client):
    await client.post(f"{BASE}/register", json=CREDENTIALS)
    resp = await client.post(f"{BASE}/register", json=CREDENTIALS)
    assert resp.status_code == 409
    assert resp.json()["code"] == "conflict"


async def test_register_weak_password_rejected(client):
    resp = await client.post(
        f"{BASE}/register", json={"email": "b@example.com", "password": "allletters"}
    )
    assert resp.status_code == 422


# ---------------- 登录 ----------------
async def test_login_success(client):
    await client.post(f"{BASE}/register", json=CREDENTIALS)
    resp = await client.post(f"{BASE}/login", json=CREDENTIALS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] and body["refresh_token"] and body["token_type"] == "bearer"


async def test_login_wrong_password(client):
    await client.post(f"{BASE}/register", json=CREDENTIALS)
    resp = await client.post(f"{BASE}/login", json={**CREDENTIALS, "password": "wrongpass1"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_credentials"


async def test_login_nonexistent_user_same_error_as_wrong_password(client):
    resp = await client.post(f"{BASE}/login", json={"email": "nobody@example.com", "password": "x1234567"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_credentials"


# ---------------- 受保护接口 ----------------
async def test_me_requires_token(client):
    assert (await client.get(f"{BASE}/me")).status_code == 401


async def test_me_with_valid_token(client):
    tokens = await _register_and_login(client)
    resp = await client.get(f"{BASE}/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == CREDENTIALS["email"]


async def test_me_with_garbage_token(client):
    resp = await client.get(f"{BASE}/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_token"


async def test_refresh_token_cannot_access_protected_endpoint(client):
    tokens = await _register_and_login(client)
    resp = await client.get(f"{BASE}/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"})
    assert resp.status_code == 401


# ---------------- 刷新与轮换 ----------------
async def test_refresh_issues_new_pair(client):
    tokens = await _register_and_login(client)
    resp = await client.post(f"{BASE}/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 200
    new_tokens = resp.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]
    assert new_tokens["access_token"] != tokens["access_token"]


async def test_refresh_reuse_is_detected_and_family_revoked(client):
    tokens = await _register_and_login(client)
    old_refresh = tokens["refresh_token"]

    first = await client.post(f"{BASE}/refresh", json={"refresh_token": old_refresh})
    new_refresh = first.json()["refresh_token"]

    second = await client.post(f"{BASE}/refresh", json={"refresh_token": old_refresh})
    assert second.status_code == 401
    assert second.json()["code"] == "token_reuse_detected"

    third = await client.post(f"{BASE}/refresh", json={"refresh_token": new_refresh})
    assert third.status_code == 401
    assert third.json()["code"] == "invalid_token"


async def test_refresh_with_unknown_token(client):
    resp = await client.post(f"{BASE}/refresh", json={"refresh_token": "does-not-exist"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_token"


# ---------------- 登出 ----------------
async def test_logout_revokes_refresh_token(client):
    tokens = await _register_and_login(client)
    assert (await client.post(f"{BASE}/logout", json={"refresh_token": tokens["refresh_token"]})).status_code == 204
    resp = await client.post(f"{BASE}/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


async def test_logout_is_idempotent(client):
    tokens = await _register_and_login(client)
    await client.post(f"{BASE}/logout", json={"refresh_token": tokens["refresh_token"]})
    assert (await client.post(f"{BASE}/logout", json={"refresh_token": tokens["refresh_token"]})).status_code == 204


async def test_logout_all_devices(client):
    tokens_1 = await _register_and_login(client)
    tokens_2 = (await client.post(f"{BASE}/login", json=CREDENTIALS)).json()

    await client.post(f"{BASE}/logout-all", headers={"Authorization": f"Bearer {tokens_1['access_token']}"})

    assert (await client.post(f"{BASE}/refresh", json={"refresh_token": tokens_1["refresh_token"]})).status_code == 401
    assert (await client.post(f"{BASE}/refresh", json={"refresh_token": tokens_2["refresh_token"]})).status_code == 401
```

```bash
pytest tests/domains/auth/ -v
```

---