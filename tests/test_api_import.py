"""OpenAPI import foundation: real DB/API boundaries, no model or target calls."""
import copy
import json
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autoqa.app import create_app
from autoqa.product_models import ApiEndpointRow, ApiSpecRow
from tests.test_auth import sign_up
from tests.test_product import product as product  # noqa: F401

SPEC = {
    "openapi": "3.0.3", "info": {"title": "用户接口", "version": "1.2.0"},
    "paths": {
        "/login": {"post": {"summary": "登录", "requestBody": {"required": True, "content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/Login"}}}},
            "responses": {"200": {"description": "登录成功", "content": {
                "application/json": {"schema": {"type": "object", "properties": {"token": {"type": "string"}}}}}},
                "401": {"description": "登录失败"}}}},
        "/users/{id}": {"parameters": [{"name": "id", "in": "path", "required": True,
            "schema": {"type": "integer"}}, {"name": "limit", "in": "query", "schema": {"type": "integer"}}],
            "get": {"summary": "查询用户", "parameters": [{"name": "limit", "in": "query", "description": "覆盖参数",
                "schema": {"type": "integer", "maximum": 20}}, {"$ref": "#/components/parameters/Token"}],
                "responses": {"200": {"$ref": "#/components/responses/User"}}}},
    },
    "components": {
        "schemas": {"Login": {"type": "object", "required": ["username"],
            "properties": {"username": {"type": "string"}, "password": {"type": "string"}}}},
        "parameters": {"Token": {"name": "x-token", "in": "header", "schema": {"type": "string"}}},
        "responses": {"User": {"description": "用户", "content": {"application/json": {"schema": {"type": "object"}}}}},
    },
}


def create_project(client, **changes):
    response = client.post("/api/projects", json={"name": "API 输入项目", "project_type": "api", **changes})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def imports(product):
    sign_up(product)
    product.item = create_project(product)
    product.prefix = f'/api/projects/{product.item["id"]}'
    product.spec = copy.deepcopy(SPEC)
    return product


def upload(client, spec=None, filename="openapi.json", raw=None, prefix=None):
    if raw is None:
        spec = client.spec if spec is None else spec
        raw = json.dumps(spec, ensure_ascii=False) if filename.endswith("json") else yaml.safe_dump(spec, allow_unicode=True)
    return client.post((prefix or client.prefix) + "/api-specs/upload", files={"file": (filename, raw, "application/octet-stream")})


def counts(client):
    with Session(client.application.state.store.engine) as session:
        return tuple(session.scalar(select(func.count()).select_from(row)) for row in (ApiSpecRow, ApiEndpointRow))


def test_upload_json_persists_spec_and_workspace(imports):
    response = upload(imports)
    assert response.status_code == 201, response.text
    info = response.json()
    assert info["endpoint_count"] == info["spec"]["endpoint_count"] == 2
    assert info["spec"]["name"] == "用户接口" and info["spec"]["version"] == "1.2.0"
    assert info["spec"]["format"] == "json" and "raw_content" not in response.text
    workspace = imports.get("/api/auth/me").json()["workspace_id"]
    with Session(imports.application.state.store.engine) as session:
        row = session.get(ApiSpecRow, info["spec"]["id"])
        assert json.loads(row.raw_content) == imports.spec
        assert row.project_id == imports.item["id"] and row.workspace_id == workspace
        assert row.created_at and row.updated_at
        assert {e.workspace_id for e in session.scalars(select(ApiEndpointRow))} == {workspace}
    assert counts(imports) == (1, 2) and imports.calls == []


@pytest.mark.parametrize("filename", ["openapi.yaml", "openapi.yml", "openapi.YAML"])
def test_upload_yaml_extensions(imports, filename):
    response = upload(imports, filename=filename)
    assert response.status_code == 201, response.text
    assert response.json()["spec"]["format"] == "yaml"
    assert len(imports.get(imports.prefix + "/api-endpoints").json()) == 2


@pytest.mark.parametrize("version", ["3.0.0", "3.1.0", "3.2.0"])
def test_openapi_versions(imports, version):
    imports.spec["openapi"] = version
    assert upload(imports).status_code == 201


