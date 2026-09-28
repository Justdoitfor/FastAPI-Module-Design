# FastAPI 生产级模块实战 ②：业务资源模块

> 前置：①已建立 `app/core`、`app/db`、`app/domains/auth`。
> 本篇新增 `app/domains/tasks/`，包括：CRUD、资源级权限、分页/过滤/排序、软删除、统一异常、测试
---

## 0. 整体结构

```
app/
├── core/                    ← ①已建立
├── db/
│   ├── base.py              ← ①已建立 Base/TimestampMixin，新增 SoftDeleteMixin
│   └── repository.py        ← ①已建立唯一的 BaseRepository，扩展（软删除过滤、分页）
├── domains/
│   ├── auth/                ← ①已完成
│   └── tasks/                ← 业务代码
│       ├── models.py
│       ├── schemas.py
│       ├── repository.py
│       ├── service.py
│       └── router.py
├── schemas/
│   └── common.py             ← 新增：Page/PageParams 是跨域通用的技术型 Schema，不下沉进 tasks
├── api/v1/router.py          ← 追加一行 include_router
└── models/__init__.py        ← 追加一行 import
```

**改动范围**：新建 `domains/tasks/` 五件套 + `schemas/common.py`；对 `db/repository.py` 做**向后兼容的增强**（老方法签名不变，新增可选参数）；`domains/auth/` 目录下**零改动**。

---

## 1. 分层职责回顾

```
API 层        解析请求 / 依赖注入 / 调 Service / 组装响应   只懂 HTTP
Service 层    业务规则 / 权限判断 / 事务边界(commit)        只懂业务，不懂 HTTP
Repository    构造查询 / 读写数据库 / flush                只懂 SQL，不懂业务
Database
```
---

## 2. 项目级基础设施的增量修改

### 2.1 `db/base.py`：新增 `SoftDeleteMixin`

```python
# app/db/base.py（在①已有内容基础上追加）
class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True
    )
```

> `TimestampMixin` 是①建立的，`SoftDeleteMixin` 不是每个域都需要（比如 `auth` 域的 `User`/`RefreshToken` 就没用软删除），所以放在项目级 `db/base.py` 而不是 `tasks` 域内——**判断标准依然是"两个以上域会用到才提升到项目级"，软删除是通用数据库模式，任何域都可能用，理应留在 `db/`**。

### 2.2 `db/repository.py`：向后兼容地增强

①建立的 `BaseRepository` 只有最基础的 `get`/`create`/`update`。②需要软删除过滤和分页，**用新增可选参数的方式扩展，不改变已有方法签名**，保证 `domains/auth/repository.py` 不需要任何改动：

```python
# app/db/repository.py（在①基础上修改 _select，新增 paginate/soft_delete/hard_delete）
from typing import Generic, TypeVar
from sqlalchemy import Select, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone

from app.db.base import Base

ModelT = TypeVar('ModelT', bound=Base)


class BaseRepository(Generic[ModelT]):
    """通用仓储：封装基础CRUD。只进行flush，不commit——事务边界由Service层进行决策"""
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _select(self, *, include_deleted: bool = False) -> Select:
        # 增加自动过滤软删除记录（如果model有deleted_at字段），auth域中User/RefreshToken没有deleted_at,hasattr判断为False，
        # auth中行为一致，不会影响auth域中model
        stmt = select(self.model)
        if not include_deleted and hasattr(self.model, 'deleted_at'):
            stmt = stmt.where(self.model.deleted_at.is_(None))
        return stmt

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

    async def soft_delete(self, obj: ModelT) -> None:
        obj.deleted_at = datetime.now(timezone.utc)
        await self.session.flush()

    async def hard_delete(self, obj: ModelT) -> None:
        await self.session.delete(obj)
        await self.session.flush()

    async def paginate(self, stmt: Select, *, offset: int, limit: int) -> tuple[list[ModelT], int]:
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = (await self.session.execute(count_stmt)).scalar_one()
        rows = await self.session.scalars(stmt.offset(offset).limit(limit))
        return list(rows), total

```

