"""Production deployment contract without changing Demo/development defaults."""
import asyncio
import os
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from autoqa.config import Settings
from tests.production_support import clean_env, random_key


def test_required_environment_values_are_injected(monkeypatch, tmp_path):
    values = clean_env(AUTOQA_MODE="production", AUTOQA_ENV="production", AUTOQA_SECRET_KEY=random_key(),
        DATABASE_URL=f"sqlite:///{tmp_path / 'injected.sqlite'}", AUTOQA_DATA_DIR=tmp_path / "custom-data",
        AUTOQA_ARTIFACT_DIR=tmp_path / "custom-artifacts")
    for key in list(os.environ):
        if key.startswith(("AUTOQA_", "MYSQL", "DEMO_")) or key == "DATABASE_URL":
            monkeypatch.delenv(key)
    for key, value in values.items():
        if key.startswith("AUTOQA_") or key == "DATABASE_URL":
            monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)
    assert settings.mode == "production" and settings.env == "production"
    assert settings.secret_key == values["AUTOQA_SECRET_KEY"]
    assert make_url(settings.database_url) == make_url(values["DATABASE_URL"])
    assert settings.data_dir == tmp_path / "custom-data" and settings.artifact_dir == tmp_path / "custom-artifacts"
    assert settings.secret_key not in repr(settings)


@pytest.mark.parametrize("url", [None, ""])
def test_production_database_address_must_be_explicit(monkeypatch, url):
    for key in ("DATABASE_URL", "AUTOQA_DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    options = {} if url is None else {"database_url": url}
    with pytest.raises(ValidationError, match="explicit DATABASE_URL"):
        Settings(_env_file=None, mode="production", env="production", secret_key=random_key(), **options)


def test_development_sqlite_default_and_demo_seed_unchanged(monkeypatch):
    for key in ("DATABASE_URL", "AUTOQA_DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    development = Settings(_env_file=None, env="development", mode=None)
    demo = Settings(_env_file=None, env="production", mode="demo")
    assert development.database_url == "sqlite:///./data/autoqa.db"
    assert demo.demo_mode and demo.seed_demo and demo.llm_provider == "mock"


def test_production_compose_requires_credentials_and_preserves_named_volumes():
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "compose.production.yaml").read_text())
    app = config["services"]["autoqa"]
    for key in ("AUTOQA_MODE", "AUTOQA_ENV", "AUTOQA_SECRET_KEY", "DATABASE_URL"):
        assert app["environment"][key].startswith("${" + key + ":?")
    for key in ("AUTOQA_DATA_DIR", "AUTOQA_ARTIFACT_DIR"):
        assert app["environment"][key].startswith("${" + key)
    assert set(config["volumes"]) == {"mysql-data", "checkpoints", "evidence"}
    assert app["volumes"] == ["checkpoints:/app/data", "evidence:/app/artifacts"]
    assert config["services"]["mysql"]["volumes"] == ["mysql-data:/var/lib/mysql"]
    for key in ("MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD"):
        assert config["services"]["mysql"]["environment"][key].startswith("${" + key + ":?")


def test_shutdown_with_completed_jobs_from_closed_server_loop():
    from autoqa.product_runner import ProductRunner

    async def completed():
        return "completed"

    previous = asyncio.new_event_loop()
    task = previous.create_task(completed())
    previous.run_until_complete(task)
    previous.close()
    runner = ProductRunner.__new__(ProductRunner)
    runner.jobs, runner.runners, runner.transient = {"completed": task}, {}, {"temporary": "fixture"}
    asyncio.run(runner.shutdown())
    assert runner.jobs == runner.transient == {}