def test_swagger2_body_response_and_consumes(imports):
    spec = {"swagger": "2.0", "info": {"title": "Swagger", "version": "2"}, "consumes": ["application/xml"],
        "produces": ["application/json"], "paths": {"/login": {"post": {
            "parameters": [{"name": "body", "in": "body", "required": True,
                "schema": {"$ref": "#/definitions/Login"}}],
            "responses": {"200": {"description": "OK", "schema": {"type": "object"}}, "x-note": "metadata"}}}},
        "definitions": {"Login": {"type": "object"}}}
    assert upload(imports, spec).status_code == 201
    endpoint = imports.get(imports.prefix + "/api-endpoints").json()[0]
    assert endpoint["parameters"][0]["in"] == "body"
    assert endpoint["request_schema"]["required"]
    assert endpoint["request_schema"]["content"]["application/xml"]["schema"] == {"$ref": "#/definitions/Login"}
    assert endpoint["response_schema"]["200"]["content"]["application/json"]["schema"] == {"type": "object"}
    assert "x-note" not in endpoint["response_schema"]


def test_swagger2_form_data(imports):
    spec = {"swagger": "2.0", "info": {"title": "Form", "version": "1"}, "consumes": ["multipart/form-data"],
        "paths": {"/upload": {"post": {"parameters": [{"name": "file", "in": "formData", "type": "file", "required": True}],
            "responses": {"200": {"description": "OK"}}}}}}
    assert upload(imports, spec, filename="swagger.yml").status_code == 201
    schema = imports.get(imports.prefix + "/api-endpoints").json()[0]["request_schema"]["content"]["multipart/form-data"]["schema"]
    assert schema["properties"]["file"]["type"] == "file" and schema["required"] == ["file"]


def test_endpoint_parsing_parameters_override_and_local_refs(imports):
    assert upload(imports).status_code == 201
    endpoints = imports.get(imports.prefix + "/api-endpoints").json()
    assert [(e["method"], e["path"], e["summary"]) for e in endpoints] == [
        ("POST", "/login", "登录"), ("GET", "/users/{id}", "查询用户")]
    parameters = endpoints[1]["parameters"]
    assert len(parameters) == 3
    assert next(p for p in parameters if p["name"] == "limit")["schema"]["maximum"] == 20
    assert next(p for p in parameters if p["name"] == "x-token")["in"] == "header"
    assert endpoints[1]["response_schema"]["200"]["description"] == "用户"
    assert endpoints[0]["request_schema"]["content"]["application/json"]["schema"]["$ref"].startswith("#/components/")


def test_path_item_request_body_refs_and_method_filter(imports):
    imports.spec["components"]["requestBodies"] = {"Login": imports.spec["paths"]["/login"]["post"]["requestBody"]}
    imports.spec["paths"]["/login"]["post"]["requestBody"] = {"$ref": "#/components/requestBodies/Login"}
    imports.spec["paths"]["/copy"] = {"$ref": "#/paths/~1login"}
    imports.spec["paths"]["/login"]["x-extra"] = {"summary": "非 HTTP 方法"}
    imports.spec["paths"]["/login"]["trace"] = {"responses": {"200": {"description": "OK"}}}
    assert upload(imports).status_code == 201
    endpoints = imports.get(imports.prefix + "/api-endpoints").json()
    assert len(endpoints) == 5
    assert all(e["method"] != "X-EXTRA" for e in endpoints)
    assert next(e for e in endpoints if e["path"] == "/copy" and e["method"] == "POST")["request_schema"]["required"]


@pytest.mark.parametrize("change", ["version", "info", "responses", "parameter", "missing_ref", "no_operations"])
def test_invalid_spec_rolls_back(imports, change):
    if change == "version":
        imports.spec["openapi"] = "4.0.0"
    elif change == "info":
        imports.spec.pop("info")
    elif change == "responses":
        imports.spec["paths"]["/login"]["post"]["responses"] = {"200": {}}
    elif change == "parameter":
        imports.spec["paths"]["/users/{id}"]["parameters"][0]["required"] = False
    elif change == "missing_ref":
        imports.spec["paths"]["/login"]["post"]["requestBody"] = {"$ref": "#/components/requestBodies/Missing"}
    else:
        imports.spec["paths"] = {}
    response = upload(imports)
    assert response.status_code == 422, response.text
    assert counts(imports) == (0, 0)


@pytest.mark.parametrize("filename,raw", [("bad.json", "{"), ("bad.yml", "paths: ["),
    ("empty.json", ""), ("array.json", "[]"), ("bad.json", b"\xff\x00secret-canary"),
    ("dup.json", '{"openapi":"3.0.3","openapi":"3.1.0"}'), ("dup.yaml", "openapi: 3.0.3\nopenapi: 3.1.0")])
