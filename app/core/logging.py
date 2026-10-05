import logging
import sys

import structlog
from opentelemetry import trace

_SENSITIVE_KEYS = {
    "password",
    "passwd",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "secret",
    "api_key",
    "cookie",
    "set-cookie",
}


def redact_sensitive(logger, method_name, event_dict):
    for key in list(event_dict):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "***"
    return event_dict


def add_trace_ids(logger, method_name, event_dict):
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


def configure_logging(
        *,
        level: str,
        json_logs: bool,
        service: str,
        env: str,
        release: str = "dev"
) -> None:
    shared = [
        structlog.contextvars.merge_contextvars,
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
        cache_logger_on_first_use=(env != "test")
    )

    if json_logs:
        final = [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    else:
        final = [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(),
        ]

    formatter = structlog.stdlib.ProcessorFormatter(foreign_pre_chain=shared, processors=final)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True

    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("arq.worker").setLevel(logging.WARNING)