> 这是"项目级基础设施应该如何演进"的一个典型例子：**新域需要新能力时，优先考虑能否以"新增方法/新增可选参数"的方式向后兼容地扩展现有基础设施，而不是让每个域各写一份**。如果扩展会破坏已有域的行为，才考虑给新域单独定义子类。本篇的改法完全不影响 `domains/auth/repository.py`——可以运行①的测试套件验证这一点，应全部通过、零改动。

### 2.3 `schemas/common.py`：跨域通用的分页类型

```python
# app/schemas/common.py
import math
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageParams(BaseModel):
    page: int = Field(1, ge=1)
    size: int = Field(20, ge=1, le=100)

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
            items=items, total=total, page=params.page, size=params.size,
            pages=math.ceil(total / params.size) if total else 0,
        )
```

> **为什么 `Page`/`PageParams` 放在 `app/schemas/common.py`，而不是 `domains/tasks/schemas.py`？** 这正是结构文档 4 节末尾强调的判断标准：`Page[T]` 不代表任何业务概念（它不是"任务"或"用户"），是纯技术性的通用响应结构，未来 `attachments`、`exports` 等域的列表接口都会复用它。如果把它定义在 `domains/tasks/schemas.py` 里，`attachments` 域要用分页时就得反向 `import domains.tasks`，直接违反"域间单向依赖"的规则。凡是"纯技术、无业务语义、多域共享"的 Schema，一律留在顶层 `app/schemas/`，不下沉进任何域——这与 `BaseRepository` 留在 `db/` 是同一个判断逻辑。

---

## 3. `domains/tasks/` 内部实现

```
app/domains/tasks/
├── __init__.py
├── models.py
├── schemas.py
├── repository.py
├── service.py
└── router.py
```

域内没有 `exceptions.py`——因为②的业务规则只需要①已建立的 `NotFoundError`、`BusinessError`（项目级通用异常），没有任何 `tasks` 专属的异常类型。**不是每个域都必须有 `exceptions.py`**，文件是否存在取决于这个域是否真的有专属异常，不要为了"结构整齐"而创建空文件。

### Step 1：Model

```python
# app/domains/tasks/models.py
import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SoftDeleteMixin, TimestampMixin


class TaskStatus(str, enum.Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class TaskPriority(enum.IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class Task(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_owner_status", "owner_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e]),
        default=TaskStatus.TODO,
    )
    priority: Mapped[int] = mapped_column(SmallInteger, default=TaskPriority.MEDIUM)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    # 跨域外键：直接引用 auth 域的表名 "users"。
    # 数据库层面的外键约束天然允许跨域，这不违反"域间单向依赖"规则——
    # 规则约束的是 Python 代码的 import 方向，tasks 从不 import domains.auth.models.User，
    # 只是在字符串层面引用表名，这是数据库设计的正常做法。
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
```

```python
# app/models/__init__.py（追加一行）
from app.domains.auth.models import RefreshToken, User  # noqa: F401
from app.domains.tasks.models import Task  # noqa: F401
```

```bash
alembic revision --autogenerate -m "add tasks table"
alembic upgrade head
```

### Step 2：Schemas

```python
# app/domains/tasks/schemas.py
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.domains.tasks.models import TaskPriority, TaskStatus
from app.schemas.common import PageParams


class TaskBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    priority: TaskPriority = TaskPriority.MEDIUM
    due_date: AwareDatetime | None = None


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_date: AwareDatetime | None = None

    @field_validator("title", "status", "priority")
    @classmethod
    def not_null_when_provided(cls, v):
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
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    keyword: str | None = Field(default=None, max_length=100)
    sort_by: Literal["created_at", "due_date", "priority"] = "created_at"
    order: Literal["asc", "desc"] = "desc"
```

> `from app.schemas.common import PageParams`——这是"域依赖项目级通用 Schema"的例子，方向正常（项目级 → 域），不是域间横向依赖。

### Step 3：Repository

