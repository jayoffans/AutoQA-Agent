"""Initialize an empty database with the same idempotent startup seed."""
from fastapi.testclient import TestClient

from autoqa.app import create_app
from autoqa.config import Settings

if __name__ == "__main__":
    settings = Settings()
    settings.seed_demo = True
    with TestClient(create_app(settings)) as client:
        client.get("/health").raise_for_status()
    print("Demo seed initialized; existing task history preserved.")