def test_malformed_encoding_and_duplicates(imports, filename, raw):
    response = upload(imports, filename=filename, raw=raw)
    assert response.status_code == 422 and "secret-canary" not in response.text
    assert counts(imports) == (0, 0)


@pytest.mark.parametrize("filename", ["spec.txt", "spec.exe", "spec.json.exe", "spec"])
def test_format_restrictions(imports, filename):
    assert upload(imports, filename=filename).status_code == 415
    assert counts(imports) == (0, 0)


@pytest.mark.parametrize("filename", ["../spec.json", "..\\spec.yaml", "C:\\spec.json", "nested/spec.yml"])
def test_path_traversal_filename_rejected(imports, filename):
    assert upload(imports, filename=filename).status_code == 422
    assert not list(imports.settings.artifact_dir.rglob("*"))
    assert counts(imports) == (0, 0)


def test_size_limits_on_file_and_request(imports):
    assert upload(imports, raw=" " * 1_000_001).status_code == 413
    assert upload(imports, raw=" " * 1_300_000).status_code == 413
    assert counts(imports) == (0, 0)


def test_exact_upload_size_and_utf8_bom_allowed(imports):
    raw = b"\xef\xbb\xbf" + json.dumps(imports.spec, ensure_ascii=False).encode("utf-8")
    raw += b" " * (1_000_000 - len(raw))
    assert upload(imports, raw=raw).status_code == 201
    assert counts(imports) == (1, 2)


def test_endpoint_limit_rejects_large_operation_set(imports):
    imports.spec["paths"] = {f"/users/{index}": {"get": {"responses": {"200": {"description": "OK"}}}}
                             for index in range(1001)}
    response = upload(imports)
    assert response.status_code == 422 and "1000" in response.text
    assert counts(imports) == (0, 0)


