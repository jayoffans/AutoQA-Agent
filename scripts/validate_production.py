"""Offline production settings validation; no LLM or database network request."""
import json
import os

from sqlalchemy.engine import make_url

from autoqa.config import Settings


def validate():
    settings = Settings(_env_file=None, env="production", port=9081, database_url="sqlite:///./work/validation.db")
    assert settings.host == "0.0.0.0" and settings.port == 9081
    assert settings.demo_mode and not settings.demo_llm_enabled and settings.llm_provider == "mock"
    assert not settings.mcp_enabled and settings.seed_demo
    mysql = Settings(_env_file=None, env="production",
                     database_url="mysql://example:encoded%40password@db.example:3306/qa")
    assert make_url(mysql.database_url).drivername == "mysql+pymysql"
    assert "encoded@password" not in repr(mysql)
    if os.getenv("DEMO_MODE") == "false":
        raise RuntimeError("Run validation without local DEMO_MODE=false overrides")
    print(json.dumps({"production_configuration": "passed", "dynamic_port": "passed",
                      "mysql_url_normalization": "passed", "safe_demo_defaults": "passed"}, indent=2))


if __name__ == "__main__":
    validate()
