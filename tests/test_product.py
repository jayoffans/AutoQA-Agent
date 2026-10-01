"""Phase 5 acceptance uses only fixture credentials and mock HTTP; no paid models."""
import base64
import json
import logging
import secrets
import socket
import time
from pathlib import Path

import httpx
import pytest
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from autoqa.app import create_app
from autoqa.config import Settings
from autoqa.contracts import parse_contract
from autoqa.crypto import SecretBox, mask_key, token_hash
from autoqa.migrations import upgrade
from autoqa.product_models import EnvironmentRow, ProviderConfigRow, UserRow
from autoqa.product_runner import validate_plan
from autoqa.providers import ProviderError
from autoqa.store import Store
from autoqa.studio_models import ProjectRow
from autoqa.target_network import PinnedTransport, PublicPolicy

KEY = "sk-fixture-1234"
SECRET = base64.urlsafe_b64encode(b"a" * 32).decode()
SPEC = {"openapi": "3.1.0", "paths": {
    "/users/{userId}": {"get": {"summary": "User", "parameters": [{"name": "userId", "in": "path", "required": True,
        "schema": {"type": "integer"}}], "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/User"}}}}}}},
    "/orders": {"post": {"requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                             "responses": {"201": {"description": "Created"}}}}},
    "components": {"schemas": {"User": {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}}}}}


def api_plan():
    return {"summary": "检查用户接口", "scope": "GET /users/{userId}", "preconditions": ["用户 1 存在"], "cases": [
        {"case_id": "API-1", "name": "用户查询", "method": "GET", "path": "/users/1", "expected_status": 200,
         "expected": "响应包含整数 id", "response_schema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}}}]}


@pytest.fixture
def product(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, mode="production", env="test", secret_key=SECRET,
        database_url=f"sqlite:///{tmp_path / 'production.sqlite'}", artifact_dir=tmp_path / "artifacts",
        data_dir=tmp_path / "data", seed_demo=True, openai_api_key="sk-developer-forbidden", llm_provider="mock")
    original_dns = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *args, **kwargs:
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        if host in {"qa.example", "model.example"} else original_dns(host, *args, **kwargs))
    app = create_app(settings)
    with TestClient(app, base_url="https://testserver") as client:
        calls = []
        def handler(request):
            calls.append(request)
            if request.url.host == "model.example":
                assert request.headers["Authorization"] == "Bearer " + KEY
                body = json.loads(request.content)
                assert "sk-developer-forbidden" not in request.content.decode()
                result = api_plan() if "RealPlan" in body["messages"][0]["content"] else {"ok": True}
                return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})
            if request.url.path == "/openapi.json":
                return httpx.Response(200, json=SPEC)
            return httpx.Response(200, json={"id": 1, "echo": "fixture-env-password", "access_token": "private-response-token"})
        app.state.gateway.transport_factory = lambda: httpx.MockTransport(handler)
        client.settings, client.application, client.calls = settings, app, calls
        yield client


def register(client, email="alice@example.com"):
    response = client.post("/api/auth/register", json={"email": email, "password": "fixture-login-password"})
    assert response.status_code == 201, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client.get("/api/auth/me").json()


def project(client, kind="api"):
    response = client.post("/api/projects", json={"name": "真实测试项目", "url": "https://qa.example", "project_type": kind})
    assert response.status_code == 201, response.text
    return response.json()


def provider(client, save_mode="workspace"):
    response = client.put("/api/provider", json={"provider": "compatible", "base_url": "https://model.example/v1",
                                               "api_key": KEY, "model": "fixture-model", "save_mode": save_mode})
    assert response.status_code == 200, response.text
    return response.json()


def wait(client, task_id, expected):
    end = time.monotonic() + 15
    while time.monotonic() < end:
        result = client.get("/api/tasks/" + task_id)
        assert result.status_code == 200, result.text
        task = result.json()
        if task["status"] == expected:
            return task
        if task["status"] == "system_error" and expected != "system_error":
            pytest.fail(json.dumps(task, ensure_ascii=False))
        time.sleep(0.03)
    pytest.fail(f"Task did not reach {expected}: {task}")


