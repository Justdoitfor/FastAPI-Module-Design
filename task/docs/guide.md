# FastAPI 生产级模块实战 ②：业务资源模块（Task）

> 前置：已完成认证系统（API → Service → Repository → Database 分层）。
> 本篇目标：用同一套分层，实现一个**可直接复制为模板**的业务模块。

---

## 0. 学习路线图

| 顺序  | 模块                     | 核心能力                                                     | 状态       |
| ----- | ------------------------ | ------------------------------------------------------------ | ---------- |
| ①     | 认证系统                 | JWT、密码哈希、依赖注入                                      | ✅ 已完成   |
| **②** | **业务资源模块（本篇）** | **CRUD、资源级权限、分页/过滤/排序、软删除、统一异常、事务边界、测试** | **👈 现在** |
| ③     | 缓存与限流               | Redis、cache-aside、slowapi / 令牌桶                         |            |
| ④     | 后台任务与队列           | BackgroundTasks、arq / Celery、幂等                          |            |
| ⑤     | 文件上传与对象存储       | 流式上传、S3/MinIO、预签名 URL                               |            |
| ⑥     | 日志与可观测性           | 结构化日志、Request ID、Prometheus、OpenTelemetry            |            |
| ⑦     | 配置与部署               | pydantic-settings、Docker、gunicorn+uvicorn、CI              |            |

**理由**：②里学到的分层、异常、分页、事务边界、测试方法，是③～⑦的地基。跳过它直接学缓存，你会不知道缓存该放在哪一层。

### 本篇完成后可以掌握

1. 各层**职责边界**（什么代码该放哪一层）
2. 泛型 `BaseRepository`，消除重复 CRUD
3. **事务边界放在 Service 层**（一个用例 = 一个事务）
4. 资源级权限：只能操作自己的数据（防 IDOR 越权）
5. 分页 / 过滤 / 排序（Query 参数模型）
6. 软删除
7. 领域异常 + 全局异常处理器（Service 不依赖 HTTP）
8. Alembic 迁移
9. pytest + httpx 异步测试

### 技术栈与前提

- Python ≥ 3.10，FastAPI ≥ 0.115（Query 参数模型），Pydantic v2，SQLAlchemy 2.0 async，Alembic
- 认证模块应该包含以下东西（名字不同需要自行替换）：
  - `db.base.Base`（DeclarativeBase） 为了后续不同模块的通用，将db单独抽取放置统一Base
  - `db.session.get_db`（yield 一个 `AsyncSession`）
  - `auth.api.user.get_current_user`（返回 `User`）
  - `auth.models.user.User`（有 `id` 字段，表名 `users`）
- `async_sessionmaker` 必须设置 `expire_on_commit=False`，否则 commit 后访问属性会触发异步懒加载报 `MissingGreenlet`。

---

## 1. 分层职责（核心心智模型）

```
HTTP 请求
   │
   ▼
┌───────────────────────────────────────────────┐
│ API 层      解析请求 / 依赖注入 / 调 Service / 组装响应 │  只懂 HTTP
├───────────────────────────────────────────────┤
│ Service 层  业务规则 / 权限判断 / 事务边界(commit)     │  只懂业务，不懂 HTTP
├───────────────────────────────────────────────┤
│ Repository  构造查询 / 读写数据库 / flush             │  只懂 SQL，不懂业务
├───────────────────────────────────────────────┤
│ Database    PostgreSQL / SQLite                      │
└───────────────────────────────────────────────┘
```

**判断代码该放哪层的三个问题：**

| 问题                                   | 答案指向      |
| -------------------------------------- | ------------- |
| 它依赖 `Request`/`Response`/状态码吗？ | API 层        |
| 它是"业务规则"吗（能不能做、谁能做）？ | Service 层    |
| 它是"怎么查/怎么存"吗？                | Repository 层 |

**三条铁律：**

1. Service **不抛 `HTTPException`**，只抛领域异常，由全局处理器转成 HTTP。
2. Repository **只 `flush`，不 `commit`**，事务由 Service 决定。
3. API 层**不写业务判断**，也不直接碰 Repository。

---

## 2. 目录结构

