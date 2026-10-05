# FastAPI 生产级模块实战 ⑥：日志与可观测性

> 前置：①`domains/auth`、②`domains/tasks`、③`app/cache`、④`app/queue`+`domains/reminders`+`domains/exports`、⑤`app/storage`+`domains/attachments` 均已就绪。
> 本篇**不新增业务域**——日志、指标、链路追踪是贯穿所有域的横切能力，全部收在项目级的 `core/`、`api/`、`worker/` 里，然后"接线"进每个已有域的入口（Router、任务函数）。
> 本篇的目标：**出了问题，3 分钟内定位到"哪个请求、哪个用户、慢在哪一步、失败在哪个组件"。**
---

## 0. 本篇在整体结构里的位置

```
app/
├── core/
│   ├── config.py               ← 新增可观测性相关配置
│   ├── logging.py               ← 新增：structlog 配置
│   ├── metrics.py               ← 新增：Prometheus 指标定义
│   ├── tracing.py               ← 新增：OpenTelemetry 配置
│   └── exception_handlers.py    ← 修改：记录日志、带 request_id
├── api/
│   ├── middleware.py             ← 新增：请求上下文 + 访问日志 + 指标
│   ├── health.py                 ← 新增：/livez /readyz
│   └── deps.py                   ← get_current_user 处绑定 user_id（在 domains/auth/deps.py 修改）
├── worker/
│   └── observability.py          ← 补全实现（此前是④篇的占位版）
├── domains/                      ← 本篇不新增域；只对已有域做"接线"式的小修改
│   ├── auth/deps.py               ← get_current_user 里绑定 user_id 到日志上下文
│   ├── tasks/service.py           ← 缓存调用处传入 name 参数（供指标区分）
│   └── attachments/service.py     ← 上传结果记指标
└── main.py                       ← 装配：日志、中间件、追踪、指标端口
```

**判断依据**：日志格式、指标定义、链路追踪配置是"每一个域都要用、且必须全项目统一"的能力，不属于任何单一业务，因此整体留在 `core/`（配置与定义）、`api/`（HTTP 层的横切装配）、`worker/`（Worker 进程的横切装配）。**接入到具体域时改动尽量小**——多数域只需要在已有代码里插入一两行（比如给缓存调用加一个 `name` 参数），不需要新建文件。


---

## 1. 技术栈与核心设计

```bash
uv add structlog prometheus-client opentelemetry-sdk opentelemetry-exporter-otlp opentelemetry-instrumentation-fastapi opentelemetry-instrumentation-sqlalchemy opentelemetry-instrumentation-redis opentelemetry-instrumentation-botocore

```

### 本篇将掌握

1. **可观测性三大支柱**（日志 / 指标 / 链路）各自回答什么问题，怎么配合排障
2. **结构化日志**（structlog）：JSON 输出、上下文自动携带、统一接管 uvicorn / SQLAlchemy / arq 的日志
3. **Request ID**：纯 ASGI 中间件 + `contextvars`，一条请求的所有日志自动带同一个 ID
4. **访问日志 + 异常日志**：5xx 不泄露内部细节，但带 `request_id` 便于用户报障
5. **Prometheus 指标**：RED 方法、直方图、**标签基数**陷阱，覆盖 ③④⑤ 的业务指标
6. **OpenTelemetry 分布式追踪**：自动埋点 FastAPI / SQLAlchemy / Redis / S3，日志中带 `trace_id`
7. **跨进程传播**：API → 队列 → Worker，`request_id` 与 trace 一路贯穿
8. **健康检查**：liveness / readiness 的区别，以及"可降级依赖不该让实例下线"
9. **告警规则**与一次完整的**排障演练**
10. 用 `capture_logs` / Prometheus registry 测试可观测性本身

### 三大支柱分工

| 支柱             | 回答的问题                                 | 特点                         | 本篇工具                       |
| ---------------- | ------------------------------------------ | ---------------------------- | ------------------------------ |
| **日志 Logs**    | "**这一次**请求发生了什么？"               | 细节最全、成本最高、按需检索 | structlog → JSON → Loki / ELK  |
| **指标 Metrics** | "**整体**健康吗？趋势如何？"               | 聚合数值、成本低、适合告警   | Prometheus + Grafana           |
| **链路 Traces**  | "这次请求的**时间花在哪**？跨了哪些服务？" | 调用树 + 耗时瀑布图          | OpenTelemetry + Jaeger / Tempo |

**排障的典型顺序**：指标发现异常（告警）→ 链路定位慢在哪一步 → 日志看那一次请求的细节。三者靠 **`trace_id` / `request_id`** 串联。

### 结构化日志原则

```python
# ❌ 字符串拼接：只能靠正则捞，无法按字段聚合
logger.info(f"user {user_id} created task {task_id} in {ms}ms")

# ✅ 事件名 + 键值对：可查询、可聚合、可告警
log.info("task_created", user_id=user_id, task_id=task_id, duration_ms=ms)
```

