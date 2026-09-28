# FastAPI 生产级模块实战 ③：缓存与限流

> 前置：①已建立 `app/core`、`app/db`、`app/domains/auth`；②已建立 `app/domains/tasks`。
> 本篇引入 Redis，给 `tasks` 域加上 Cache-Aside 缓存，并给全局与登录接口加上滑动窗口限流。

---

## 0. 本篇在整体结构里的位置

```
app/
├── core/
│   ├── config.py             ← REDIS_URL、RATE_LIMIT_ENABLED 等配置项
│   ├── redis.py               ← 新增：Redis 客户端工厂
│   └── exceptions.py          ← 新增 RateLimitError；AppException 基类新增 headers 支持
├── cache/                     ← 新增顶层包：缓存与限流是技术能力，不属于任何业务域
│   ├── cache.py               ← Cache-Aside 封装
│   └── rate_limiter.py        ← 滑动窗口限流核心（Lua）
├── domains/
│   ├── auth/
│   │   └── router.py          ← 登录接口挂载限流依赖
│   └── tasks/
│       └── service.py         ← 接入缓存
├── api/
│   ├── deps.py                ← 新增 get_redis / get_cache / get_rate_limiter
│   ├── rate_limit.py           ← 限流依赖工厂（横切能力，放 api/ 而非某个域）
│   └── v1/router.py            ← 挂载全局限流
└── main.py                    ← lifespan 管理 Redis 连接池
```

**判断依据**：缓存和限流是"任何域都可能用到的技术能力"（`tasks` 用缓存，`auth` 用限流），符合《项目结构设计》里"两个以上域共用才提升到项目级"的标准，因此新建独立顶层包 `app/cache/`，而不是塞进某个 `domains/` 子目录。`api/rate_limit.py` 里的限流依赖工厂同理——它是"给任意路由挂限流"的通用能力，不专属于哪个域，所以放在横切的 `api/` 目录，而不是 `domains/auth/` 或 `domains/tasks/` 内部。

---

## 1. 技术栈与核心设计

```bash
uv add redis
```

- **Cache-Aside**：读缓存 → 未命中回源 → 写缓存；写数据 → commit → 删缓存。
- **穿透/击穿/雪崩**对策：空值缓存、单飞锁、TTL 随机抖动。
- **列表缓存靠版本号失效**：`list:{user_id}:v{ver}:{query_hash}`，写入时 `INCR` 版本号，旧 key 自然作废。
- **滑动窗口限流**：Redis + Lua 原子执行"读→判断→写"三步，避免并发竞态；用 Redis `TIME` 而非本地时钟，避免多实例时钟漂移。

---

## 2. 项目级基础设施

### 2.1 配置

```python
# app/core/config.py（追加字段）
class Settings(BaseSettings):
    ...
    REDIS_URL: str = "redis://localhost:6379/0"
    RATE_LIMIT_ENABLED: bool = True
```

### 2.2 Redis 客户端工厂

```python
# app/core/redis.py
from redis.asyncio import Redis


def create_redis(url: str) -> Redis:
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
        health_check_interval=30,
        max_connections=50,
    )
```

### 2.3 异常扩展

```python
# app/core/exceptions.py（AppException 基类新增 headers 支持；新增 RateLimitError）
class AppException(Exception):
    status_code: int = 400
    code: str = "app_error"
    message: str = "请求失败"

    def __init__(self, message: str | None = None, *, headers: dict[str, str] | None = None) -> None:
        self.message = message or self.message
        self.headers = headers
        super().__init__(self.message)


class RateLimitError(AppException):
    status_code = 429
    code = "rate_limited"
    message = "请求过于频繁，请稍后再试"
```

```python
# app/core/exception_handlers.py（透传 headers，支持 429 携带 Retry-After）
@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message},
        headers=exc.headers,
    )
```

> `RateLimitError` 放在项目级 `core/exceptions.py`，因为限流会作用于任意域的任意接口（`auth` 的登录、`tasks` 的创建接口都会用到），是跨域通用的异常语义。

---

## 3. `app/cache/`：缓存与限流的核心实现

### 3.1 Cache-Aside 封装