def create_api_task(client, item):
    response = client.post(f'/api/projects/{item["id"]}/openapi', json={"content": json.dumps(SPEC)})
    assert response.status_code == 200, response.text
    response = client.post("/api/tasks", json={"project_id": item["id"], "goal": "检查用户查询结果", "endpoints": ["GET /users/{userId}"]})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_production_empty_and_no_demo_assets(product):
    assert product.get("/api/dashboard").status_code == 401
    register(product)
    assert all(value == 0 for value in product.get("/api/dashboard").json().values())
    assert product.get("/api/projects").json() == product.get("/api/tasks").json() == []
    assert product.settings.seed_demo is False and product.settings.provider_key == ""
    assert product.settings.model_enabled is False
    for path in ["/demo/login.html", "/demo/api/login", "/app.js", "/static/../data/db", "/docs", "/openapi.json"]:
        assert product.get(path).status_code == 404
    assert "Login API" not in product.get("/").text
    with Session(product.application.state.store.engine) as session:
        assert list(session.scalars(select(ProjectRow))) == []


def test_identity_password_hash_session_and_csrf(product):
    identity = register(product)
    assert identity["workspaces"][0]["role"] == "owner"
    assert identity["workspaces"][0]["name"] == "个人工作区"
    with Session(product.application.state.store.engine) as session:
        user = session.get(UserRow, identity["user_id"])
        assert user.password_hash.startswith("scrypt$") and "fixture-login-password" not in user.password_hash
    assert product.post("/api/projects", headers={"X-CSRF-Token": "wrong"}, json={"name": "x", "url": "https://qa.example"}).status_code == 403
    assert product.post("/api/projects", headers={"Origin": "https://evil.example"}, json={"name": "x", "url": "https://qa.example"}).status_code == 403
    assert product.get("/api/dashboard", headers={"X-Workspace-ID": str(secrets.token_hex(18))}).status_code == 404
    assert product.post("/api/auth/logout").status_code == 204
    assert product.get("/api/auth/me").status_code == 401
    assert product.post("/api/auth/login", json={"email": "alice@example.com", "password": "bad-password"}).status_code == 401
    assert product.post("/api/auth/login", json={"email": "alice@example.com", "password": "fixture-login-password"}).status_code == 200


def test_workspace_byok_encrypt_mask_and_no_plaintext(product, caplog):
    identity = register(product)
    caplog.set_level(logging.DEBUG)
    config = provider(product)
    assert config["key_mask"] == "sk-••••••••1234" and "api_key" not in config
    with Session(product.application.state.store.engine) as session:
        row = session.get(ProviderConfigRow, identity["workspace_id"])
        assert KEY not in row.encrypted_api_key
        assert SecretBox(SECRET).decrypt(row.encrypted_api_key, "provider:" + identity["workspace_id"]) == KEY
        with pytest.raises(InvalidTag):
            SecretBox(SECRET).decrypt(row.encrypted_api_key, "provider:other-workspace")
    assert KEY not in product.get("/api/provider").text
    assert product.post("/api/provider/test", json={"provider": "compatible", "base_url": "https://model.example/v1",
        "api_key": KEY, "model": "fixture-model"}).json() == {"ok": True}
    assert KEY not in caplog.text and "sk-developer-forbidden" not in caplog.text
    assert product.delete("/api/provider").status_code == 204
    assert product.get("/api/provider").json() == {"configured": False}


def test_session_key_not_persisted_or_shared_and_removed_on_logout(product):
    register(product)
    provider(product, "session")
    assert product.get("/api/provider").json()["save_mode"] == "session"
    with Session(product.application.state.store.engine) as session:
        assert list(session.scalars(select(ProviderConfigRow))) == []
    cookie = product.cookies.get("autoqa_session")
    other = TestClient(product.application, base_url="https://testserver")
    login = other.post("/api/auth/login", json={"email": "alice@example.com", "password": "fixture-login-password"})
    assert login.status_code == 200
    assert other.get("/api/provider").json() == {"configured": False}
    assert product.post("/api/auth/logout").status_code == 204
    assert not any(key[0] == token_hash(cookie) for key in product.application.state.gateway.session_configs)