| 原则         | 做法                                                         |
| ------------ | ------------------------------------------------------------ |
| 事件名稳定   | `snake_case` 的固定标识（`cache_read_failed`），不含变量     |
| 数据放字段   | 变量一律作为 kwargs，不拼进事件名                            |
| 上下文自动带 | `request_id` / `user_id` 通过 `contextvars` 绑定一次，之后**每条日志自动携带** |
| 级别有语义   | 见下表                                                       |
| 不记敏感信息 | 密码、Token、完整预签名 URL、邮箱等 PII 不进日志             |

| 级别    | 用于                        | 例                         |
| ------- | --------------------------- | -------------------------- |
| DEBUG   | 开发排查细节，生产关闭      | 缓存 key、SQL 参数         |
| INFO    | 正常业务里程碑              | 请求完成、任务开始/结束    |
| WARNING | 异常但已自愈/降级，值得关注 | 缓存读失败已回源、任务重试 |
| ERROR   | 需要人介入的失败            | 未处理异常、任务最终失败   |

> **4xx 不是 ERROR**：客户端传错参数、访问不存在的资源是正常业务现象，记 INFO。ERROR 只留给"我们自己的问题"，这样 ERROR 数量才能做告警依据。

### 1.3 指标设计：RED 方法与基数

对每个服务关注三件事（**RED**）：

- **R**ate：每秒请求数
- **E**rrors：错误率
- **D**uration：耗时分布（用**直方图**，才能算 p95 / p99）

> **⚠️ 标签基数（cardinality）是指标系统的头号杀手。**
> 每个不同的标签值组合都是一条独立时间序列。
> **绝不能**把 `user_id`、原始 URL 路径（`/tasks/123`）、`job_id`、`request_id` 当标签——序列数会无限增长，把 Prometheus 拖垮。
> 用**路由模板**（`/tasks/{task_id}`）、**任务函数名**、**有限枚举**当标签；高基数信息放日志和链路里。

### 1.4 关联：如何把三者串起来

```
        ┌─ 每条日志：request_id、trace_id、span_id、user_id、job_id ...
一次请求 ─┼─ 每个 Span：同一个 trace_id（跨 API → Worker 传播）
        └─ 响应头：X-Request-ID（用户报障时提供）
```
---

## 2. `core/`：日志、指标、追踪的项目级定义

### 2.1 配置

```python
# app/core/config.py（追加）
from typing import Literal

class Settings(BaseSettings):
    ...
    ENV: Literal["local", "test", "staging", "production"] = "local"
    SERVICE_NAME: str = "myapi"
    RELEASE: str = "dev"

    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True
    SLOW_REQUEST_SECONDS: float = 1.0

    METRICS_PORT: int | None = 9100
    WORKER_METRICS_PORT: int | None = 9101

    OTEL_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:4317"
    OTEL_SAMPLE_RATIO: float = 1.0
```

### 2.2 结构化日志
**目标：所有日志——包括 uvicorn、SQLAlchemy、arq、第三方库、以及 ③④⑤ 里用标准库 `logging` 写的日志——统一输出成同一种 JSON。**

做法：structlog 的 `ProcessorFormatter` 接管标准库 `logging` 的输出。
```python
# app/core/logging.py
import logging
import sys

import structlog
from opentelemetry import trace

_SENSITIVE_KEYS = {
    "password", "passwd", "token", "access_token", "refresh_token",
    "authorization", "secret", "api_key", "cookie", "set-cookie",
}

"""
structlog 规定，每一个处理器函数都必须接受固定的三个位置参数,不是可以按需省略的参数：
def some_processor(logger, method_name, event_dict):
    ...
    return event_dict   # 或者是修改后的 event_dict
"""

def redact_sensitive(logger, method_name, event_dict):
    """兜底脱敏：字段名命中敏感词则打码。只处理顶层字段；根本办法是不要把这些东西传给日志。"""
    for key in list(event_dict):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "***"
    return event_dict


def add_trace_ids(logger, method_name, event_dict):
    """把当前 Span 的 trace_id / span_id 写进日志，日志与链路一键互跳。"""
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def _static_fields(service: str, env: str, release: str):
    def processor(logger, method_name, event_dict):
        event_dict.setdefault("service", service)
        event_dict.setdefault("env", env)
        event_dict.setdefault("release", release)
        return event_dict
    return processor


def configure_logging(*, level: str, json_logs: bool, service: str, env: str, release: str = "dev") -> None:
    shared = [
        structlog.contextvars.merge_contextvars, # 合并 request_id / user_id / job_id 等上下文
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        add_trace_ids,
        _static_fields(service, env, release),
        redact_sensitive,
    ]

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=(env != "test"), # 测试环境关闭缓存，capture_logs 才能生效
    )

    if json_logs:
        final = [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info, # 异常堆栈写成字符串字段
            structlog.processors.JSONRenderer(),
        ]
    else:
        final = [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(), # 本地：彩色、易读
        ]

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,  # 标准库 logging 产生的日志（第三方库、老代码）也走同一条链
        processors=final)
    handler = logging.StreamHandler(sys.stdout) # 容器日志写 stdout，由平台采集
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # 接管 uvicorn：错误日志并入 root；access log 由我们自己的中间件负责
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("arq.worker").setLevel(logging.WARNING)
```
**JSON 输出样例：**

