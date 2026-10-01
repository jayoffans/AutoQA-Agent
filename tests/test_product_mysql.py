"""Production runtime parity on an explicitly provisioned disposable MySQL database."""
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from autoqa.app import create_app
from autoqa.config import Settings
from tests.test_product import KEY, SECRET, SPEC, api_plan, register, wait


def test_mysql_production_identity_byok_environments_execution_and_restart(tmp_path):
    import json
    url = os.getenv("AUTOQA_TEST_PRODUCT_MYSQL_URL")
    if not url:
        pytest.skip("No disposable Production MySQL supplied; real MySQL acceptance runs in CI")
    settings = Settings(_env_file=None, mode="production", env="test", secret_key=SECRET,
                        database_url=url, artifact_dir=tmp_path / "artifacts", allow_private_targets=True)
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        register(client)
        assert client.get("/api/dashboard").json()["projects"] == 0
        project = client.post("/api/projects", json={"name": "MySQL Production", "url": "http://127.0.0.1:8765", "project_type": "api"}).json()
        client.application = client.app
        client.app.state.gateway.transport_factory = lambda: httpx.MockTransport(lambda r: httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(api_plan())}}]}) if "/chat/completions" in r.url.path else httpx.Response(200, json={"id": 1}))
        assert client.put("/api/provider", json={"provider": "compatible", "base_url": "http://127.0.0.1:8765", "api_key": KEY, "model": "fixture"}).status_code == 200
        assert client.put(f'/api/projects/{project["id"]}/environment', json={"base_url": project["url"], "secrets": {"TOKEN": "mysql-fixture-token"}}).status_code == 200
        assert client.post(f'/api/projects/{project["id"]}/openapi', json={"content": json.dumps(SPEC)}).status_code == 200
        response = client.post("/api/tasks", json={"project_id": project["id"], "goal": "检查用户接口", "endpoints": ["GET /users/{userId}"]})
        task_id = response.json()["id"]
        wait(client, task_id, "awaiting_approval")
        client.post(f"/api/tasks/{task_id}/approve", json={"approved": True})
        assert wait(client, task_id, "passed")["metrics"]["passed"] == 1
        cookies = dict(client.cookies)
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        client.cookies.update(cookies)
        assert client.get(f"/api/tasks/{task_id}/report").status_code == 200
        assert client.get("/api/provider").json()["key_mask"] == "sk-••••••••1234"
        assert client.get("/api/dashboard").json()["passed"] == 1
