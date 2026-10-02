"""Additive migrations and import contracts on SQLite and disposable CI MySQL."""
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autoqa.migrations import upgrade
from autoqa.product_models import ApiEndpointRow, ApiSpecRow, WorkspaceRow
from autoqa.studio_models import ProjectRow
from tests.production_support import register
from tests.test_api_import import SPEC, create_project
from tests.test_production_compatibility import cli_migrate
from tests.test_production_compatibility import compat as compat  # noqa: F401
from tests.test_production_compatibility import compat_settings as compat_settings  # noqa: F401


def test_api_import_migration_preserves_existing_project_and_constraints(compat_settings):
    cli_migrate(compat_settings, "0005_provider_center")
    engine = create_engine(compat_settings.database_url, hide_parameters=True)
    workspace, project_id = str(uuid4()), str(uuid4())
    try:
        with Session(engine) as session:
            session.add(WorkspaceRow(id=workspace, name="已有工作区"))
            session.flush()
            session.add(ProjectRow(id=project_id, workspace_id=workspace, name="已有项目", url="", project_type="api"))
            session.commit()
        upgrade(engine)
        upgrade(engine)
        with Session(engine) as session:
            assert session.get(ProjectRow, project_id).name == "已有项目"
            assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0009_execution_assertions"
        inspector = inspect(engine)
        for table in ("api_specs", "api_endpoints"):
            assert not next(column for column in inspector.get_columns(table) if column["name"] == "workspace_id")["nullable"]
            assert {fk["referred_table"] for fk in inspector.get_foreign_keys(table)} >= {"workspaces", "projects"}
        fk = next(fk for fk in inspector.get_foreign_keys("api_endpoints") if fk["referred_table"] == "api_specs")
        assert fk["constrained_columns"] == ["spec_id", "project_id", "workspace_id"]
        assert fk["options"]["ondelete"].upper() == "CASCADE"
        if engine.dialect.name == "mysql":
            raw_type = next(column for column in inspector.get_columns("api_specs") if column["name"] == "raw_content")["type"]
            assert str(raw_type) == "LONGTEXT"
    finally:
        engine.dispose()


def test_api_import_database_parity_upload_scope_cascade_and_large_content(compat):
    client, services, _ = compat
    register(client)
    item = create_project(client)
    prefix = f'/api/projects/{item["id"]}'
    raw = json.dumps({**SPEC, "x-description": "跨数据库文档" * 12_000}, ensure_ascii=False)
    response = client.post(prefix + "/api-specs/upload", files={"file": ("spec.json", raw)})
    assert response.status_code == 201, response.text
    info = response.json()["spec"]
    assert len(client.get(prefix + "/api-endpoints").json()) == 2
    with Session(client.app.state.store.engine) as session:
        assert session.get(ApiSpecRow, info["id"]).raw_content == raw
    with TestClient(client.app, base_url="https://testserver") as other:
        register(other)
        workspace = other.get("/api/auth/me").json()["workspace_id"]
        assert other.get(prefix + "/api-endpoints").status_code == 404
        with Session(client.app.state.store.engine) as session:
            session.add(ApiEndpointRow(workspace_id=workspace, project_id=item["id"], spec_id=info["id"],
                method="GET", path="/forged", summary="", parameters=[], request_schema={}, response_schema={}))
            with pytest.raises(IntegrityError):
                session.commit()
    assert client.delete(prefix + '/api-specs/' + info["id"]).status_code == 204
    with Session(client.app.state.store.engine) as session:
        assert session.scalar(select(ApiEndpointRow)) is None
        assert session.scalar(select(ApiSpecRow)) is None
    assert services.calls == []