```json
{"event":"task_created","task_id":42,"request_id":"9f3c...","user_id":7,
 "trace_id":"4bf9...","span_id":"00f0...","level":"info","logger":"app.services.task",
 "timestamp":"2026-09-21T08:15:30.123456Z","service":"myapi-api","env":"production","release":"a1b2c3d"}
```

> 注意 `request_id` / `user_id` 没有在 `task_created` 里写，它们是 `contextvars` **自动合并**进来的——这就是"绑定一次，全程携带"。

**为什么用纯 ASGI 中间件，而不是 `@app.middleware("http")`（`BaseHTTPMiddleware`）？**

- `BaseHTTPMiddleware` 会把下游放到**另一个任务**里执行，`contextvars` 的绑定/传递容易出问题，且有额外性能开销；
- 纯 ASGI 中间件与下游**同一个任务**，上下文变量天然一致（endpoint 里绑定的 `user_id` 在中间件收尾时也能读到）。

### 2.3 Prometheus 指标定义

```python
# app/core/metrics.py
from prometheus_client import Counter, Gauge, Histogram

# ---------- HTTP（RED） ----------
HTTP_REQUESTS = Counter("http_requests_total", "HTTP 请求总数", ["method", "route", "status"])
HTTP_DURATION = Histogram(
    "http_request_duration_seconds", "HTTP 请求耗时", ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
HTTP_IN_FLIGHT = Gauge("http_requests_in_flight", "正在处理的请求数")

# ---------- 缓存与限流 ----------
CACHE_REQUESTS = Counter("cache_requests_total", "缓存查询次数", ["name", "result"])
CACHE_ERRORS = Counter("cache_errors_total", "缓存(Redis)异常次数", ["op"])
RATE_LIMIT_DECISIONS = Counter("rate_limit_decisions_total", "限流判定", ["scope", "decision"])

# ---------- 后台任务 ----------
JOBS = Counter("jobs_total", "任务执行次数", ["job_name", "status"])
JOB_DURATION = Histogram(
    "job_duration_seconds", "任务耗时", ["job_name"],
    buckets=(0.1, 0.5, 1, 5, 15, 60, 300, 600),
)
QUEUE_DEPTH = Gauge("queue_depth", "队列中等待执行的任务数")

# ---------- 文件上传 ----------
UPLOADS = Counter("uploads_total", "上传结果", ["path", "result"])
```

> 这个文件是所有域共用的"指标注册表"。每个域不重新定义自己的指标类型，只是在自己的代码里 `import` 需要的那几个并调用
### 2.4 OpenTelemetry

```python
# app/core/tracing.py
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import settings


def setup_tracing(service_name: str) -> TracerProvider:
    resource = Resource.create({
        "service.name": service_name, # API 与 Worker 用不同的名字，追踪图里才分得清
        "service.version": settings.RELEASE,
        "deployment.environment": settings.ENV,
    })
    provider = TracerProvider(
        resource=resource,
        # ParentBased：上游已决定采样，则跟随；否则按比例采样。保证一条链路要么整条采、要么整条不采
        sampler=ParentBased(TraceIdRatioBased(settings.OTEL_SAMPLE_RATIO)),
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
                insecure=True
            )
        )
    )
    trace.set_tracer_provider(provider)
    return provider

def instrument_libraries(engine: AsyncEngine) -> None:
    """自动埋点：数据库、Redis、S3 的每次调用自动成为链路里的 Span。"""
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine) # 异步引擎要传底层 sync_engine
    RedisInstrumentor().instrument()
    BotocoreInstrumentor().instrument()

def instrument_app(app: FastAPI) -> None:
     # 探针和指标不追踪，避免污染
    FastAPIInstrumentor.instrument_app(app, excluded_urls="livez,readyz,metrics")

```

### 2.5 全局异常处理器（升级：记日志、带 request_id）
要点：

- 业务异常（`AppException`）按状态码分级记录，响应体带 `request_id`；
- **未处理异常**：记录完整堆栈（ERROR），但对客户端只返回通用信息，**绝不泄露内部细节**；
- 500 响应由最外层 `ServerErrorMiddleware` 发出，不经过我们的中间件，所以要**手动补上** `X-Request-ID`。
- 
```python
# app/core/exceptions.py（AppException）
```