```python
# app/cache/cache.py
import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)

_NULL = "__null__"
_LOCK_TTL = 10
_VERSION_TTL = 7 * 86400


class Cache:
    """Cache-Aside 封装。任何 Redis 异常都降级为直接回源，缓存只是优化，不是依赖。"""

    def __init__(self, redis: Redis, prefix: str = "app") -> None:
        self.redis = redis
        self.prefix = prefix

    def key(self, *parts: object) -> str:
        return ":".join((self.prefix, *map(str, parts)))

    async def get_or_load(
        self,
        key: str,
        model: type[M],
        loader: Callable[[], Awaitable[M | None]],
        *,
        ttl: int,
        null_ttl: int = 30,
    ) -> M | None:
        hit, value = await self._read(key, model)
        if hit:
            return value

        lock_key = f"{key}:lock"
        acquired = await self._acquire_lock(lock_key)
        try:
            if not acquired:
                for _ in range(20):
                    await asyncio.sleep(0.05)
                    hit, value = await self._read(key, model)
                    if hit:
                        return value

            value = await loader()
            await self._write(key, value, ttl, null_ttl)
            return value
        finally:
            if acquired:
                await self._release_lock(lock_key)

    async def delete(self, *keys: str) -> None:
        try:
            await self.redis.delete(*keys)
        except RedisError:
            logger.warning("cache delete failed: %s", keys, exc_info=True)

    async def version(self, scope: str) -> int:
        try:
            return int(await self.redis.get(self.key("ver", scope)) or 0)
        except RedisError:
            logger.warning("cache version read failed: %s", scope, exc_info=True)
            return 0

    async def bump_version(self, scope: str) -> None:
        key = self.key("ver", scope)
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, _VERSION_TTL)
                await pipe.execute()
        except RedisError:
            logger.warning("cache version bump failed: %s", scope, exc_info=True)

    async def _read(self, key: str, model: type[M]) -> tuple[bool, M | None]:
        try:
            raw = await self.redis.get(key)
        except RedisError:
            logger.warning("cache read failed: %s", key, exc_info=True)
            return False, None

        if raw is None:
            return False, None
        if raw == _NULL:
            return True, None
        try:
            return True, model.model_validate_json(raw)
        except ValidationError:
            logger.warning("cache payload invalid, treat as miss: %s", key)
            return False, None

    async def _write(self, key: str, value: BaseModel | None, ttl: int, null_ttl: int) -> None:
        if value is None:
            payload, expire = _NULL, null_ttl
        else:
            payload, expire = value.model_dump_json(), ttl
        expire += random.randint(0, max(1, expire // 10))
        try:
            await self.redis.set(key, payload, ex=expire)
        except RedisError:
            logger.warning("cache write failed: %s", key, exc_info=True)

    async def _acquire_lock(self, lock_key: str) -> bool:
        try:
            return bool(await self.redis.set(lock_key, "1", nx=True, ex=_LOCK_TTL))
        except RedisError:
            return True

    async def _release_lock(self, lock_key: str) -> None:
        try:
            await self.redis.delete(lock_key)
        except RedisError:
            pass
```

### 3.2 滑动窗口限流

```python
# app/cache/rate_limiter.py
import logging
from dataclasses import dataclass
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

_SLIDING_WINDOW_LUA = """
local key    = KEYS[1]
local window = tonumber(ARGV[1])
local limit  = tonumber(ARGV[2])
local member = ARGV[3]

local t   = redis.call('TIME')
local now = t[1] * 1000 + math.floor(t[2] / 1000)

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)

if count < limit then
  redis.call('ZADD', key, now, member)
  redis.call('PEXPIRE', key, window)
  return {1, limit - count - 1, 0}
end

local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local retry  = window - (now - tonumber(oldest[2]))
return {0, 0, retry}
"""


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_ms: int = 0


class RateLimiter:
    def __init__(self, redis: Redis, prefix: str = "app:rl") -> None:
        self._prefix = prefix
        self._script = redis.register_script(_SLIDING_WINDOW_LUA)

    async def hit(
        self, key: str, *, limit: int, window: int, fail_open: bool = True
    ) -> RateLimitResult:
        try:
            allowed, remaining, retry_ms = await self._script(
                keys=[f"{self._prefix}:{key}"], args=[window * 1000, limit, uuid4().hex]
            )
        except RedisError:
            logger.error("rate limiter unavailable", exc_info=True)
            if fail_open:
                return RateLimitResult(allowed=True, remaining=limit)
            return RateLimitResult(allowed=False, remaining=0, retry_after_ms=1000)

        return RateLimitResult(bool(allowed), int(remaining), int(retry_ms))
```

---

## 4. 依赖注入与限流依赖工厂

```python
# app/api/deps.py（追加）
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis

from app.cache.cache import Cache
from app.cache.rate_limiter import RateLimiter


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


RedisDep = Annotated[Redis, Depends(get_redis)]


def get_cache(redis: RedisDep) -> Cache:
    return Cache(redis)


def get_rate_limiter(redis: RedisDep) -> RateLimiter:
    return RateLimiter(redis)


CacheDep = Annotated[Cache, Depends(get_cache)]
LimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
```

