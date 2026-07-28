# 1.创建项目结构
```text
app
├── main.py
├── api
│   ├── __init__.py
│   ├── auth.py
│   └── user.py
├── core
│   ├── config.py
│   └── security.py
├── database
│   ├── session.py
│   └── base.py
├── models
│   └── user.py
├── schemas
│   └── user.py
├── repositories
│   └── user_repository.py
├── services
│   └── auth_service.py
├── dependencies
│   └── auth.py
└── utils
```

# 流程
```text
HTTP请求
 ↓
Router
 ↓
Service
 ↓
Repository
 ↓
Database
```

# 数据库访问
```text
FastAPI
   |
Dependency
   |
AsyncSession
   |
SQLAlchemy ORM
   |
PostgreSQL
```

# 启动PostgresSQL
- 一般企业开发通过docker
docker-compose.yml
```yaml
version: "3.8"

services:
  postgres:
    image: postgres:16
    container_name: auth_postgres
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: auth_system
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```
启动命令： docker compose up -d
查看状态：docker ps

# 通过.env配置环境变量，避免硬编码或者直接在代码中泄露敏感（配置密钥等）信息

# 通过config.py 配置项目参数
config.py
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str
    ALEMBIC_DATABASE_URL: str

    class Config:
        env_file = ".env" # 从.env 中获取配置信息具体字段

settings = Settings()

```
通过上面的配置文件，项目中任何地方都可以直接通过下面方式获取配置信息
```python
from auth.core.config import settings

print(settings.DATABASE_URL)
```

# 创建数据库连接
session.py
```python
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession
)

from auth.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=True
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
```

# 创建Base模型
base.py 后续数据表模型直接继承Base
```python
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass
```

# 创建User模型
```python
from sqlalchemy import (
    String,
    Boolean,
    DateTime
)

from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime

from auth.database.base import Base


class User(Base):
    __tablename__="users"

    id:Mapped[int]=mapped_column(
        primary_key=True,
        index=True
    )

    username:Mapped[str]=mapped_column(
        String(50),
        unique=True,
        nullable=False
    )

    email:Mapped[str]=mapped_column(
        String(100),
        unique=True,
        index=True,
        nullable=False
    )

    password_hash:Mapped[str]=mapped_column(
        String(255),
        nullable=False
    )

    avatar:Mapped[str|None]=mapped_column(
        String(255),
        nullable=True
    )

    role:Mapped[str]=mapped_column(
        String(20),
        default="user"
    )
    
    is_active:Mapped[bool]=mapped_column(
        Boolean,
        default=True
    )
    
    created_at:Mapped[datetime]=mapped_column(
        DateTime,
        default=datetime.utcnow
    )
    
    updated_at:Mapped[datetime]=mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )
```
SQLAlchemy会自动进行转换
```text
Python对象
↓
SQL
↓
PostgreSQL
```

# 注册模型
auth/models/__init__.py
```python
from .user import User
```

# 测试数据库连接
main.py
```python
from auth.database.session import engine

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        print("database connected")
```

# 启动服务
```bash
uvicorn app.main:app --reload
```
启动日志中会出现 "database connected"

# 引入Alembic
避免每次修改模型都需要删除数据库重新创建
```text
Model变化
↓
migration文件
↓
升级数据库
```
初始化：
```bash
alembic init migrations
```
# 配置Alembic
修改默认配置文件，数据库连接配置通过之前配置的settings中的来自.env文件的配置

修改migrations/env.py, 增加如下修改
```python
from auth.core.config import settings
from auth.database.base import Base
from auth.models import *

target_metadata = Base.metadata
# 使用settings中的连接配置信息
config.set_main_option("sqlalchemy.url", settings.ALEMBIC_DATABASE_URL)
```

生成迁移：
```bash
alembic revision --autogenerate -m "create user table"
```
执行：
```bash
alembic upgrade head
```
说明：
- `upgrade`：执行**升级迁移（创建 / 更新数据表）**
- `head`：代表**最新的迁移版本**（migrations 版本链最顶端）