```python
# app/core/exception_handlers.py
import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import AppException

log = structlog.get_logger(__name__)


def _request_id() -> str | None:
    return structlog.contextvars.get_contextvars().get("request_id")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        emit = log.error if exc.status_code >= 500 else log.info
        emit("app_exception", code=exc.code, status=exc.status_code, message=exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "request_id": _request_id()},
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception", method=request.method, path=request.url.path)
        request_id = _request_id()
        return JSONResponse(
            status_code=500,
            content={"code": "internal_error", "message": "服务器内部错误", "request_id": request_id},
            headers={"X-Request-ID": request_id} if request_id else None,
        )
```
用户报障时只需要 `request_id`，就能在日志系统里一键找到整条链路。**把 `request_id` 放进错误响应体，是成本最低、收益最高的可观测性实践之一。**

> 这个处理器只依赖 `AppException` 基类接口，**不需要知道 `domains/auth/exceptions.py`、`domains/tasks` 里用到的 `core.exceptions` 子类具体有哪些**。任何域新增自己的异常子类，这里都不需要改动——这是①篇建立 `AppException` 基类时就设计好的扩展点，在本篇的可观测性升级中直接受益。

---

## 3. `api/`：请求上下文、健康检查

### 3.1 请求上下文中间件

```python
# app/api/middleware.py
import re
import time
from uuid import uuid4

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings
from app.core.metrics import HTTP_DURATION, HTTP_IN_FLIGHT, HTTP_REQUESTS

log = structlog.get_logger("app.access")

# 只接受"看起来安全"的上游 Request ID，防止日志注入（换行、超长、特殊字符）
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9\-_]{8,64}$")
# 探针与指标抓取很频繁，记录只会制造噪音
_QUIET_PATHS = {"/livez", "/readyz", "/metrics"}


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in _QUIET_PATHS:
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid4().hex

        structlog.contextvars.clear_contextvars() # 防止复用的任务残留上一个请求的上下文
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status_code = 500 # 若下游抛异常未产生响应，则按 500 记录
        start = time.perf_counter()
        HTTP_IN_FLIGHT.inc()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - start
            HTTP_IN_FLIGHT.dec()

            route = getattr(scope.get("route"), "path", None) or "unmatched"
            method = scope["method"]
            HTTP_REQUESTS.labels(method, route, str(status_code)).inc()
            HTTP_DURATION.labels(method, route).observe(duration)

            slow = duration >= settings.SLOW_REQUEST_SECONDS
            (log.warning if slow else log.info)(
                "http_request",
                request_id=request_id,  # 显式携带：访问日志是最重要的一条，不依赖上下文变量
                method=method, 
                path=scope["path"],
                route=route, 
                status=status_code, 
                duration_ms=round(duration * 1000, 1),
                client_ip=(scope.get("client") or (None,))[0], 
                slow=slow,
            )
```

### 3.2 认证域接入：绑定 `user_id`

```python
# app/domains/auth/deps.py（get_current_user 末尾新增两行）
import structlog


async def get_current_user(
        session: DbDep,
        token: Annotated[str | None, Depends(oauth2_scheme)],
) -> User | None:
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证凭证",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id = decode_access_token(token)
    user = await UserRepository(session).get(user_id)
    if user is None:
        raise InvalidTokenError()
    structlog.contextvars.bind_contextvars(user_id=user.id)
    return user
```

> **这是本篇对 `domains/auth/` 唯一的改动**：在已经存在的依赖函数末尾插入一行绑定。`tasks`、`attachments`、`exports` 域完全不需要改动就能"顺带"获得 `user_id` 自动出现在日志里的效果——因为它们的路由都通过 `ActiveUser` 间接调用了这个函数。这正是"横切能力通过一个公共入口点接入，收益扩散到所有下游域"的例子。

### 3.3 健康检查
| 探针                  | 回答             | 失败的后果             | 检查什么               |
| --------------------- | ---------------- | ---------------------- | ---------------------- |
| **`/livez`**（存活）  | 进程还活着吗？   | **重启**容器           | **不检查任何外部依赖** |
| **`/readyz`**（就绪） | 能正常接流量吗？ | **摘除**流量（不重启） | 关键依赖               |

**关键判断——哪些依赖挂了应该摘流量？**

- **数据库**：核心依赖，挂了什么都做不了 → 就绪失败（503）。
- **Redis / 对象存储**：③ 里我们把缓存设计成"可降级"，Redis 挂了系统仍能工作。如果因为 Redis 故障就让所有实例下线，就把"降级"变成了"全站宕机"。→ 只报告 `degraded`，**仍返回 200**。