@pytest.mark.parametrize("configured", [False, True])
def test_no_developer_or_mock_fallback(product, configured):
    register(product)
    item = project(product)
    if configured:
        provider(product)
        product.application.state.gateway.transport_factory = lambda: httpx.MockTransport(lambda r: httpx.Response(401))
        task_id = create_api_task(product, item)
        task = wait(product, task_id, "system_error")
        assert task["steps"] == [] and task["findings"] == [] and task["metrics"]["failed"] == 0
    else:
        response = product.post("/api/tasks", json={"project_id": item["id"], "goal": "检查接口", "endpoints": []})
        assert response.status_code == 409 and "尚未配置" in response.text
        assert product.calls == []
    response = product.put("/api/provider", json={"provider": "mock", "base_url": "https://model.example", "api_key": KEY, "model": "mock"})
    assert response.status_code == 422


def test_api_contract_human_edit_execution_and_history(product):
    register(product)
    item = project(product)
    provider(product)
    task_id = create_api_task(product, item)
    review = wait(product, task_id, "awaiting_approval")
    assert not any(r.url.host == "qa.example" for r in product.calls)
    plan = review["plan"]
    plan["cases"].append({**plan["cases"][0], "case_id": "API-2", "name": "增加的用例", "expected_status": 404})
    assert product.patch(f"/api/tasks/{task_id}/plan", json=plan).status_code == 200
    plan["cases"] = plan["cases"][1:]
    assert product.patch(f"/api/tasks/{task_id}/plan", json=plan).status_code == 200
    assert product.post(f"/api/tasks/{task_id}/approve", json={"approved": True, "plan_hash": review["plan_hash"]}).status_code == 409
    assert product.post(f"/api/tasks/{task_id}/approve", json={"approved": True}).status_code == 202
    result = wait(product, task_id, "failed")
    assert len(result["steps"]) == 1 and result["steps"][0]["case_id"] == "API-2"
    assert result["metrics"]["failed"] == 1 and result["metrics"]["passed"] == 0
    assert len(result["findings"]) == 1
    assert result["diagnosis"]["root_cause_confirmed"] is False
    assert product.get(f"/api/tasks/{task_id}/findings").json()[0]["suggestion_label"] == "AI 分析建议"
    assert product.get(f'/api/projects/{item["id"]}/runs').json()[0]["report"]
    dashboard = product.get("/api/dashboard").json()
    assert dashboard["projects"] == dashboard["executions"] == dashboard["failed"] == dashboard["issues"] == 1
    assert "private-response-token" not in product.get(f"/api/tasks/{task_id}/report").text


def test_environment_secret_roundtrip_and_artifact_redaction(product):
    register(product)
    item = project(product)
    response = product.put(f'/api/projects/{item["id"]}/environment', json={"base_url": item["url"],
        "headers": {"Authorization": "Bearer fixture-header-secret"}, "variables": {"USER_ID": "1", "TOKEN": "fixture-env-token"},
        "password": "fixture-env-password", "username": "fixture-account", "secrets": {"CUSTOM": "fixture-custom-secret"}})
    assert response.status_code == 200
    for secret in ("fixture-env-token", "fixture-env-password", "fixture-header-secret", "fixture-custom-secret"):
        assert secret not in response.text
    with Session(product.application.state.store.engine) as session:
        row = session.get(EnvironmentRow, item["id"])
        assert "fixture-env-password" not in row.encrypted_secrets
        assert "fixture-env-token" not in json.dumps(row.variables)
        assert "fixture-header-secret" not in json.dumps(row.headers)
    provider(product)
    task_id = create_api_task(product, item)
    wait(product, task_id, "awaiting_approval")
    assert product.post(f"/api/tasks/{task_id}/approve", json={"approved": True}).status_code == 202
    result = wait(product, task_id, "passed")
    assert "fixture-env-password" not in json.dumps(result)
    evidence = product.get(f"/api/tasks/{task_id}/artifacts/api-case-1.json")
    assert evidence.status_code == 200 and "fixture-env-password" not in evidence.text
    requests = [r for r in product.calls if r.url.host == "qa.example"]
    assert requests[0].headers["Authorization"] == "Bearer fixture-header-secret"
    assert product.delete(f'/api/projects/{item["id"]}/environment').status_code == 204
    assert product.get(f'/api/projects/{item["id"]}/environment').json()["secret_names"] == []


