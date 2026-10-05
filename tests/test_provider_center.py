"""BYOK acceptance: disposable keys, fake HTTP, and no task execution."""
import asyncio
import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autoqa.app import create_app
from autoqa.crypto import SecretBox
from autoqa.product_models import ProviderConfigRow
from tests.test_auth import bearer, sign_up
from tests.test_product import KEY, SECRET
from tests.test_product import product as product  # noqa: F401 - disposable production fixture

URL = "/api/settings/providers"
SAFE_ERROR = "连接失败，请检查服务地址、密钥、模型和余额"


@pytest.fixture
def center(product):
    product.identity = sign_up(product)
    product.identity["workspace_id"] = product.get("/api/auth/me").json()["workspace_id"]
    return product


def create(client, **changes):
    data = {"provider": "compatible", "model": "fixture-model", "base_url": "https://model.example/v1",
            "api_key": KEY, **changes}
    response = client.post(URL, json=data)
    assert response.status_code == 201, response.text
    return response.json()


def test_provider_key_encrypted(center):
    item = create(center)
    with Session(center.application.state.store.engine) as session:
        row = session.get(ProviderConfigRow, item["id"])
        assert row.encrypted_api_key.startswith("v1:") and KEY not in row.encrypted_api_key
        assert SecretBox(SECRET).decrypt(row.encrypted_api_key,
            "provider:" + center.identity["workspace_id"] + ":" + item["id"]) == KEY
        assert row.created_at and row.updated_at and row.model_name == item["model"]
    assert KEY.encode() not in (center.settings.data_dir.parent / "production.sqlite").read_bytes()


def test_provider_api_not_return_plain_key(center):
    item = create(center)
    for response in [center.get(URL), center.get("/api/provider"),
                     center.post(f'{URL}/{item["id"]}/test'), center.post(f'{URL}/{item["id"]}/activate')]:
        assert response.is_success and KEY not in response.text and "encrypted_api_key" not in response.text
        assert '"api_key"' not in response.text


@pytest.mark.parametrize("key,mask", [(KEY, "sk-********1234"), ("12345678", "sk-************"),
                                      ("opaque-fixture-token-abcd", "sk-********abcd")])
def test_provider_key_mask(center, key, mask):
    item = create(center, api_key=key)
    assert item["masked_key"] == mask and key not in json.dumps(item)
    assert center.get(URL).json()[0]["masked_key"] == mask


def test_workspace_provider_isolation(center):
    a = create(center, model="workspace-a-model")
    b_identity = sign_up(center, "provider-b@example.com")
    b_client = bearer(center, b_identity["access_token"])
    assert b_client.get(URL).json() == []
    b = create(b_client, model="workspace-b-model")
    a_client = bearer(center, center.identity["access_token"])
    assert [row["id"] for row in a_client.get(URL).json()] == [a["id"]]
    assert [row["id"] for row in b_client.get(URL).json()] == [b["id"]]
    for method, suffix in [("DELETE", ""), ("POST", "/test"), ("POST", "/activate")]:
        response = b_client.request(method, f'{URL}/{a["id"]}{suffix}')
        assert response.status_code == 404 and "workspace-a-model" not in response.text
        assert a_client.request(method, f'{URL}/{b["id"]}{suffix}').status_code == 404
    assert b_client.get(URL, headers={"X-Workspace-ID": center.identity["workspace_id"]}).status_code == 404
    assert center.calls == []
    with Session(center.application.state.store.engine) as session:
        assert session.scalar(select(ProviderConfigRow.id).where(
            ProviderConfigRow.id == a["id"],
            ProviderConfigRow.workspace_id == b_identity["onboarding"]["workspace_id"])) is None


@pytest.mark.parametrize("changes", [
    {"provider": "mock"}, {"provider": "unknown"}, {"model": ""}, {"model": "   "},
    {"model": "x" * 161}, {"api_key": "short"}, {"api_key": "        "}, {"api_key": "   abc   "},
    {"api_key": "key\nfixture-secret"}, {"save_mode": "disk"}, {"workspace_id": "foreign"},
    {"model": KEY}, {"base_url": "https://model.example/" + KEY},
])
def test_invalid_provider_config(center, changes):
    response = center.post(URL, json={"provider": "compatible", "model": "fixture-model",
        "base_url": "https://model.example/v1", "api_key": KEY, **changes})
    assert response.status_code == 422 and KEY not in response.text
    assert center.get(URL).json() == [] and not center.get("/api/onboarding").json()["model_configured"]