> **存活探针里检查数据库是常见反模式**：数据库故障时所有实例被反复重启，雪上加霜。
```python
# app/api/health.py
import asyncio
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbDep, RedisDep, StorageDep

log = structlog.get_logger(__name__)
router = APIRouter(tags=["health"], include_in_schema=False)

CHECK_TIMEOUT = 2.0
CRITICAL = {"database"}  # 只有这些依赖失败才会让实例"未就绪"


async def _probe(name: str, call) -> tuple[str, bool]:
    try:
        await asyncio.wait_for(call(), CHECK_TIMEOUT) # 必须有超时，否则探针本身会被拖死
        return name, True
    except Exception:
        log.warning("readiness_check_failed", dependency=name, exc_info=True)
        return name, False


@router.get("/livez")
async def livez() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(
    response: Response, db: DbDep, redis: RedisDep, storage: StorageDep,
) -> dict:
    results = dict(await asyncio.gather(
        _probe("database", lambda: db.execute(text("SELECT 1"))),
        _probe("redis", redis.ping),
        _probe("storage", storage.ping),
    ))
    if not all(results[name] for name in CRITICAL):
        response.status_code = 503
        overall = "unavailable"
    elif not all(results.values()):
        overall = "degraded"
    else:
        overall = "ok"
    # 只返回每个依赖 ok/fail，不返回错误详情（详情在日志里），避免向外暴露内部拓扑
    return {"status": overall, "checks": {k: "ok" if v else "fail" for k, v in results.items()}}
```

```python
# app/storage/base.py（追加 ping）
async def ping(self) -> None: ...              # 协议

# app/storage/s3.py
async def ping(self) -> None:                   # S3Storage
    await self._client.head_bucket(Bucket=self._bucket)
```

> `/livez`、`/readyz` 不属于任何域，直接挂在 `api/` 层——它们检查的是"整个系统"是否健康，而不是某个业务概念。

---

## 4. Worker 可观测性

```python
# app/worker/observability.py
import functools
import time
import structlog

from arq.worker import Retry
from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.trace import SpanKind

from app.core.metrics import JOB_DURATION, JOBS

logger = structlog.get_logger("app.worker")
tracer = trace.get_tracer("app.worker")


def instrumented_job(fn):
    """给任务函数统一加上：上下文绑定、日志、指标、链路（并接续 API 侧的 trace）。"""

    name = fn.__name__

    @functools.wraps(fn)
    async def wrapper(ctx: dict, *args, obs_ctx: dict | None = None, **kwargs):
        obs_ctx = obs_ctx or {}
        structlog.contextvars.clear_contextvars()
        bound = {"job_name": name, "job_id": ctx.get("job_id"), "job_try": ctx.get("job_try")}
        if obs_ctx.get("request_id"):
            bound["request_id"] = obs_ctx["request_id"]  # 与触发它的 HTTP 请求同一个 request_id
        structlog.contextvars.bind_contextvars(**bound)

        parent = extract(obs_ctx.get("trace") or {})
        status = "succeeded"
        start = time.perf_counter()

        with tracer.start_as_current_span(
                f"job {name}", context=parent, kind=SpanKind.CONSUMER,
                attributes={"job.id": ctx.get("job_id") or "", "job.try": ctx.get("job_try") or 0},
        ):
            logger.info("job_started")
            try:
                return await fn(ctx, *args, **kwargs)
            except Retry:
                status = "retry"
                logger.warning("job_retry_scheduled")
                raise
            except Exception:
                status = "failed"
                logger.exception("job_failed")
                raise
            finally:
                elapsed = time.perf_counter() - start
                JOBS.labels(name, status).inc()
                JOB_DURATION.labels(name).observe(elapsed)
                logger.info("job_finished", status=status, duration_ms=round(elapsed * 1000, 1))

    return wrapper

```

> `domains/reminders/service.py`、`domains/exports/service.py`、`domains/attachments/service.py` 里已经用 `@instrumented_job` 装饰了各自的任务函数（④⑤篇建立），**本篇只是把这个装饰器从占位版换成完整实现，三个域的任务函数代码本身一行都不用改**。这正是"横切能力独立演进，不牵动业务域代码"的效果。

### 队列注入上下文（`app/queue/client.py` 增强）

```python
# app/queue/client.py（enqueue 内新增：注入 request_id 与 trace 上下文）
import structlog
from opentelemetry.propagate import inject


class ArqJobQueue:
    ...
    async def enqueue(
        self,
        function: str,
        *args: Any,
        job_id: str | None = None,
        defer_by: timedelta | None = None,
) -> str | None:
    carrier: dict[str, str] = {}
    inject(carrier)
    obs_ctx = {
        "request_id": structlog.contextvars.get_contextvars().get("request_id"),
        "trace": carrier,
    }

    try:
        job = await self._pool.enqueue_job(
            function,
            *args,
            _job_id=job_id,
            _defer_by=defer_by,
        )
    except RedisError as e:
        logger.error(f"enqueue failed:{function}", exc_info=True)
        raise QueueUnavailableError() from e
    return job.job_id if job else None
```
上下文以一个**约定名字的关键字参数** `obs_ctx` 随任务传递（arq 保留以下划线开头的参数名，所以不加下划线）。Fake 队列不受影响，因为注入发生在 arq 适配器内部。
> `app/queue/client.py` 增强对所有调用方（`domains/reminders`、`domains/exports`）透明生效——它们调用 `queue.enqueue(...)` 的代码一行都不用改，就能获得"Worker 日志自动带上触发它的 HTTP 请求的 `request_id`"这个能力。

