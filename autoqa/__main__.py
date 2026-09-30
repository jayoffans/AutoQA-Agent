"""Portable production entry point; PORT works in Docker and Railway."""
import uvicorn

from .app import create_app
from .config import Settings


def main():
    settings = Settings()
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.port, workers=1, access_log=False)


if __name__ == "__main__":
    main()