@pytest.mark.parametrize("url", ["", "http://model.example/v1", "http://127.0.0.1", "file:///secret",
    "https://user:password@model.example/v1", "https://model.example/v1?key=fixture",
    "https://model.example/v1#fragment", "https://169.254.169.254", "https://model.example:22"])
def test_provider_address_rejected_without_secret_echo(center, url):
    response = center.post(URL, json={"provider": "compatible", "model": "fixture", "api_key": KEY,
                                     "base_url": url})
    assert response.status_code == 422 and KEY not in response.text and "password" not in response.text


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 302])
def test_connection_failure_safe_response(center, caplog, status):
    item = create(center)
    caplog.set_level(logging.DEBUG)
    center.application.state.gateway.transport_factory = lambda: httpx.MockTransport(lambda request: httpx.Response(
        status, headers={"location": "https://other.example/" + KEY}, json={"error": KEY, "stack": "Traceback"}))
    response = center.post(f'{URL}/{item["id"]}/test')
    assert response.status_code == 502 and response.json() == {"detail": SAFE_ERROR}
    assert KEY not in response.text + caplog.text and "Traceback" not in response.text
    assert center.get(URL).json()[0]["status"] == "failed"


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout, RuntimeError])
def test_connection_exception_is_safe(center, caplog, error_type):
    item = create(center)
    caplog.set_level(logging.DEBUG)
    def fail(request):
        raise error_type("Traceback " + KEY)
    center.application.state.gateway.transport_factory = lambda: httpx.MockTransport(fail)
    response = center.post(f'{URL}/{item["id"]}/test')
    assert response.status_code == 502 and response.json() == {"detail": SAFE_ERROR}
    assert KEY not in caplog.text + response.text


@pytest.mark.parametrize("provider", ["deepseek", "openai", "compatible"])
def test_connection_minimal_request_uses_workspace_key(center, provider):
    item = create(center, provider=provider)
    captured = []
    def handle(request):
        assert request.headers["Authorization"] == "Bearer " + KEY
        captured.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"model": "untrusted-provider-model", "choices": [
            {"message": {"content": KEY}}]})
    center.application.state.gateway.transport_factory = lambda: httpx.MockTransport(handle)
    response = center.post(f'{URL}/{item["id"]}/test')
    assert response.json() == {"status": "success", "model": "fixture-model"}
    assert captured == [("https://model.example/v1/chat/completions", {"model": "fixture-model", "max_tokens": 1,
                         "messages": [{"role": "user", "content": "Hi"}]})]
    assert center.get(URL).json()[0]["status"] == "connected"
    assert center.get("/api/projects").json() == center.get("/api/tasks").json() == []
    assert not center.get("/api/onboarding").json()["first_test_completed"]


@pytest.mark.parametrize("body", [{}, {"choices": []}, {"choices": [{"message": {"content": 7}}]}])
def test_connection_invalid_response_is_safe(center, body):
    item = create(center)
    center.application.state.gateway.transport_factory = lambda: httpx.MockTransport(
        lambda request: httpx.Response(200, json=body))
    response = center.post(f'{URL}/{item["id"]}/test')
    assert response.status_code == 502 and response.json() == {"detail": SAFE_ERROR}


def test_connection_response_size_is_limited(center):
    item = create(center)
    center.application.state.gateway.transport_factory = lambda: httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"x" * 65537))
    assert center.post(f'{URL}/{item["id"]}/test').status_code == 502


def test_connection_total_deadline_returns_safe_error(center, monkeypatch):
    item = create(center)
    monkeypatch.setattr("autoqa.gateway.CONNECTION_TIMEOUT", 0.02)
    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Hi"}}]})
    center.application.state.gateway.transport_factory = lambda: httpx.MockTransport(slow)
    response = center.post(f'{URL}/{item["id"]}/test')
    assert response.status_code == 502 and response.json() == {"detail": SAFE_ERROR}


@pytest.mark.parametrize("path", ["/api/provider", "/api/provider/test"])
def test_legacy_provider_api_rejects_keys_in_public_metadata(center, path):
    method = "PUT" if path == "/api/provider" else "POST"
    response = center.request(method, path, json={"provider": "compatible", "model": KEY,
        "base_url": "https://model.example/v1", "api_key": KEY})
    assert response.status_code == 422 and KEY not in response.text and center.calls == []


@pytest.mark.parametrize("provider,url", [("deepseek", "https://api.deepseek.com"),
                                        ("openai", "https://api.openai.com/v1")])
