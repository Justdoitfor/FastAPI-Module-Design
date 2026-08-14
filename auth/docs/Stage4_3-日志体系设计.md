# 日志体系设计
## Python logging体系
```python
logging
```
等级划分
```text
DEBUG       调试信息
INFO        正常运行
WARNING     警告
ERROR       错误
CRITICAL    严重错误
```
## 日志目录设计j
新增如下文件
```text
app
├── core
│   ├── logger.py     新增
├── logs             新增
│   └── app.log

```
### 创建日志配置
auth/core/logger.py
```python
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path('logs')
LOG_DIR.mkdir(exist_ok=True)


def setup_logger():
    logger = logging.getLogger("fastapi-auth")

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s "
        "[%(levelname)s] "
        "%(name)s "
        "%(filename)s:%(lineno)d "
        "- %(message)s"
    )
    file_handler = RotatingFileHandler(
        filename=LOG_DIR / "auth.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    # 控制台输出
    console__handler = logging.StreamHandler()

    console__handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console__handler)
    return logger


logger = setup_logger()

```

### 加载日志
auth/main.py
增加
```python
from auth.core.logger import logger

logger.info("Application started")
```
控制台输出
```terminaloutput
2026-07-03 18:10:17,327 [INFO] fastapi-auth main.py:16 - Application started
```

### Service 中使用日志
auth/services/auth_service.py
增加
```python
from auth.core.logger import logger

# 检查用户名是否存在
exist_user = await self.user_repository.get_by_username(data.username)
if exist_user:
    logger.warning(f'User {data.username} already exists')
    raise UsernameAlreadyExists()
# 检查邮箱是否存在
exist_user = await self.user_repository.get_by_email(data.email)
if exist_user:
    logger.warning(f'Email {data.email} already exists')
    raise EmailAlreadyExists()
```

### 异常自动记录 traceback
auth/core/exception_handler.py
```python
async def database_exception_handler(request: Request, exc: IntegrityError):
    logger.error(
        f"Database error occurred: {request.url}",
        exc_info=True
    )
    return JSONResponse(
        status_code=500,
        content=error_response(ErrorCode.DATABASE_ERROR).model_dump()
    )


async def global_exception_handler(request: Request, exc: Exception):
    logger.error(
        f"Unhandled exception:"
        f"{request.url}",
        exc_info=True  # 自动记录（Traceback、文件、行号、错误原因）
    )
    return JSONResponse(
        status_code=500,
        content=error_response(ErrorCode.INTERNAL_ERROR).model_dump()
    )
```

### 当前日志体系
```text
请求
 ↓
Router
 ↓
Service
 ↓
logger.info()
 ↓
Exception
 ↓
logger.error()
 ↓
Response
```