```
db/
│   └── base.py                  # Base + Mixin        [修改/新增]
│   └── session.py               # 异步会话             [修改/新增]
task/
├── main.py
├── core/
│   ├── exceptions.py            # 领域异常            [新增]
│   └── exception_handlers.py    # 全局处理器          [新增]
├── models/
│   ├── __init__.py              # 导入所有模型(供 Alembic)
│   └── task.py                  # [新增]
├── schemas/
│   ├── common.py                # 分页通用模型        [新增]
│   └── task.py                  # [新增]
├── repositories/
│   ├── base.py                  # 泛型 BaseRepository [新增]
│   └── task.py                  # [新增]
├── services/
│   └── task.py                  # [新增]
└── api/
    └── v1/
        ├── router.py
        └── endpoints/
            └── tasks.py         # [新增]
tests/
├── conftest.py
└── test_tasks.py
```

---

## 3. 分步实现

### Step 1：领域异常 + 全局处理器

先建"错误语言"，后面每一层都用它。

```python
# app/core/exceptions.py
class AppException(Exception):
    """所有业务异常的基类。Service 层只抛这类异常。"""

    status_code: int = 400
    code: str = "app_error"
    message: str = "请求失败"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.message
        super().__init__(self.message)


class NotFoundError(AppException):
    status_code = 404
    code = "not_found"
    message = "资源不存在"


class PermissionDeniedError(AppException):
    status_code = 403
    code = "permission_denied"
    message = "无权执行此操作"


class ConflictError(AppException):
    status_code = 409
    code = "conflict"
    message = "资源冲突"


class BusinessError(AppException):
    """违反业务规则（参数格式合法，但业务上不允许）。"""

    status_code = 422
    code = "business_rule_violation"
    message = "不满足业务规则"
```

```python
# app/core/exception_handlers.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from task.core.exceptions import AppException


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message},
        )
```

> 如果你的认证模块已经有自己的异常，建议让它们也继承 `AppException`，统一错误格式：`{"code": "...", "message": "..."}`。

---

### Step 2：数据库基类与 Mixin

把"每张表都有"的字段抽成 Mixin。

```python
# app/db/base.py
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True
    )
```

---

### Step 3：Model

```python
# app/models/task.py
import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, SoftDeleteMixin, TimestampMixin


class TaskStatus(str, enum.Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class TaskPriority(enum.IntEnum):
    # 用整数存储，才能按优先级正确排序
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class Task(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "tasks"
    __table_args__ = (
        # 高频查询：某用户 + 某状态
        Index("ix_tasks_owner_status", "owner_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(
            TaskStatus,
            native_enum=False,  # 存字符串，避免 PG 原生 enum 迁移的麻烦
            length=20,
            values_callable=lambda e: [m.value for m in e],
        ),
        default=TaskStatus.TODO,
    )
    priority: Mapped[int] = mapped_column(SmallInteger, default=TaskPriority.MEDIUM)
    due_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
```

```python
# app/models/__init__.py
# 必须导入所有模型，Alembic autogenerate 才能发现它们
from app.models.task import Task  # noqa: F401
from app.models.user import User  # noqa: F401
```

---

### Step 4：Schemas（请求/响应模型）

```python
# app/schemas/common.py
import math
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageParams(BaseModel):
    page: int = Field(1, ge=1, description="页码，从 1 开始")
    size: int = Field(20, ge=1, le=100, description="每页条数，最大 100")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int
    pages: int

    @classmethod
    def build(cls, items: list, total: int, params: PageParams) -> "Page[T]":
        return cls(
            items=items,
            total=total,
            page=params.page,
            size=params.size,
            pages=math.ceil(total / params.size) if total else 0,
        )
```

```python
# app/schemas/task.py
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from task.models.task import TaskPriority, TaskStatus
from task.schemas.common import PageParams


class TaskBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    priority: TaskPriority = TaskPriority.MEDIUM
    due_date: AwareDatetime | None = None  # 强制带时区，杜绝 naive/aware 比较错误


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    """PATCH 语义：只更新客户端传了的字段。"""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_date: AwareDatetime | None = None

    @field_validator("title", "status", "priority")
    @classmethod
    def not_null_when_provided(cls, v):
        # 这些列在数据库中 NOT NULL：允许不传，但不允许显式传 null
        if v is None:
            raise ValueError("该字段不能为 null")
        return v


class TaskRead(TaskBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: TaskStatus
    owner_id: int
    created_at: datetime
    updated_at: datetime


class TaskQuery(PageParams):
    """列表查询参数：分页 + 过滤 + 排序。"""

    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    keyword: str | None = Field(default=None, max_length=100)
    sort_by: Literal["created_at", "due_date", "priority"] = "created_at"
    order: Literal["asc", "desc"] = "desc"
```