def test_provider_defaults(center, monkeypatch, provider, url):
    monkeypatch.setattr("autoqa.product_app.resolve_target", lambda *args: "93.184.216.34")
    item = create(center, provider=provider, base_url="")
    assert item["base_url"] == url and center.calls == []


def test_multiple_configs_one_active_and_gateway_selection(center):
    a, b = create(center, model="first"), create(center, model="second")
    items = center.get(URL).json()
    assert len(items) == 2 and sum(row["is_active"] for row in items) == 1
    assert center.get("/api/provider").json()["model"] == "second"
    response = center.post(f'{URL}/{a["id"]}/activate')
    assert response.json()["is_active"]
    assert center.get("/api/provider").json()["model"] == "first"
    assert {row["id"] for row in items} == {a["id"], b["id"]}


def test_database_rejects_two_active_providers(center):
    item = create(center)
    with Session(center.application.state.store.engine) as session:
        row = session.get(ProviderConfigRow, item["id"])
        session.add(ProviderConfigRow(id=str(uuid4()), workspace_id=row.workspace_id, provider=row.provider,
            model_name="second", base_url=row.base_url, encrypted_api_key=row.encrypted_api_key,
            api_key_hint=row.api_key_hint, is_active=True, active_workspace_id=row.workspace_id))
        with pytest.raises(IntegrityError):
            session.commit()


def test_database_rejects_active_flag_without_workspace_slot(center):
    item = create(center)
    with Session(center.application.state.store.engine) as session:
        session.get(ProviderConfigRow, item["id"]).active_workspace_id = None
        with pytest.raises(IntegrityError):
            session.commit()


def test_concurrent_provider_creates_preserve_all_and_single_active(center):
    def save(index):
        client = bearer(center, center.identity["access_token"])
        return create(client, model=f"concurrent-{index}")
    with ThreadPoolExecutor(max_workers=4) as executor:
        items = list(executor.map(save, range(6)))
    rows = center.get(URL).json()
    assert len(rows) == len(items) == 6 and sum(row["is_active"] for row in rows) == 1


def test_delete_active_promotes_remaining_and_last_clears_milestone(center):
    a, b = create(center, model="first"), create(center, model="second")
    assert center.delete(f'{URL}/{b["id"]}').status_code == 204
    assert center.get("/api/provider").json()["model"] == "first"
    assert center.get(URL).json()[0]["is_active"]
    assert center.delete(f'{URL}/{a["id"]}').status_code == 204
    assert center.get(URL).json() == [] and center.get("/api/provider").json() == {"configured": False}
    assert not center.get("/api/onboarding").json()["model_configured"]


def test_delete_inactive_preserves_default(center):
    a, b = create(center), create(center, model="second")
    assert center.delete(f'{URL}/{a["id"]}').status_code == 204
    assert center.get(URL).json()[0]["id"] == b["id"]


def test_provider_onboarding_milestone_and_next_step(center):
    assert center.patch("/api/onboarding", json={"action": "start"}).json()["step"] == 2
    create(center)
    state = center.get("/api/onboarding").json()
    assert state["model_configured"] and state["step"] == 2 and center.calls == []
    assert center.patch("/api/onboarding", json={"action": "skip_model"}).json()["step"] == 3


def test_session_config_encrypted_isolated_and_not_persisted(center):
    item = create(center, save_mode="session")
    assert item["id"] == "session" and item["save_mode"] == "session"
    assert center.get(URL).json()[0]["id"] == "session"
    assert center.post(URL + "/session/test").json()["status"] == "success"
    assert center.get(URL).json()[0]["status"] == "connected"
    assert KEY not in json.dumps(list(center.application.state.gateway.session_configs.values()))
    with Session(center.application.state.store.engine) as session:
        assert list(session.scalars(select(ProviderConfigRow))) == []
    assert center.delete(URL + "/session").status_code == 204
    assert center.get(URL).json() == [] and not center.get("/api/onboarding").json()["model_configured"]