### 队列深度上报

```python
# app/domains/reminders/service.py（或任意一个已有域追加一个纯技术性任务函数也可，
# 这里放在 reminders 域仅因为它已经有 cron 任务的先例，实际上报的是队列的整体状态，不专属于某个域）
from app.core.metrics import QUEUE_DEPTH
from app.queue.names import QUEUE_NAME


async def report_queue_depth(ctx: dict) -> None:
    QUEUE_DEPTH.set(await ctx["redis"].zcard(QUEUE_NAME))
```

```python
# app/worker/jobs.py（追加）
from app.domains.reminders.service import report_queue_depth

ALL_JOBS = [send_due_reminder, scan_due_tasks, export_tasks, cleanup_attachments, report_queue_depth]
```

```python
# app/worker/settings.py（cron_jobs 追加，注意不装饰 @instrumented_job：高频执行，避免刷屏）
cron_jobs = [
    cron(scan_due_tasks, minute=set(range(0, 60, 5)), run_at_startup=False),
    cron(cleanup_attachments, minute=17),
    cron(report_queue_depth, second={0, 15, 30, 45}, unique=False),
]
```

---

## 5. 具体域的埋点：只需一两行改动

### `domains/tasks/service.py`：缓存指标

```python
# app/cache/cache.py（get_or_load 增加 name 参数）
from app.core.metrics import CACHE_REQUESTS

async def get_or_load(self, key, model, loader, *, ttl, null_ttl=30, name="default"):
    hit, value = await self._read(key, model)
    CACHE_REQUESTS.labels(name=name, result="hit" if hit else "miss").inc()
    if hit:
        return value
    ...
```

```python
# app/domains/tasks/service.py（调用处传入 name，其余代码不变）
page = await self.cache.get_or_load(key, Page[TaskRead], load, ttl=self.LIST_TTL, name="task_list")
task = await self.cache.get_or_load(..., ttl=self.DETAIL_TTL, name="task_detail")
```

### `domains/auth/router.py` / `domains/tasks/router.py`：限流指标

```python
# app/api/rate_limit.py（dependency 内部追加一行）
from app.core.metrics import RATE_LIMIT_DECISIONS

RATE_LIMIT_DECISIONS.labels(
    scope=scope, decision="allowed" if result.allowed else "blocked"
).inc()
```

> `auth`/`tasks` 域里已经声明的 `dependencies=[Depends(rate_limit(...))]` 一行都不用动，就能获得指标。

### `domains/attachments/service.py`：上传结果指标

```python
# app/domains/attachments/service.py（upload 方法包一层 try/except）
from app.core.exceptions import AppException
from app.core.metrics import UPLOADS


async def upload(self, user: User, task_id: int, file: IncomingFile) -> AttachmentRead:
    try:
        attachment = await self._upload(user, task_id, file)   # 原方法体挪到 _upload
    except AppException as exc:
        UPLOADS.labels(path="relay", result=exc.code).inc()
        raise
    UPLOADS.labels(path="relay", result="success").inc()
    return attachment

async def _upload(self, user: User, task_id: int, file: IncomingFile) -> AttachmentRead:
    task = await self._get_owned_task(user, task_id)
    await self._ensure_quota(task.id)
    size = self._measure(file.fileobj)
    if size == 0:
        raise BusinessError("文件为空")
    if size > MAX_UPLOAD_SIZE:
        raise PayloadTooLargeError(f"文件大小不能超过 {MAX_UPLOAD_SIZE // 1024 // 1024} MB")
    head = file.fileobj.read(SNIFF_BYTES)
    file.fileobj.seek(0)
    mime = self._sniff(head)

    key = self._make_key(user.id, task_id, mime)
    await self.storage.upload_fileobj(key, file.fileobj, content_type=mime)
    try:
        attachment = await self.attachments.create(
            Attachment(
                task_id=task_id,
                owner_id=user.id,
                object_key=key,
                filename=sanitize_filename(file.filename),
                content_type=mime,
                size=size,
                status=AttachmentStatus.READY,
            )
        )
        await self.session.commit()
    except Exception:
        await self._delete_object_quietly(key)
        raise
    return AttachmentRead.model_validate(attachment)
```

### 装配指标暴露端口
指标端点包含内部信息，**不应暴露到公网**。最简单的隔离方式：单独开一个端口，只给 Prometheus 访问。
```python
# app/main.py（lifespan 内）
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        ...
        if settings.METRIC_PORT:
            start_http_server(settings.METRIC_PORT)
    
```