> **设计要点**：`sort_by` 用 `Literal` 白名单，客户端不可能传入任意列名，从源头避免注入/异常。

---

### Step 5：Repository（泛型基类 + 具体实现）

```python
# app/repositories/base.py
from datetime import datetime, timezone
from typing import Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """通用仓储：封装 CRUD 与分页。只 flush，不 commit。"""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _select(self) -> Select:
        """所有查询的起点：自动过滤已软删除的记录。"""
        stmt = select(self.model)
        if hasattr(self.model, "deleted_at"):
            stmt = stmt.where(self.model.deleted_at.is_(None))
        return stmt

    async def get(self, obj_id: int) -> ModelT | None:
        stmt = self._select().where(self.model.id == obj_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        await self.session.flush()
        await self.session.refresh(obj)  # 取回 server_default 生成的字段
        return obj

    async def update(self, obj: ModelT, values: dict) -> ModelT:
        for key, value in values.items():
            setattr(obj, key, value)
        await self.session.flush()
        await self.session.refresh(obj)  # 取回 onupdate 生成的 updated_at
        return obj

    async def soft_delete(self, obj: ModelT) -> None:
        obj.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def paginate(
        self, stmt: Select, *, offset: int, limit: int
    ) -> tuple[list[ModelT], int]:
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = (await self.session.execute(count_stmt)).scalar_one()
        rows = await self.session.scalars(stmt.offset(offset).limit(limit))
        return list(rows), total
```

```python
# app/repositories/task.py
from task.models.task import Task
from task.repositories.base import BaseRepository
from task.schemas.task import TaskQuery

_SORT_COLUMNS = {
    "created_at": Task.created_at,
    "due_date": Task.due_date,
    "priority": Task.priority,
}


class TaskRepository(BaseRepository[Task]):
    model = Task

    async def list_by_owner(
        self, owner_id: int, query: TaskQuery
    ) -> tuple[list[Task], int]:
        stmt = self._select().where(Task.owner_id == owner_id)

        if query.status:
            stmt = stmt.where(Task.status == query.status)
        if query.priority:
            stmt = stmt.where(Task.priority == query.priority)
        if query.keyword:
            # autoescape 会转义 % 和 _，防止用户用通配符拖慢查询
            stmt = stmt.where(Task.title.icontains(query.keyword, autoescape=True))

        column = _SORT_COLUMNS[query.sort_by]
        order = column.desc() if query.order == "desc" else column.asc()
        # 追加 id 作为次级排序，保证分页结果稳定、不重复
        stmt = stmt.order_by(order, Task.id.desc())

        return await self.paginate(stmt, offset=query.offset, limit=query.size)
```

---

### Step 6：Service（业务规则 + 事务边界）

```python
# app/services/task.py
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from task.core.exceptions import BusinessError, NotFoundError
from task.models.task import Task
from auth.models.user import User
from task.repositories.task import TaskRepository
from task.schemas.task import TaskCreate, TaskQuery, TaskUpdate


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session  # 事务边界由 Service 掌控
        self.tasks = TaskRepository(session)

    # ---------- 查询 ----------
    async def list_tasks(
        self, user: User, query: TaskQuery
    ) -> tuple[list[Task], int]:
        return await self.tasks.list_by_owner(user.id, query)

    async def get_task(self, user: User, task_id: int) -> Task:
        return await self._get_owned_task(user, task_id)

    # ---------- 写入 ----------
    async def create_task(self, user: User, data: TaskCreate) -> Task:
        self._validate_due_date(data.due_date)
        task = Task(**data.model_dump(), owner_id=user.id)
        await self.tasks.create(task)
        await self.session.commit()
        return task

    async def update_task(self, user: User, task_id: int, data: TaskUpdate) -> Task:
        task = await self._get_owned_task(user, task_id)
        values = data.model_dump(exclude_unset=True)  # 只取客户端传了的字段
        if "due_date" in values:
            self._validate_due_date(values["due_date"])
        await self.tasks.update(task, values)
        await self.session.commit()
        return task

    async def delete_task(self, user: User, task_id: int) -> None:
        task = await self._get_owned_task(user, task_id)
        await self.tasks.soft_delete(task)
        await self.session.commit()

    # ---------- 内部规则 ----------
    async def _get_owned_task(self, user: User, task_id: int) -> Task:
        task = await self.tasks.get(task_id)
        # 不存在 与 不属于你，统一返回 404：
        # 返回 403 会泄露"这个 ID 存在"，攻击者可据此枚举资源
        if task is None or task.owner_id != user.id:
            raise NotFoundError("任务不存在")
        return task

    @staticmethod
    def _validate_due_date(due_date: datetime | None) -> None:
        if due_date is not None and due_date <= datetime.now(timezone.utc):
            raise BusinessError("截止时间必须晚于当前时间")
```