def test_member_reads_masks_but_cannot_mutate_or_spend(center):
    item = create(center)
    member = sign_up(center, "member-provider@example.com")
    owner = bearer(center, center.identity["access_token"])
    assert owner.post("/api/workspace/members", json={"email": member["user"]["email"]}).status_code == 201
    client = bearer(center, member["access_token"])
    client.headers["X-Workspace-ID"] = center.identity["workspace_id"]
    assert client.get(URL).json()[0]["masked_key"] == "sk-********1234"
    for method, path in [("POST", URL), ("DELETE", f'{URL}/{item["id"]}'),
                         ("POST", f'{URL}/{item["id"]}/test'), ("POST", f'{URL}/{item["id"]}/activate')]:
        response = client.request(method, path, json={"provider": "openai", "api_key": KEY,
            "base_url": "https://model.example", "model": "fixture"} if path == URL else None)
        assert response.status_code == 403
    assert center.calls == []


def test_provider_endpoints_require_login_and_cookie_csrf(product):
    for method, path in [("GET", URL), ("POST", URL), ("DELETE", URL + "/unknown"),
                         ("POST", URL + "/unknown/test"), ("POST", URL + "/unknown/activate")]:
        assert product.request(method, path).status_code == 401
    sign_up(product)
    product.headers.pop("X-CSRF-Token")
    assert product.post(URL, json={"provider": "openai", "model": "fixture", "api_key": KEY}).status_code == 403


def test_unknown_config_test_does_not_call_provider(center):
    for method, suffix in [("DELETE", ""), ("POST", "/test"), ("POST", "/activate")]:
        assert center.request(method, URL + "/unknown" + suffix).status_code == 404
    assert center.calls == []


def test_provider_envelope_cannot_move_between_records_or_workspaces(center):
    a, b = create(center), create(center)
    with Session(center.application.state.store.engine) as session:
        row = session.get(ProviderConfigRow, a["id"])
        envelope = row.encrypted_api_key
        for scope in ["provider:foreign:" + a["id"], "provider:" + row.workspace_id + ":" + b["id"]]:
            with pytest.raises(InvalidTag):
                SecretBox(SECRET).decrypt(envelope, scope)
        session.get(ProviderConfigRow, b["id"]).encrypted_api_key = envelope
        session.commit()
    assert center.post(f'{URL}/{b["id"]}/test').status_code == 502 and center.calls == []


def test_ciphertext_nonce_random_and_master_key_requires_256_bits(center):
    a, b = create(center), create(center)
    with Session(center.application.state.store.engine) as session:
        assert session.get(ProviderConfigRow, a["id"]).encrypted_api_key != session.get(
            ProviderConfigRow, b["id"]).encrypted_api_key
    for length in [16, 24]:
        with pytest.raises(ValueError):
            SecretBox(base64.urlsafe_b64encode(b"a" * length).decode())


def test_create_failure_rolls_back_default_and_onboarding(center):
    a = create(center)
    engine = center.application.state.store.engine
    def fail(connection, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO provider_configs"):
            raise RuntimeError("fixture-insert-error " + KEY)
    event.listen(engine, "before_cursor_execute", fail)
    try:
        response = center.post(URL, json={"provider": "compatible", "model": "other",
            "base_url": "https://model.example/v1", "api_key": KEY})
        assert response.status_code == 500 and KEY not in response.text
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert center.get(URL).json()[0]["id"] == a["id"] and center.get(URL).json()[0]["is_active"]


def test_provider_config_and_test_status_survive_restart(center):
    a, b = create(center), create(center, model="second")
    assert center.post(f'{URL}/{b["id"]}/test').status_code == 200
    with TestClient(create_app(center.settings), base_url="https://testserver") as reopened:
        reopened.headers["Authorization"] = "Bearer " + center.identity["access_token"]
        rows = reopened.get(URL).json()
        assert {row["id"] for row in rows} == {a["id"], b["id"]}
        assert rows[0]["status"] == "connected" and rows[0]["is_active"]


@pytest.mark.parametrize("fails", [False, True])
def test_gateway_releases_decrypted_provider_references(center, monkeypatch, fails):
    create(center)
    instances = []
    class FakeProvider:
        def __init__(self, settings, transport):
            self.settings, self.key = settings, settings.provider_key
            instances.append(self)
        async def generate(self, *args):
            assert self.key == KEY
            if fails:
                raise ValueError("fixture-generate-failure")
            return {"echo": self.key}
    monkeypatch.setattr("autoqa.gateway.CompatibleProvider", FakeProvider)
    run = center.application.state.gateway.generate(center.identity["workspace_id"], "unused", "fixture", {}, {})
    if fails:
        with pytest.raises(ValueError):
            asyncio.run(run)
    else:
        assert asyncio.run(run) == {"echo": "[REDACTED]"}
    assert instances[0].key == instances[0].settings.provider_key == ""