```python
# app/domains/tasks/repository.py
from app.db.repository import BaseRepository
from app.domains.tasks.models import Task
from app.domains.tasks.schemas import TaskQuery

_SORT_COLUMNS = {
    "created_at": Task.created_at,
    "due_date": Task.due_date,
    "priority": Task.priority,
}


class TaskRepository(BaseRepository[Task]):
    model = Task

    async def get_owned(self, task_id: int, owner_id: int) -> Task | None:
        stmt = self._select().where(Task.id == task_id, Task.owner_id == owner_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_owner(self, owner_id: int, query: TaskQuery) -> tuple[list[Task], int]:
        stmt = self._select().where(Task.owner_id == owner_id)

        if query.status:
            stmt = stmt.where(Task.status == query.status)
        if query.priority:
            stmt = stmt.where(Task.priority == query.priority)
        if query.keyword:
            stmt = stmt.where(Task.title.icontains(query.keyword, autoescape=True))

        column = _SORT_COLUMNS[query.sort_by]
        order = column.desc() if query.order == "desc" else column.asc()
        stmt = stmt.order_by(order, Task.id.desc())

        return await self.paginate(stmt, offset=query.offset, limit=query.size)
```

> `TaskRepository(BaseRepository[Task])`——继承的是①在 `app/db/repository.py` 里建立的**同一个类**。`_select()`、`paginate()` 都是白拿的，`tasks` 域完全不需要重新实现分页/软删除过滤逻辑。这就是"泛型基础设施只写一次、所有域复用"的直接收益。

### Step 4：Service

```python
# app/domains/tasks/service.py
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessError, NotFoundError
from app.domains.auth.models import User
from app.domains.tasks.models import Task
from app.domains.tasks.repository import TaskRepository
from app.domains.tasks.schemas import TaskCreate, TaskQuery, TaskUpdate


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tasks = TaskRepository(session)

    async def list_tasks(self, user: User, query: TaskQuery) -> tuple[list[Task], int]:
        return await self.tasks.list_by_owner(user.id, query)

    async def get_task(self, user: User, task_id: int) -> Task:
        return await self._get_owned_task(user, task_id)

    async def create_task(self, user: User, data: TaskCreate) -> Task:
        self._validate_due_date(data.due_date)
        task = Task(**data.model_dump(), owner_id=user.id)
        await self.tasks.create(task)
        await self.session.commit()
        return task

    async def update_task(self, user: User, task_id: int, data: TaskUpdate) -> Task:
        task = await self._get_owned_task(user, task_id)
        values = data.model_dump(exclude_unset=True)
        if "due_date" in values:
            self._validate_due_date(values["due_date"])
        await self.tasks.update(task, values)
        await self.session.commit()
        return task

    async def delete_task(self, user: User, task_id: int) -> None:
        task = await self._get_owned_task(user, task_id)
        await self.tasks.soft_delete(task)
        await self.session.commit()

    async def _get_owned_task(self, user: User, task_id: int) -> Task:
        # 用 Step 3 新增的 get_owned，一次查询完成"存在且属于我"，
        # 比①原版"先 get 再比对 owner_id"少一次判断分支
        task = await self.tasks.get_owned(task_id, user.id)
        if task is None:
            raise NotFoundError("任务不存在")
        return task

    @staticmethod
    def _validate_due_date(due_date: datetime | None) -> None:
        if due_date is not None and due_date <= datetime.now(timezone.utc):
            raise BusinessError("截止时间必须晚于当前时间")
```

> `from app.domains.auth.models import User`——**这是本篇唯一一处跨域 import**，方向是 `tasks → auth`，符合"认证是最底层的身份域，其他域可以依赖它"的规则（结构文档 3.4 节）。`TaskService` 只用到 `User` 这个类型标注（"谁在操作"），完全不接触 `auth` 域的密码哈希、Token 逻辑——这正是好的域边界该有的样子：**依赖对方暴露的数据类型，而不是对方的实现细节**。

### Step 5：Router

