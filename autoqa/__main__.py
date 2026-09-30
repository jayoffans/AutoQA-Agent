"""Portable production entry point; PORT works in Docker and Railway."""
import logging

import uvicorn

from .config import Settings
from .safe_logging import configure_logging


def main():
    configure_logging()
    try:
        from .app import create_app
        settings = Settings()
    except Exception:
        logging.getLogger(__name__).error("Invalid startup configuration", extra={"event": "invalid_configuration"})
        raise SystemExit(1) from None
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, workers=1,
                access_log=False, log_config=None, proxy_headers=False)


if __name__ == "__main__":
    main()
