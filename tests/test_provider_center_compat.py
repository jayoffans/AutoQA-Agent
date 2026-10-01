"""BYOK migration and multi-provider contracts on both CI database backends."""
from uuid import uuid4

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from autoqa.crypto import SecretBox
from autoqa.gateway import ProviderGateway
from autoqa.migrations import upgrade
from autoqa.product_models import ProviderConfigRow
from tests.production_support import register
from tests.test_production_compatibility import cli_migrate
from tests.test_production_compatibility import compat as compat  # noqa: F401 - SQLite/MySQL fixture
from tests.test_production_compatibility import compat_settings as compat_settings  # noqa: F401

KEY = "sk-disposable-byok-fixture-5678"
URL = "/api/settings/providers"


def test_provider_migration_preserves_legacy_ciphertext_and_schema(compat_settings):
    settings = compat_settings
    cli_migrate(settings, "0004_onboarding")
    engine = create_engine(settings.database_url, hide_parameters=True)
    workspace = str(uuid4())
    box = SecretBox(settings.secret_key)
    ciphertext = box.encrypt(KEY, "provider:" + workspace)
    try:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO workspaces (id,name,created_at,updated_at) "
                "VALUES (:id,'旧工作区',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"), {"id": workspace})
            connection.execute(text("INSERT INTO provider_configs "
                "(workspace_id,provider,base_url,model,encrypted_api_key,key_mask) "
                "VALUES (:workspace,'deepseek','https://api.deepseek.com','deepseek-chat',:cipher,:mask)"),
                {"workspace": workspace, "cipher": ciphertext, "mask": "sk-••••••••5678"})
        upgrade(engine)
        upgrade(engine)
        with Session(engine) as session:
            row = session.get(ProviderConfigRow, workspace)
            assert row.encrypted_api_key == ciphertext and row.model_name == "deepseek-chat"
            assert row.is_active and row.active_workspace_id == workspace
            assert row.connection_status == "untested" and row.created_at and row.updated_at
        gateway = ProviderGateway(engine, box, settings)
        config = gateway.config(workspace, "unused")
        assert box.decrypt(config["encrypted_api_key"], config["secret_scope"]) == KEY
        columns = {column["name"] for column in inspect(engine).get_columns("provider_configs")}
        assert {"id", "workspace_id", "provider", "model_name", "base_url", "encrypted_api_key", "api_key_hint",
                "is_active", "created_at", "updated_at"} <= columns
        assert "api_key" not in columns
        assert inspect(engine).get_pk_constraint("provider_configs")["constrained_columns"] == ["id"]
    finally:
        engine.dispose()


def test_provider_center_database_parity_create_test_switch_and_delete(compat):
    client, services, _ = compat
    signed, _, _ = register(client)
    data = {"provider": "compatible", "model": "fixture-model", "api_key": KEY, "base_url": services.url + "/v1"}
    a = client.post(URL, json=data)
    b = client.post(URL, json={**data, "model": "second-model"})
    assert a.status_code == b.status_code == 201
    a, b = a.json(), b.json()
    assert sum(row["is_active"] for row in client.get(URL).json()) == 1
    assert client.post(f'{URL}/{a["id"]}/test').json() == {"status": "success", "model": "fixture-model"}
    assert services.calls == ["/v1/chat/completions"]
    assert client.post(f'{URL}/{a["id"]}/activate').json()["is_active"]
    with Session(client.app.state.store.engine) as session:
        rows = list(session.scalars(select(ProviderConfigRow).where(
            ProviderConfigRow.workspace_id == signed["onboarding"]["workspace_id"])))
        assert len(rows) == 2 and all(KEY not in row.encrypted_api_key for row in rows)
        assert sum(row.is_active for row in rows) == 1
    assert client.delete(f'{URL}/{a["id"]}').status_code == 204
    assert client.get(URL).json()[0]["id"] == b["id"]
    assert client.delete(f'{URL}/{b["id"]}').status_code == 204
    assert client.get(URL).json() == [] and client.get("/api/tasks").json() == []