```python
# app/domains/tasks/router.py
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbDep
from app.domains.auth.deps import ActiveUser
from app.domains.tasks.schemas import TaskCreate, TaskQuery, TaskRead, TaskUpdate
from app.domains.tasks.service import TaskService
from app.schemas.common import Page

router = APIRouter(prefix="/tasks", tags=["tasks"])


def get_task_service(session: DbDep) -> TaskService:
    return TaskService(session)


ServiceDep = Annotated[TaskService, Depends(get_task_service)]


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(data: TaskCreate, user: ActiveUser, service: ServiceDep):
    return await service.create_task(user, data)


@router.get("", response_model=Page[TaskRead])
async def list_tasks(
    query: Annotated[TaskQuery, Query()], user: ActiveUser, service: ServiceDep
):
    tasks, total = await service.list_tasks(user, query)
    return Page[TaskRead].build(items=tasks, total=total, params=query)


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

> `from app.domains.auth.deps import ActiveUser`——①在 `domains/auth/deps.py` 里暴露的公共接口，在这里被直接复用。`tasks` 域完全不知道"如何验证一个用户已登录"这件事是怎么做到的，只知道"拿到一个 `ActiveUser` 类型的参数，注入进来的就是当前登录用户"。这是依赖注入 + 域边界配合的典型效果。

---

## 4. 路由与模型汇总（本篇唯一需要碰 `domains/tasks/` 之外文件的地方）

```python
# app/api/v1/router.py（在①基础上追加两行）
from fastapi import APIRouter

from app.domains.auth.router import router as auth_router
from app.domains.tasks.router import router as tasks_router   # 新增

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(tasks_router)   # 新增
```

`app/models/__init__.py` 的改动已在 Step 1 展示（追加一行 import）。

**除了这两处必要的汇总点，`domains/auth/` 目录下的任何文件都没有被修改。** 
---

## 5. 测试

```
tests/
├── conftest.py
└── domains/
    ├── auth/
    │   └── test_auth.py         ← ①已有
    └── tasks/
        ├── conftest.py           ← 新增
        └── test_tasks.py         ← 新增
```

`conftest.py` 需要补充两个 fixture：一个测试用户、一个已登录的 client（复用①已经验证过的注册/登录流程）：

```python
# tests/conftest.py（在①版本基础上追加）
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # 保证所有 Model 在 create_all() 之前被加载
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
async def user(session_factory) -> User:
    return await _make_user(session_factory, "owner@example.com")


@pytest_asyncio.fixture
async def other_user(session_factory) -> User:
    return await _make_user(session_factory, "other@example.com")


@pytest_asyncio.fixture
async def client(session_factory):  # 不再依赖 user，也不再覆盖 get_current_user
    from httpx import ASGITransport, AsyncClient

    from app.api.deps import get_db
    from app.main import app

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()

```
```python
# tests/domains/tasks/conftest.py
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_db
from app.domains.auth.deps import get_current_user
from app.main import app


@pytest_asyncio.fixture
async def client(session_factory, user):
    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user  # 只对 tasks 目录生效, 跳过user校验，只测试tasks中的业务逻辑
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()

```

```python
# tests/domains/tasks/test_tasks.py
from datetime import datetime, timedelta, timezone

import pytest

URL = "/api/v1/tasks"


async def test_create_and_get_task(client):
    resp = await client.post(URL, json={"title": "写教程", "priority": 3})
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "写教程" and body["status"] == "todo"

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
    assert data["total"] == 25 and data["pages"] == 3 and len(data["items"]) == 10

    resp = await client.get(URL, params={"keyword": "task-1"})
    assert resp.json()["total"] == 11


async def test_cannot_access_others_task(client, other_user):
    from app.domains.auth.deps import get_current_user
    from app.main import app

    task_id = (await client.post(URL, json={"title": "私有"})).json()["id"]

    app.dependency_overrides[get_current_user] = lambda: other_user
    resp = await client.get(f"{URL}/{task_id}")
    assert resp.status_code == 404


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
# 分别验证：①的域完全不受影响，②的域按预期工作
pytest tests/domains/auth/ -v
pytest tests/domains/tasks/ -v
```