> **为什么 commit 在 Service？** 一个用例可能涉及多个 Repository（例如"创建任务 + 写审计日志"），只有 Service 知道它们是否应该**同为一个事务**。Repository 只管"怎么写"，不管"何时提交"。

---

### Step 7：API 层

```python
# app/api/v1/endpoints/tasks.py
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from auth.api.user import get_current_user
from db.session import get_db
from auth.models.user import User
from task.schemas.common import Page
from task.schemas.task import TaskCreate, TaskQuery, TaskRead, TaskUpdate
from task.services.task import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])


def get_task_service(session: Annotated[AsyncSession, Depends(get_db)]) -> TaskService:
    return TaskService(session)


TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(data: TaskCreate, user: CurrentUser, service: TaskServiceDep):
    return await service.create_task(user, data)


@router.get("", response_model=Page[TaskRead])
async def list_tasks(
    query: Annotated[TaskQuery, Query()],  # FastAPI ≥0.115：Pydantic 模型作为查询参数
    user: CurrentUser,
    service: TaskServiceDep,
):
    tasks, total = await service.list_tasks(user, query)
    return Page[TaskRead].build(items=tasks, total=total, params=query)


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(task_id: int, user: CurrentUser, service: TaskServiceDep):
    return await service.get_task(user, task_id)


@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(
    task_id: int, data: TaskUpdate, user: CurrentUser, service: TaskServiceDep
):
    return await service.update_task(user, task_id, data)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: int, user: CurrentUser, service: TaskServiceDep):
    await service.delete_task(user, task_id)
```

```python
# app/api/v1/router.py
from fastapi import APIRouter

from task.api.v1.endpoints import auth, tasks  # auth 是你之前写的

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(tasks.router)
```

```python
# app/main.py
from fastapi import FastAPI
from task.api.v1.router import api_router
from task.core.exception_handlers import register_exception_handlers


def get_app() -> FastAPI:
    app = FastAPI(title="FastAPI-Module-Design-Tasks")
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = get_app()
```

---

### Step 8：数据库迁移

```bash
alembic revision --autogenerate -m "add tasks table"
# 打开生成的文件，人工检查一遍再执行（autogenerate 不是万能的）
alembic upgrade head
```

**检查清单：**

- `alembic/env.py` 中 `target_metadata = Base.metadata`，并且 `import app.models`
- 迁移脚本里有 `ix_tasks_owner_status` 索引
- 线上大表加索引时，PostgreSQL 用 `CREATE INDEX CONCURRENTLY`，避免锁表

---

### Step 9：测试

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

```bash
uv add pytest pytest-asyncio httpx aiosqlite
```

```python
# tests/conftest.py
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from auth.api.user import get_current_user
from db.session import get_db
from db.base import Base
from task.main import app
from auth.models.user import User


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,  # 内存库必须共用同一连接
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _make_user(session_factory, username: str, email: str) -> User:
    async with session_factory() as session:
        user = User(username=username,email=email, password_hash="x")  # 按你的 User 模型调整字段
        session.add(user)
        await session.commit()
        return user


@pytest_asyncio.fixture
async def user(session_factory) -> User:
    return await _make_user(session_factory, "user1","a@example.com")


@pytest_asyncio.fixture
async def other_user(session_factory) -> User:
    return await _make_user(session_factory, "user2","b@example.com")


@pytest_asyncio.fixture
async def client(session_factory, user):
    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()
```