```python
# app/api/rate_limit.py —— 限流依赖工厂：横切能力，任何域的任何路由都能挂载
import math
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request, Response

from app.api.deps import LimiterDep
from app.core.config import settings
from app.core.exceptions import RateLimitError
from app.domains.auth.deps import get_current_user
from app.domains.auth.models import User


async def ip_identifier(request: Request) -> str:
    return f"ip:{request.client.host if request.client else 'unknown'}"


async def user_identifier(user: Annotated[User, Depends(get_current_user)]) -> str:
    return f"user:{user.id}"


def rate_limit(
    *,
    limit: int,
    window: int,
    scope: str,
    identifier: Callable = ip_identifier,
    fail_open: bool = True,
):
    async def dependency(
        response: Response,
        limiter: LimiterDep,
        ident: Annotated[str, Depends(identifier)],
    ) -> None:
        if not settings.RATE_LIMIT_ENABLED:
            return

        result = await limiter.hit(
            f"{scope}:{ident}", limit=limit, window=window, fail_open=fail_open
        )
        headers = {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(result.remaining),
        }
        if not result.allowed:
            headers["Retry-After"] = str(max(1, math.ceil(result.retry_after_ms / 1000)))
            raise RateLimitError(headers=headers)

        response.headers.update(headers)

    return dependency
```

> `app/api/rate_limit.py` 里 `from app.domains.auth.deps import get_current_user`——`user_identifier` 需要知道"当前请求的用户是谁"，这是横切能力对域公开接口的合理依赖（方向：`api/` → `domains/auth/`，与 `tasks` 依赖 `auth` 是同一种关系）。

```python
# app/main.py
from contextlib import asynccontextmanager

from fastapi import FastAPI
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.core.redis import create_redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = create_redis(settings.REDIS_URL)
    yield
    await app.state.redis.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="FastAPI-Module-Design",
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()

```

---

## 5. 接入 `tasks` 域：缓存

```python
# app/domains/tasks/service.py
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessError, NotFoundError
from app.domains.auth.models import User
from app.domains.tasks.models import Task
from app.domains.tasks.repository import TaskRepository
from app.domains.tasks.schemas import TaskCreate, TaskQuery, TaskUpdate, TaskRead

import hashlib
from app.cache.cache import Cache
from app.schemas.common import Page


class TaskService:
    DETAIL_TTL = 300
    LIST_TTL = 60

    def __init__(self, session: AsyncSession, cache: Cache) -> None:
        self.session = session
        self.cache = cache
        self.tasks = TaskRepository(session)

    async def list_tasks(self, user: User, query: TaskQuery) -> Page[TaskRead]:
        version = await self.cache.version(self._list_scope(user.id))
        key = self.cache.key("list", user.id, f"v{version}", self._digest(query))

        async def load() -> Page[TaskRead]:
            tasks, total = await self.tasks.list_by_owner(user.id, query)
            return Page[TaskRead].build(items=tasks, total=total, params=query)

        page = await self.cache.get_or_load(key, Page[TaskRead], load, ttl=self.LIST_TTL)
        assert page is not None
        return page

    async def get_task(self, user: User, task_id: int) -> TaskRead:
        async def load() -> TaskRead | None:
            task = await self.tasks.get(task_id)
            return TaskRead.model_validate(task) if task else None

        task = await self.cache.get_or_load(
            self.cache.key("task", task_id),
            TaskRead,
            load,
            ttl=self.DETAIL_TTL
        )
        if task is None or task.owner_id != user.id:
            raise NotFoundError("任务不存在")
        return task

    async def create_task(self, user: User, data: TaskCreate) -> TaskRead:
        self._validate_due_date(data.due_date)
        task = Task(**data.model_dump(), owner_id=user.id)
        await self.tasks.create(task)
        await self.session.commit()
        await self._invalidate(user.id, task.id)
        return TaskRead.model_validate(task)

    async def update_task(self, user: User, task_id: int, data: TaskUpdate) -> TaskRead:
        task = await self._get_owned_task(user, task_id)
        values = data.model_dump(exclude_unset=True)
        if "due_date" in values:
            self._validate_due_date(values["due_date"])
        await self.tasks.update(task, values)
        await self.session.commit()
        await self._invalidate(user.id, task.id)

        return TaskRead.model_validate(task)

    async def delete_task(self, user: User, task_id: int) -> None:
        task = await self._get_owned_task(user, task_id)
        await self.tasks.soft_delete(task)
        await self.session.commit()
        await self._invalidate(user.id, task.id)

    async def _get_owned_task(self, user: User, task_id: int) -> Task:
        task = await self.tasks.get_owned(task_id, user.id)
        if task is None:
            raise NotFoundError("任务不存在")
        return task

    @staticmethod
    def _validate_due_date(due_date: datetime | None) -> None:
        if due_date is not None and due_date <= datetime.now(timezone.utc):
            raise BusinessError("截至时间不许晚于当前时间")

    async def _invalidate(self, user_id: int, task_id: int) -> None:
        await self.cache.delete(self.cache.key("task", task_id))
        await self.cache.bump_version(self._list_scope(user_id))

    @staticmethod
    def _list_scope( user_id: int) -> str:
        return f"tasks:{user_id}"

    @staticmethod
    def _digest(query: TaskQuery) -> str:
        return hashlib.sha256(query.model_dump_json().encode()).hexdigest()[:16]
```

