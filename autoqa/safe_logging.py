"""Allowlisted JSON logs: never serialize exception messages, paths or request bodies."""
import json
import logging


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