```python
# tests/test_tasks.py
from datetime import datetime, timedelta, timezone

from auth.api.user import get_current_user
from task.main import app

URL = "/api/v1/tasks"


async def test_create_and_get_task(client):
    resp = await client.post(URL, json={"title": "写教程", "priority": 3})
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "写教程"
    assert body["status"] == "todo"

    resp = await client.get(f"{URL}/{body['id']}")
    assert resp.status_code == 200


async def test_due_date_in_past_is_rejected(client):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    resp = await client.post(URL, json={"title": "x", "due_date": past})
    assert resp.status_code == 422
    assert resp.json()["code"] == "business_rule_violation"


async def test_pagination_and_filter(client):
    for i in range(25):
        await client.post(URL, json={"title": f"task-{i}"})

    resp = await client.get(URL, params={"page": 2, "size": 10})
    data = resp.json()
    assert data["total"] == 25
    assert data["pages"] == 3
    assert len(data["items"]) == 10

    resp = await client.get(URL, params={"keyword": "task-1"})
    assert resp.json()["total"] == 11  # task-1, task-10 ~ task-19


async def test_cannot_access_others_task(client, other_user):
    task_id = (await client.post(URL, json={"title": "私有"})).json()["id"]

    # 切换为另一个用户
    app.dependency_overrides[get_current_user] = lambda: other_user
    resp = await client.get(f"{URL}/{task_id}")
    assert resp.status_code == 404  # 不是 403，避免泄露资源存在性


async def test_soft_delete(client):
    task_id = (await client.post(URL, json={"title": "待删除"})).json()["id"]

    assert (await client.delete(f"{URL}/{task_id}")).status_code == 204
    assert (await client.get(f"{URL}/{task_id}")).status_code == 404
    assert (await client.get(URL)).json()["total"] == 0


async def test_patch_rejects_null_title(client):
    task_id = (await client.post(URL, json={"title": "t"})).json()["id"]
    resp = await client.patch(f"{URL}/{task_id}", json={"title": None})
    assert resp.status_code == 422
```

```bash
pytest -v
```

---

## 4. 请求流转回顾（以 `PATCH /tasks/5` 为例）

```
PATCH /api/v1/tasks/5  {"status": "done"}
  │
  ├─ API：Pydantic 校验 TaskUpdate；依赖注入 get_current_user、TaskService
  ├─ Service.update_task：
  │     ├─ _get_owned_task  → Repository.get(5)（自动排除软删除）→ 校验归属
  │     ├─ model_dump(exclude_unset=True) → {"status": "done"}
  │     ├─ Repository.update → setattr + flush + refresh
  │     └─ session.commit()   ← 事务在这里提交
  └─ API：response_model=TaskRead 序列化返回
       （任何一步抛 AppException → 全局处理器 → {"code","message"} + 对应状态码）
```

---

## 5. 生产级要点与常见坑

| 坑                                    | 正确做法                                                  |
| ------------------------------------- | --------------------------------------------------------- |
| Service 里 `raise HTTPException`      | 抛领域异常，让全局处理器转换；Service 才能被 CLI/队列复用 |
| Repository 里 `commit()`              | 只 `flush`；否则一个用例里多次 commit 无法回滚            |
| 越权返回 403                          | 对"别人的资源"返回 404，防 ID 枚举                        |
| `PATCH` 用 `model_dump()`             | 必须 `exclude_unset=True`，否则会把没传的字段覆盖成默认值 |
| 排序字段由客户端直接传列名            | `Literal` 白名单 + 映射字典                               |
| 分页无稳定排序                        | 追加唯一列（`id`）作为次级排序                            |
| commit 后访问属性报 `MissingGreenlet` | `expire_on_commit=False` + 服务端生成字段用 `refresh`     |
| 软删除后忘记过滤                      | 统一走 `_select()`，不要在各处手写 `select(Model)`        |
| 大表 `OFFSET` 很深时变慢              | 数据量上百万后改用**游标分页**（keyset pagination）       |
| 模型没被 Alembic 发现                 | `models/__init__.py` 导入所有模型                         |

---

## 6. 练习

1. **状态机**：在 Service 中限制状态流转 `todo → in_progress → done`，禁止 `done` 回退，违反抛 `BusinessError`。
2. **RBAC**：给 `User` 加 `is_superuser`，超级管理员可以访问所有任务（修改 `_get_owned_task`）。
3. **标题唯一**：同一用户未删除任务标题不能重复，抛 `ConflictError`；再用 PostgreSQL 部分唯一索引 `WHERE deleted_at IS NULL` 兜底并发场景。
4. **子资源**：新增 `Comment` 模块（Task 1:N Comment），体验"父资源权限继承"。
5. **游标分页**：为列表接口增加 `cursor` 参数版本，对比 offset 分页的差异。
6. **Unit of Work**：把 `session.commit()` 从 Service 抽到 `UnitOfWork` 类，Service 通过 `async with uow:` 使用（这是更进阶的主流写法）。

---