```python
# app/domains/tasks/router.py
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbDep, CacheDep
from app.domains.auth.deps import ActiveUser
from app.domains.tasks.schemas import TaskCreate, TaskUpdate, TaskQuery, TaskRead
from app.domains.tasks.service import TaskService
from app.schemas.common import Page

from app.api.rate_limit import rate_limit, user_identifier

router = APIRouter(
    prefix="/tasks",
    dependencies=[Depends(rate_limit(limit=120, window=60, scope="tasks", identifier=user_identifier))],
    tags=["tasks"])


def get_task_service(session: DbDep, cache: CacheDep) -> TaskService:
    return TaskService(session, cache)


ServiceDep = Annotated[TaskService, Depends(get_task_service)]


@router.post(
    "",
    response_model=TaskRead,
    dependencies=[Depends(rate_limit(limit=20, window=60, scope="tasks:create", identifier=user_identifier))],
    status_code=status.HTTP_201_CREATED)
async def create_task(data: TaskCreate, user: ActiveUser, service: ServiceDep):
    return await service.create_task(user, data)


@router.get("", response_model=Page[TaskRead])
async def list_tasks(query: Annotated[TaskQuery, Query()], user: ActiveUser, service: ServiceDep):
    return await service.list_tasks(user, query)


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(task_id: int, user: ActiveUser, service: ServiceDep):
    return await service.get_task(user, task_id)


@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(task_id: int, data: TaskUpdate, user: ActiveUser, service: ServiceDep):
    return await service.update_task(user, task_id, data)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: int, user: ActiveUser, service: ServiceDep):
    await service.delete_task(user, task_id)

```

---

## 6. 接入 `auth` 域：登录接口限流

```python
# app/domains/auth/router.py（在 login 路由上追加限流依赖）
from fastapi import Depends

from app.api.rate_limit import rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    response_model=TokenPair,
    dependencies=[Depends(rate_limit(limit=5, window=60, scope="login", fail_open=False))],
)
async def login(data: UserLogin, service: ServiceDep):
    return await service.login(data.email, data.password)
```

> `fail_open=False`：Redis 故障时宁可暂时拒绝登录，也不能让暴力破解防护失效。这是唯一需要改动 `domains/auth/` 内部文件的地方，且只是给已有路由追加一个 `dependencies` 参数，函数体本身零改动。

```python
# app/api/v1/router.py（全局 IP 限流）
from fastapi import APIRouter, Depends

from app.api.rate_limit import rate_limit
from app.domains.auth.router import router as auth_router
from app.domains.tasks.router import router as tasks_router

api_router = APIRouter(
    dependencies=[Depends(rate_limit(limit=300, window=60, scope="global"))]
)
api_router.include_router(auth_router)
api_router.include_router(tasks_router)
```

```python
# app/domains/tasks/router.py（整个路由 + 写接口分别限流）
from app.api.rate_limit import rate_limit, user_identifier

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[
        Depends(rate_limit(limit=120, window=60, scope="tasks", identifier=user_identifier))
    ],
)


@router.post(
    "",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(rate_limit(limit=20, window=60, scope="tasks:create", identifier=user_identifier))
    ],
)
async def create_task(data: TaskCreate, user: ActiveUser, service: ServiceDep):
    return await service.create_task(user, data)
```

---

## 7. 测试

```bash
uv add "fakeredis[lua]"
```