def test_user_workspace_project_task_and_artifact_isolation(product):
    alice = register(product)
    item = project(product)
    provider(product)
    task_id = create_api_task(product, item)
    wait(product, task_id, "awaiting_approval")
    bob = TestClient(product.application, base_url="https://testserver")
    bob_id = register(bob, "bob@example.com")
    assert bob_id["workspace_id"] != alice["workspace_id"]
    assert bob.get("/api/projects").json() == [] and bob.get("/api/tasks").json() == []
    assert all(value == 0 for value in bob.get("/api/dashboard").json().values())
    for path in [f'/api/projects/{item["id"]}', f'/api/projects/{item["id"]}/environment',
                 f'/api/projects/{item["id"]}/endpoints', f'/api/projects/{item["id"]}/runs',
                 f"/api/tasks/{task_id}", f"/api/tasks/{task_id}/workflow", f"/api/tasks/{task_id}/findings",
                 f"/api/tasks/{task_id}/report", f"/api/tasks/{task_id}/report?format=json",
                 f"/api/tasks/{task_id}/events", f"/api/tasks/{task_id}/artifacts/report.html"]:
        assert bob.get(path).status_code == 404, path
    for suffix in ["approve", "cancel", "regenerate"]:
        assert bob.post(f"/api/tasks/{task_id}/{suffix}", json={"approved": True} if suffix=="approve" else {}).status_code == 404
    assert bob.patch(f"/api/tasks/{task_id}/plan", json=api_plan()).status_code == 404
    assert bob.post("/api/tasks", json={"project_id": item["id"], "goal": "检查接口"}).status_code == 404
    own = project(bob)
    assert product.patch(f"/api/tasks/{task_id}/project", json={"project_id": own["id"]}).status_code == 404
    assert bob.get("/api/provider", headers={"X-Workspace-ID": alice["workspace_id"]}).status_code == 404
    assert bob.get("/api/provider").json() == {"configured": False}


def test_owner_member_access_and_provider_permissions(product):
    alice = register(product)
    bob = TestClient(product.application, base_url="https://testserver")
    register(bob, "bob@example.com")
    assert product.post("/api/workspace/members", json={"email": "bob@example.com", "role": "member"}).status_code == 201
    item = project(product)
    bob.headers["X-Workspace-ID"] = alice["workspace_id"]
    assert bob.get(f'/api/projects/{item["id"]}').status_code == 200
    assert bob.put("/api/provider", json={"provider": "openai", "base_url": "https://model.example", "api_key": KEY, "model": "fixture"}).status_code == 403
    assert bob.post("/api/workspace/members", json={"email": "bob@example.com"}).status_code == 403


def test_api_network_error_is_not_business_failure(product):
    register(product)
    item = project(product)
    provider(product)
    task_id = create_api_task(product, item)
    wait(product, task_id, "awaiting_approval")
    def broken(request):
        raise httpx.ConnectError("fixture failure " + KEY)
    product.application.state.gateway.transport_factory = lambda: httpx.MockTransport(broken)
    assert product.post(f"/api/tasks/{task_id}/approve", json={"approved": True}).status_code == 202
    result = wait(product, task_id, "system_error")
    assert result["metrics"]["failed"] == 0 and result["metrics"]["errors"] == 1
    assert result["findings"] == [] and result["steps"][0]["status"] == "system_error"
    assert KEY not in json.dumps(result)


@pytest.mark.parametrize("provider_name", ["deepseek", "openai", "compatible"])
def test_gateway_selects_user_provider_without_environment_keys(product, provider_name):
    register(product)
    data = {"provider": provider_name, "base_url": "https://model.example", "model": "fixture-model", "api_key": KEY}
    assert product.post("/api/provider/test", json=data).status_code == 200
    assert len(product.calls) == 1 and product.calls[0].url.path == "/chat/completions"


@pytest.mark.parametrize("url", ["http://localhost", "http://127.0.0.1", "http://10.1.2.3", "http://172.16.1.1",
    "http://192.168.1.1", "http://169.254.169.254/latest/meta-data", "http://[::1]", "http://[::ffff:127.0.0.1]",
    "file:///etc/passwd", "ftp://example.com", "http://user:secret@qa.example", "https://qa.example:22"])
