"""Validate deployment structure; run the real Compose validator only if installed."""
import json
import os
import secrets
import shutil
import subprocess
import tomllib
from pathlib import Path

import yaml

root = Path(__file__).resolve().parents[1]
compose = yaml.safe_load((root / "compose.yaml").read_text(encoding="utf-8-sig"))
assert {"autoqa", "mysql"} <= set(compose["services"])
application = compose["services"]["autoqa"]
assert application["depends_on"]["mysql"]["condition"] == "service_healthy"
assert application["environment"]["DATABASE_URL"] == ""
assert "Set MYSQL_PASSWORD" in application["environment"]["MYSQLPASSWORD"]
assert application["volumes"] and compose["services"]["mysql"]["volumes"]
assert yaml.safe_load((root / "docker-compose.yml").read_text())["include"] == ["compose.yaml"]
railway = tomllib.loads((root / "railway.toml").read_text())
assert railway["build"]["builder"] == "DOCKERFILE"
assert railway["deploy"]["healthcheckPath"] == "/api/health"
dockerfile = (root / "Dockerfile").read_text()
assert 'CMD ["python", "-m", "autoqa"]' in dockerfile and "USER autoqa" in dockerfile
for name in ["Dockerfile", "compose.yaml", "docker-compose.yml", "railway.toml"]:
    assert "C:\\" not in (root / name).read_text() and "D:\\" not in (root / name).read_text()
result = {"structural_validation": "passed", "docker_compose_config": "not_run", "docker_runtime": "not_run"}
if shutil.which("docker"):
    env = {**os.environ, "MYSQL_PASSWORD": secrets.token_urlsafe(24), "MYSQL_ROOT_PASSWORD": secrets.token_urlsafe(24)}
    process = subprocess.run(["docker", "compose", "--env-file", ".env.example", "config", "--quiet"],
                             cwd=root, env=env, capture_output=True)
    if process.returncode:
        raise RuntimeError("docker compose config failed (output withheld to protect interpolated secrets)")
    result["docker_compose_config"] = "passed"
else:
    result["unverified_reason"] = "Docker CLI/engine is not installed on this host"
print(json.dumps(result, indent=2))