---

## 6. `main.py` 完整装配

```python
# app/main.py
from contextlib import asynccontextmanager, AsyncExitStack

from fastapi import FastAPI
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.core.redis import create_redis

from arq import create_pool
from arq.connections import RedisSettings

from app.queue.names import QUEUE_NAME
from app.storage.s3 import S3Storage

import structlog
from prometheus_client import start_http_server
from app.api.health import router as health_router
from app.api.middleware import RequestContextMiddleware
from app.core.logging import configure_logging
from app.core.tracing import instrument_app, instrument_libraries, setup_tracing
from app.db.session import engine

configure_logging(
    level=settings.LOG_LEVEL,
    json_logs=settings.LOG_JSON,
    service=f"{settings.SERVICE_NAME}-api",
    env=settings.ENV,
    release=settings.RELEASE,
)

logger = structlog.get_logger(__name__)
tracer_provider = setup_tracing(f"{settings.SERVICE_NAME}-api") if settings.OTEL_ENABLED else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        app.state.redis = create_redis(settings.REDIS_URL)
        stack.push_async_callback(app.state.redis.aclose)
        app.state.queue_pool = await create_pool(
            RedisSettings.from_dsn(settings.REDIS_URL),
            default_queue_name=QUEUE_NAME,
        )
        stack.push_async_callback(app.state.queue_pool.aclose)
        app.state.storage = await S3Storage.create(stack)

        if tracer_provider:
            stack.callback(tracer_provider.shutdown)
        if settings.METRIC_PORT:
            start_http_server(settings.METRIC_PORT)
        logger.info("app_started", env=settings.ENV, release=settings.RELEASE)
        yield
        logger.info("app_stopping")


def create_app() -> FastAPI:
    app = FastAPI(
        title="FastAPI-Module-Design",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router, prefix="/api/v1")
    if tracer_provider:
        instrument_libraries(engine)
        instrument_app(app)
    return app


app = create_app()

```

```python
# app/worker/settings.py（startup 内新增日志/追踪/指标初始化）
from arq import cron, func
from arq.connections import RedisSettings
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.domains.attachments.service import cleanup_attachments
from app.integrations.email import build_email_sender
from app.queue.names import QUEUE_NAME
from app.worker.jobs import ALL_JOBS
from app.domains.reminders.service import scan_due_tasks, report_queue_depth
from app.storage.s3 import S3Storage

from contextlib import AsyncExitStack
from prometheus_client import start_http_server
from app.core.logging import configure_logging
from app.core.tracing import instrument_libraries, setup_tracing


async def startup(ctx: dict) -> None:
    configure_logging(
        level=settings.LOG_LEVEL,
        json_logs=settings.LOG_JSON,
        service=f"{settings.SERVICE_NAME}-worker",
        env=settings.ENV,
        release=settings.RELEASE,
    )
    engine = create_async_engine(
        settings.DATABASE_URL,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
    )
    ctx["engine"] = engine
    ctx["session_factory"] = async_sessionmaker(engine, expire_on_commit=False)
    ctx["email"] = build_email_sender()

    from app.core.redis import create_redis
    ctx["redis"] = create_redis(settings.REDIS_URL)

    ctx["stack"] = AsyncExitStack()
    ctx["storage"] = await S3Storage.create(ctx["stack"])

    if settings.OTEL_ENABLED:
        ctx["tracer_provider"] = setup_tracing(f"{settings.SERVICE_NAME}-worker")
        instrument_libraries(engine)
    if settings.WORKER_METRICS_PORT:
        start_http_server(settings.WORKER_METRICS_PORT)


async def shutdown(ctx: dict) -> None:
    if provider := ctx.get("tracer_provider"):
        provider.shutdown()
    await ctx["stack"].aclose()
    await ctx["redis"].aclose()
    await ctx["engine"].dispose()


class WorkerSettings:
    functions = [func(job, max_tries=5) for job in ALL_JOBS]
    cron_jobs = [
        cron(scan_due_tasks, minute=set(range(0, 60, 5)), run_at_startup=False),
        cron(cleanup_attachments, minute=17),
        cron(report_queue_depth, second={0, 15, 30, 45}, unique=False),
    ]

    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    queue_name = QUEUE_NAME
    on_startup = startup
    on_shutdown = shutdown

    max_jobs = 10
    job_timeout = 300
    keep_result = 86400
    health_check_interval = 30

```

---

## 7. 测试

```
tests/
└── test_observability.py   ← 横切能力的测试不属于任何域，放在 tests/ 顶层
```

```python
# tests/conftest.py 最顶部（必须在 import app 之前）
import os
os.environ.setdefault("ENV", "test")
os.environ.setdefault("LOG_JSON", "false")
os.environ.setdefault("OTEL_ENABLED", "false")

# FakesStorage 添加ping()

class FakeStorage:
    ...
    async def ping(self) -> None:
        pass
```