def test_production_ssrf_boundaries(product, url):
    register(product)
    assert product.post("/api/projects", json={"name": "拒绝目标", "url": url}).status_code == 422


def test_dns_rebinding_pins_connection_and_preserves_tls_hostname(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kw: [(2, 1, 6, "", ("93.184.216.34", 443))])
    seen = []
    transport = PinnedTransport()
    class Inner(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            seen.append(request)
            return httpx.Response(200, content=b"ok")
    transport.inner = Inner()
    import asyncio
    asyncio.run(transport.handle_async_request(httpx.Request("GET", "https://qa.example/users")))
    assert seen[0].url.host == "93.184.216.34" and seen[0].headers["Host"] == "qa.example"
    assert seen[0].extensions["sni_hostname"] == "qa.example"
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kw: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError):
        asyncio.run(transport.handle_async_request(httpx.Request("GET", "https://qa.example/users")))


@pytest.mark.parametrize("format", ["json", "yaml", "swagger"])
def test_openapi_arbitrary_paths_parameters_bodies_and_schemas(format):
    document = json.loads(json.dumps(SPEC))
    if format == "swagger":
        document.pop("openapi")
        document["swagger"] = "2.0"
        document["paths"]["/orders"]["post"]["parameters"] = [{"name": "body", "in": "body", "schema": {"type": "object"}}]
    if format == "yaml":
        import yaml
        content = yaml.safe_dump(document)
    else:
        content = json.dumps(document)
    endpoints = parse_contract(content)
    assert {(e["method"], e["path"]) for e in endpoints} == {("GET", "/users/{userId}"), ("POST", "/orders")}
    by_path = {e["path"]: e for e in endpoints}
    assert by_path["/users/{userId}"]["parameters"][0]["name"] == "userId"
    assert by_path["/orders"]["request_body"]["content"]["application/json"]["schema"] == {"type": "object"}


def test_remote_refs_yaml_aliases_and_unsupported_plan_targets_rejected(product):
    register(product)
    item = project(product)
    document = json.loads(json.dumps(SPEC))
    document["paths"]["/orders"] = {"$ref": "http://169.254.169.254"}
    assert product.post(f'/api/projects/{item["id"]}/openapi', json={"content": json.dumps(document)}).status_code == 422
    with pytest.raises(ValueError):
        parse_contract("openapi: 3.0.0\nx: &anchor [a,b]\ny: *anchor")
    row = {"request": {"test_type": "api", "endpoint_contracts": parse_contract(json.dumps(SPEC))}}
    plan = api_plan()
    plan["cases"][0]["path"] = "/admin"
    with pytest.raises(ValueError):
        validate_plan(plan, row, PublicPolicy(product.settings))
    plan["cases"][0]["path"] = "/users/1"
    plan["cases"][0]["response_schema"] = {"type": "object", "properties": {"access_token": {"type": "string"}}}
    assert validate_plan(plan, row, PublicPolicy(product.settings))["cases"][0]["response_schema"]["properties"]["access_token"] == {"type": "string"}
    plan["cases"][0]["response_schema"] = {"$ref": "file:///etc/passwd"}
    with pytest.raises(ValueError):
        validate_plan(plan, row, PublicPolicy(product.settings))


def test_openapi_url_and_manual_endpoint_import(product):
    register(product)
    item = project(product)
    response = product.post(f'/api/projects/{item["id"]}/openapi', json={"url": "https://qa.example/openapi.json"})
    assert response.status_code == 200 and len(response.json()) == 2
    response = product.post(f'/api/projects/{item["id"]}/openapi', json={"endpoints": [{"path": "/arbitrary", "method": "PATCH"}]})
    assert response.status_code == 200 and response.json()[0]["method"] == "PATCH"