```python
# tests/conftest.py 修改
import pytest_asyncio
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from fakeredis import FakeAsyncRedis

import app.models  # 保证所有 Model 在 create_all() 之前被加载
from app.core.config import settings
from app.db.base import Base
from app.domains.auth.models import User
from app.domains.auth.security import hash_password


async def _make_user(session_factory, email: str) -> User:
    async with session_factory() as session:
        user = User(email=email, hashed_password=hash_password("passw0rd"))
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest.fixture(autouse=True)
def _disable_rate_limit(monkeypatch):
    # 默认关闭限流：②里"连续创建 25 个任务"的用例会被 20 次/分钟的限制拦住
    # 限流相关测试再通过 打开
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)


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
async def redis():
    r = FakeAsyncRedis(decode_responses=True)
    yield r
    await r.flushall()
    await r.aclose()


@pytest_asyncio.fixture
async def user(session_factory) -> User:
    return await _make_user(session_factory, "owner@example.com")


@pytest_asyncio.fixture
async def other_user(session_factory) -> User:
    return await _make_user(session_factory, "other@example.com")

@pytest.fixture
def enable_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)

@pytest_asyncio.fixture
async def client(session_factory, redis):  # 不再依赖 user，也不再覆盖 get_current_user
    from httpx import ASGITransport, AsyncClient

    from app.api.deps import get_db, get_redis
    from app.main import app

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = lambda: redis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()

```

```python
# tests/domains/tasks/conftest.py
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_db, get_redis
from app.domains.auth.deps import get_current_user
from app.main import app


@pytest_asyncio.fixture
async def client(session_factory, redis, user):
    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_current_user] = lambda: user  # 只对 tasks 目录生效
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()

```

```python
# tests/domains/tasks/test_cache.py
import pytest

from app.core.config import settings
from app.domains.tasks.repository import TaskRepository

URL = "/api/v1/tasks"


@pytest.fixture
def db_calls(monkeypatch):
    counter = {"n": 0}
    original = TaskRepository.get

    async def spy(self, obj_id):
        counter["n"] += 1
        return await original(self, obj_id)

    monkeypatch.setattr(TaskRepository, "get", spy)
    return counter


async def test_detail_is_cached(client, db_calls):
    task_id = (await client.post(URL, json={"title": "t"})).json()["id"]
    await client.get(f"{URL}/{task_id}")
    await client.get(f"{URL}/{task_id}")
    assert db_calls["n"] == 1


async def test_detail_invalidated_on_update(client):
    task_id = (await client.post(URL, json={"title": "旧标题"})).json()["id"]
    await client.get(f"{URL}/{task_id}")
    await client.patch(f"{URL}/{task_id}", json={"title": "新标题"})
    resp = await client.get(f"{URL}/{task_id}")
    assert resp.json()["title"] == "新标题"


async def test_list_invalidated_on_create(client):
    assert (await client.get(URL)).json()["total"] == 0
    await client.post(URL, json={"title": "t"})
    assert (await client.get(URL)).json()["total"] == 1


async def test_cache_hit_still_checks_owner(client, other_user):
    from app.domains.auth.deps import get_current_user
    from app.main import app

    task_id = (await client.post(URL, json={"title": "私有"})).json()["id"]
    await client.get(f"{URL}/{task_id}")

    app.dependency_overrides[get_current_user] = lambda: other_user
    assert (await client.get(f"{URL}/{task_id}")).status_code == 404
```

```python
# tests/domains/tasks/test_rate_limit.py
import pytest

from app.core.config import settings

URL = "/api/v1/tasks"


@pytest.fixture
def enable_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)


async def test_create_is_rate_limited(client, enable_rate_limit):
    for _ in range(20):
        assert (await client.post(URL, json={"title": "x"})).status_code == 201

    resp = await client.post(URL, json={"title": "x"})
    assert resp.status_code == 429
    assert resp.json()["code"] == "rate_limited"
    assert int(resp.headers["Retry-After"]) >= 1
```

```python
# tests/domains/auth/test_login_rate_limit.py
import pytest

from app.core.config import settings

BASE = "/api/v1/auth"
CREDENTIALS = {"email": "a@example.com", "password": "passw0rd"}


@pytest.fixture
def enable_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)


async def test_login_is_rate_limited(client, enable_rate_limit):
    await client.post(f"{BASE}/register", json=CREDENTIALS)
    for _ in range(5):
        await client.post(f"{BASE}/login", json={**CREDENTIALS, "password": "wrong"})

    resp = await client.post(f"{BASE}/login", json=CREDENTIALS)
    assert resp.status_code == 429
```

```bash
pytest tests/domains/ -v
```

---