```python
# tests/test_observability.py
import pytest_asyncio
import structlog
from httpx import ASGITransport, AsyncClient
from prometheus_client import REGISTRY
from structlog.testing import capture_logs

from app.api.deps import get_db, get_redis, get_storage
from app.core.logging import redact_sensitive
from app.domains.auth.deps import get_current_user
from app.main import app
from app.queue.client import ArqJobQueue
from app.worker.observability import instrumented_job


@pytest_asyncio.fixture
async def authed_client(session_factory, redis, user,storage):
    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()

def sample(name: str, labels: dict) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_request_id_generated_echoed_and_sanitized(client):
    resp = await client.get("/api/v1/tasks")
    assert len(resp.headers["X-Request-ID"]) == 32

    resp = await client.get("/api/v1/tasks", headers={"X-Request-ID": "upstream-abc-12345"})
    assert resp.headers["X-Request-ID"] == "upstream-abc-12345"

    resp = await client.get("/api/v1/tasks", headers={"X-Request-ID": "bad id!"})
    assert resp.headers["X-Request-ID"] != "bad id!"


async def test_probe_paths_are_not_logged(client):
    with capture_logs() as logs:
        await client.get("/livez")
    assert not [e for e in logs if e["event"] == "http_request"]


async def test_http_metrics_use_route_template(authed_client):
    task_id = (await authed_client.post("/api/v1/tasks", json={"title": "t"})).json()["id"]
    labels = {"method": "GET", "route": "/api/v1/tasks/{task_id}", "status": "200"}
    before = sample("http_requests_total", labels)
    await authed_client.get(f"/api/v1/tasks/{task_id}")
    assert sample("http_requests_total", labels) == before + 1


async def test_cache_metrics_from_tasks_domain(authed_client):
    task_id = (await authed_client.post("/api/v1/tasks", json={"title": "t"})).json()["id"]
    hit = {"name": "task_detail", "result": "hit"}
    miss = {"name": "task_detail", "result": "miss"}
    h0, m0 = sample("cache_requests_total", hit), sample("cache_requests_total", miss)

    await authed_client.get(f"/api/v1/tasks/{task_id}")
    await authed_client.get(f"/api/v1/tasks/{task_id}")

    assert sample("cache_requests_total", miss) == m0 + 1
    assert sample("cache_requests_total", hit) == h0 + 1


@app.get("/__boom", include_in_schema=False)
async def _boom():
    raise RuntimeError("secret internal detail")


async def test_unhandled_exception_is_safe_and_traceable():
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/__boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "internal_error"
    assert "secret internal detail" not in resp.text
    assert body["request_id"] == resp.headers["X-Request-ID"]


async def test_livez_and_readyz(authed_client):
    assert (await authed_client.get("/livez")).status_code == 200
    resp = await authed_client.get("/readyz")
    assert resp.status_code == 200 and resp.json()["status"] == "ok"


async def test_readyz_degraded_when_redis_down_but_still_ready(authed_client, redis, monkeypatch):
    async def down(*a, **k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(redis, "ping", down)
    resp = await authed_client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


async def test_readyz_unavailable_when_database_down(authed_client):
    class BrokenSession:
        async def execute(self, *a, **k):
            raise ConnectionError("db down")

    async def broken_db():
        yield BrokenSession()

    app.dependency_overrides[get_db] = broken_db
    resp = await authed_client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["status"] == "unavailable"


async def test_instrumented_job_records_success_and_binds_context():
    @instrumented_job
    async def sample_job(ctx, x):
        return x * 2

    labels = {"job_name": "sample_job", "status": "succeeded"}
    before = sample("jobs_total", labels)

    with capture_logs() as logs:
        result = await sample_job({"job_id": "j1", "job_try": 1}, 21, obs_ctx={"request_id": "req-1"})

    assert result == 42
    assert sample("jobs_total", labels) == before + 1
    assert [e["event"] for e in logs] == ["job_started", "job_finished"]


async def test_enqueue_propagates_request_id():
    captured = {}

    class FakePool:
        async def enqueue_job(self, function, *args, **kwargs):
            captured.update(kwargs)
            from types import SimpleNamespace
            return SimpleNamespace(job_id="j3")

    structlog.contextvars.bind_contextvars(request_id="req-xyz")
    try:
        await ArqJobQueue(FakePool()).enqueue("export_tasks", 1)
    finally:
        structlog.contextvars.clear_contextvars()

    assert captured["obs_ctx"]["request_id"] == "req-xyz"


def test_redact_sensitive_fields():
    event = redact_sensitive(None, "info", {"event": "login", "password": "hunter2", "user_id": 1})
    assert event["password"] == "***"
    assert event["user_id"] == 1
```

```bash
pytest tests/test_observability.py -v
pytest tests/domains/ -v   # 确认所有域的既有测试仍然全部通过
```

---