def test_legacy_database_migration_preserves_data_and_mode_refuses_reuse(tmp_path):
    from alembic import command
    from alembic.config import Config
    url = f"sqlite:///{tmp_path / 'legacy.sqlite'}"
    engine = create_engine(url)
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0001_legacy")
        connection.execute(text("INSERT INTO projects (id,name,description,url,builtin,archived,created_at,updated_at) "
            "VALUES ('legacy','Login API Quality Demo','','https://qa.example',1,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO tasks (id,status,request,created_at,updated_at) "
            "VALUES ('legacy-task','passed','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO task_metadata (task_id,project_id,title,test_type) "
            "VALUES ('legacy-task','legacy','Legacy history','api')"))
    upgrade(engine)
    upgrade(engine)
    with engine.connect() as connection:
        row = connection.execute(text("SELECT name,workspace_id FROM projects")).one()
        assert row == ("Login API Quality Demo", None)
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0002_product"
        assert connection.execute(text("SELECT project_id FROM task_metadata")).scalar() == "legacy"
    for table in ["users", "workspaces", "workspace_members", "provider_configs", "environment_configs"]:
        assert inspect(engine).has_table(table)
    with pytest.raises(ValueError, match="clean database"):
        Store(url, mode="production")
    engine.dispose()


@pytest.mark.parametrize("mode", ["demo", None])
def test_production_database_cannot_be_opened_as_demo(tmp_path, mode):
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    store = Store(url, mode="production")
    store.engine.dispose()
    with pytest.raises(ValueError, match="separate databases"):
        Store(url, mode=mode)


async def test_seed_helper_cannot_write_production_fixture(product):
    from autoqa.seed import seed_demo
    with pytest.raises(ValueError, match="forbidden"):
        await seed_demo(product.application.state.store, product.settings, product.application)
    assert product.application.state.store.projects() == []


def test_chinese_i18n_keys_and_production_assets(product):
    root = Path(__file__).parents[1] / "autoqa/static/locales"
    zh, en = [json.loads((root / (name + ".json")).read_text(encoding="utf-8")) for name in ["zh-CN", "en-US"]]
    assert set(zh) == set(en)
    assert zh["dashboard"] == "工作台" and zh["firstProject"] == "创建你的第一个测试项目"
    assert zh["modelMissing"] == "尚未配置模型服务"
    assert product.get("/locales/zh-CN.json").status_code == 200
    assert product.get("/production.js").status_code == 200
    for value in ["abcdefgh", "sk-a", "sk-fixture-9999"]:
        assert value not in mask_key(value)


def test_production_global_provider_factory_refuses_mock_and_developer_keys(product):
    from autoqa.providers import get_provider
    with pytest.raises(ProviderError, match="workspace BYOK"):
        get_provider(product.settings)


def test_byok_failure_is_sanitized_and_returns_no_key(product, caplog):
    register(product)
    caplog.set_level(logging.DEBUG)
    product.application.state.gateway.transport_factory = lambda: httpx.MockTransport(lambda r: httpx.Response(
        401, json={"error": KEY, "authorization": "Bearer " + KEY}))
    response = product.post("/api/provider/test", json={"provider": "compatible", "base_url": "https://model.example", "api_key": KEY, "model": "fixture"})
    assert response.status_code == 502 and KEY not in response.text and KEY not in caplog.text


def test_workspace_key_survives_restart_but_session_key_does_not(product):
    register(product)
    persistent = provider(product)
    saved_cookies = dict(product.cookies)
    with TestClient(create_app(product.settings), base_url="https://testserver") as reopened:
        reopened.cookies.update(saved_cookies)
        assert reopened.get("/api/provider").json() == persistent
    product.delete("/api/provider")
    provider(product, "session")
    with TestClient(create_app(product.settings), base_url="https://testserver") as reopened:
        reopened.cookies.update(saved_cookies)
        assert reopened.get("/api/provider").json() == {"configured": False}


def test_session_expiry_revokes_cookie_and_volatile_key(product):
    from datetime import timedelta

    from autoqa.product_models import SessionRow
    from autoqa.store_base import now
    register(product)
    provider(product, "session")
    raw = product.cookies.get("autoqa_session")
    with Session(product.application.state.store.engine) as session:
        session.get(SessionRow, token_hash(raw)).expires_at = now() - timedelta(seconds=1)
        session.commit()
    assert product.get("/api/provider").status_code == 401
    assert product.application.state.gateway.session_configs == {}


@pytest.mark.parametrize("master", ["", "weak-password", base64.urlsafe_b64encode(b"x" * 16).decode()])
def test_production_requires_strong_encryption_master_key(master):
    with pytest.raises(ValueError, match="AUTOQA_SECRET_KEY"):
        Settings(_env_file=None, mode="production", secret_key=master)