@pytest.mark.parametrize("ref", ["https://private.example/secret.json", "file:///etc/passwd", "../other.json", "//127.0.0.1/spec", "#anchor"])
def test_external_refs_rejected_without_io(imports, ref, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Import attempted network/file reference retrieval")
    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    imports.spec["paths"]["/login"]["post"]["requestBody"] = {"$ref": ref}
    assert upload(imports).status_code == 422
    assert imports.calls == [] and counts(imports) == (0, 0)


def test_yaml_content_not_executed_and_aliases_rejected(imports, tmp_path):
    canary = tmp_path / "executed.txt"
    raw = f'!!python/object/apply:builtins.open ["{canary.as_posix()}", "w"]'
    assert upload(imports, filename="unsafe.yaml", raw=raw).status_code == 422
    assert not canary.exists()
    assert upload(imports, filename="alias.yml", raw="info: &a {title: Test, version: '1'}\nx-example: *a").status_code == 422
    assert counts(imports) == (0, 0)


def test_recursive_schema_preserved_without_expansion(imports):
    imports.spec["components"]["schemas"]["Node"] = {"type": "object", "properties": {
        "next": {"$ref": "#/components/schemas/Node"}}}
    assert upload(imports).status_code == 201
    with Session(imports.application.state.store.engine) as session:
        assert "#/components/schemas/Node" in session.scalar(select(ApiSpecRow)).raw_content


def test_complexity_and_resource_ids_rejected(imports):
    imports.spec["x-nested"] = value = {}
    for _ in range(70):
        value["nested"] = {}
        value = value["nested"]
    assert upload(imports).status_code == 422
    imports.spec = copy.deepcopy(SPEC)
    imports.spec["components"]["schemas"]["Login"]["$id"] = "https://private.example/root"
    assert upload(imports).status_code == 422
    assert counts(imports) == (0, 0)


def test_no_model_task_report_or_raw_content_exposure(imports):
    assert upload(imports).status_code == 201
    specs = imports.get(imports.prefix + "/api-specs")
    assert specs.json()[0]["endpoint_count"] == 2 and "raw_content" not in specs.text
    assert imports.get("/api/tasks").json() == [] and imports.get("/api/dashboard").json()["executions"] == 0
    assert imports.calls == [] and not list(imports.settings.artifact_dir.rglob("*"))
    spec_id = specs.json()[0]["id"]
    assert imports.get(imports.prefix + f"/api-specs/{spec_id}/raw").status_code == 404


def test_workspace_isolation_read_upload_delete_and_raw(imports):
    spec_id = upload(imports).json()["spec"]["id"]
    workspace = imports.get("/api/auth/me").json()["workspace_id"]
    with TestClient(imports.application, base_url="https://testserver") as other:
        sign_up(other, "other-import@example.com")
        for suffix in ("/api-specs", "/api-endpoints", f"/api-specs/{spec_id}/raw"):
            response = other.get(imports.prefix + suffix)
            assert response.status_code == 404 and "用户接口" not in response.text
        assert upload(other, SPEC, prefix=imports.prefix).status_code == 404
        assert other.delete(imports.prefix + f"/api-specs/{spec_id}").status_code == 404
        assert other.get(imports.prefix + "/api-endpoints", headers={"X-Workspace-ID": workspace}).status_code == 404
        own_project = create_project(other)
        own_prefix = f'/api/projects/{own_project["id"]}'
        assert other.get(own_prefix + "/api-specs").json() == other.get(own_prefix + "/api-endpoints").json() == []
        assert other.delete(own_prefix + f"/api-specs/{spec_id}").status_code == 404
    assert counts(imports) == (1, 2)


def test_database_rejects_cross_workspace_spec(imports):
    with TestClient(imports.application, base_url="https://testserver") as other:
        sign_up(other, "db-other@example.com")
        workspace = other.get("/api/auth/me").json()["workspace_id"]
    with Session(imports.application.state.store.engine) as session:
        session.add(ApiSpecRow(id=str(uuid4()), project_id=imports.item["id"], workspace_id=workspace,
            name="forged", version="1", format="json", raw_content="{}"))
        with pytest.raises(IntegrityError):
            session.commit()
    assert counts(imports) == (0, 0)


def test_database_rejects_endpoint_spec_project_mismatch(imports):
    info = upload(imports).json()["spec"]
    second = create_project(imports)
    with Session(imports.application.state.store.engine) as session:
        session.add(ApiEndpointRow(project_id=second["id"], workspace_id=info["workspace_id"], spec_id=info["id"],
            method="GET", path="/forged", summary="", parameters=[], request_schema={}, response_schema={}))
        with pytest.raises(IntegrityError):
            session.commit()
    assert counts(imports) == (1, 2)


def test_delete_spec_only_removes_its_endpoints(imports):
    first = upload(imports).json()["spec"]["id"]
    second = upload(imports).json()["spec"]["id"]
    assert counts(imports) == (2, 4)
    assert imports.delete(imports.prefix + f"/api-specs/{first}").status_code == 204
    assert counts(imports) == (1, 2)
    assert {e["spec_id"] for e in imports.get(imports.prefix + "/api-endpoints").json()} == {second}
    assert imports.delete(imports.prefix + f"/api-specs/{first}").status_code == 404
    assert imports.delete(imports.prefix + f"/api-specs/{second}").status_code == 204
    assert imports.get(imports.prefix + "/api-endpoints").json() == []


def test_database_cascades_spec_deletion(imports):
    spec_id = upload(imports).json()["spec"]["id"]
    with Session(imports.application.state.store.engine) as session:
        session.delete(session.get(ApiSpecRow, spec_id))
        session.commit()
    assert counts(imports) == (0, 0)


def test_atomic_import_rolls_back_endpoint_insert_failure(imports):
    engine = imports.application.state.store.engine
    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO api_endpoints"):
            raise RuntimeError("private-import-canary")
    event.listen(engine, "before_cursor_execute", fail)
    try:
        response = upload(imports)
        assert response.status_code == 500 and "private-import-canary" not in response.text
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert counts(imports) == (0, 0)


def test_spec_persists_after_app_reopen(imports):
    spec_id = upload(imports).json()["spec"]["id"]
    with TestClient(create_app(imports.settings), base_url="https://testserver") as reopened:
        reopened.cookies.update(imports.cookies)
        assert reopened.get(imports.prefix + "/api-specs").json()[0]["id"] == spec_id
        assert len(reopened.get(imports.prefix + "/api-endpoints").json()) == 2


def test_import_requires_auth_and_cookie_csrf(imports):
    saved = dict(imports.cookies)
    imports.cookies.clear()
    assert upload(imports).status_code == 401
    imports.cookies.update(saved)
    imports.headers.pop("X-CSRF-Token")
    assert upload(imports).status_code == 403


def test_wrong_project_type_and_archived_project(imports):
    web = create_project(imports, project_type="web", url="https://qa.example")
    assert upload(imports, prefix=f'/api/projects/{web["id"]}').status_code == 422
    assert imports.delete(imports.prefix).status_code == 204
    assert upload(imports).status_code == 404
    assert counts(imports) == (0, 0)
