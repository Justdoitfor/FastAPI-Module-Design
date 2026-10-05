from prometheus_client import Counter, Gauge, Histogram

# HTTP(RED)
HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP请求总数",
    ["method", "route", "status"]
)

HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP请求耗时",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
)

HTTP_IN_FLIGHT = Gauge(
    "http_requests_in_flight",
    "正在处理的请求数",
)

# 缓存与限流
CACHE_REQUESTS = Counter(
    "cache_requests_total",
    "缓存查询次数",
    ["name", "result"]
)

CACHE_ERRORS = Counter(
    "cache_errors_total",
    "缓存异常次数",
    ["op"]
)

RATE_LIMIT_DECISIONS = Counter(
    "rate_limit_decisions_total",
    "限流判定",
    ["scope", "decision"]
)

# 后台任务
JOBS = Counter(
    "jobs_total",
    "任务执行次数",
    ["job_name", "status"]
)

JOB_DURATION = Histogram(
    "job_duration_seconds",
    "任务耗时",
    ["job_name"],
    buckets=(0.1, 0.5, 1.0, 5.0, 15.0, 60.0, 300.0, 600.0)
)

QUEUE_DEPTH = Gauge(
    "queue_depth",
    "队列中等待执行的任务数"
)

UPLOADS = Counter(
    "uploads_total",
    "上传结果",
    ["path", "result"]
)
