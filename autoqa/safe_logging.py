"""Allowlisted JSON logs: never serialize exception messages, paths or request bodies."""
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar

_private_http = ContextVar("private_api_http", default=False)


class PrivateHTTPFilter(logging.Filter):
    """Remove HTTP library payloads only within the API execution task's context."""
    def filter(self, record):
        if _private_http.get():
            record.msg, record.args = "API HTTP event", ()
            record.exc_info = record.exc_text = None
            record.event = "api_http_event"
        return True


@contextmanager
def private_http_logs():
    for name in ("httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2",
                 "httpcore.proxy", "httpcore.socks"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, PrivateHTTPFilter) for item in logger.filters):
            logger.addFilter(PrivateHTTPFilter())
    token = _private_http.set(True)
    try:
        yield
    finally:
        _private_http.reset(token)


class SafeFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({"level": record.levelname, "logger": record.name,
                           "event": getattr(record, "event", "service_event"),
                           "exception_type": record.exc_info[0].__name__ if record.exc_info else None})


def configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(SafeFormatter())
    logging.basicConfig(handlers=[handler], level=logging.INFO, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
