import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from autoqa.app import create_app
from autoqa.config import Settings
from autoqa.providers import MockProvider, OpenAIProvider, ProviderError
from autoqa.schemas import APIPlan, ProjectCreate, TaskCreate
from autoqa.store import EventRow, StepRow, Store, TaskRow
from autoqa.studio_models import CaseRow, PlanRow, ProjectRow, ReportRow, RunRow, SchemaVersionRow


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'studio.db'}",
                    data_dir=tmp_path / "data", artifact_dir=tmp_path / "artifacts", llm_provider="mock")


@pytest.fixture
def store(settings):
    value = Store(settings.database_url, settings.artifact_dir)
    yield value
    value.engine.dispose()


def completed_task(store, project_id=None):
    request = TaskCreate(target_url="http://127.0.0.1:8765/demo/form.html", goal="Check the form title",
                         project_id=project_id, test_type="smoke")
    task_id = store.create(request)
    steps = [{"action": "assert_text", "description": "Check title", "value": "Form"}]
    store.event(task_id, "planner_started", {"node": "Test Planner"})
    store.update(task_id, status="running", plan={"summary": request.goal, "steps": steps}, planner_mode="structured")
    store.event(task_id, "plan_created", {"plan": {"steps": steps}})
    store.step(task_id, 0, 0, {"step": steps[0], "ok": True, "duration_ms": 2, "error": None})
    store.update(task_id, status="passed")
    store.event(task_id, "completed", {"status": "passed"})
    return task_id


def test_project_crud_persists_across_application_restart(settings):
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/projects", json={"name": "Portal", "url": "https://staging.example"})
        assert response.status_code == 201
        project_id = response.json()["id"]
        assert client.patch(f"/api/projects/{project_id}", json={"description": "Shared QA"}).status_code == 200
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/projects/{project_id}").json()["description"] == "Shared QA"
        assert client.delete(f"/api/projects/{project_id}").status_code == 204
        assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_project_validation_and_builtin_guard(settings):
    with TestClient(create_app(settings)) as client:
        for value in [{"name": " ", "url": "https://staging.example"},
                      {"name": "Bad", "url": "file:///etc/passwd"},
                      {"name": "Bad", "url": "https://user:secret@example.com"}]:
            assert client.post("/api/projects", json=value).status_code == 422
        builtin = client.get("/api/projects").json()[0]["id"]
        assert client.delete(f"/api/projects/{builtin}").status_code == 409
        assert client.patch(f"/api/projects/{builtin}", json={"name": "Changed"}).status_code == 409


def test_tasks_cases_runs_and_reports_survive_store_restart(store, settings):
    project = store.create_project(ProjectCreate(name="QA", url="https://staging.example"))
    task_id = completed_task(store, project["id"])
    reopened = Store(settings.database_url, settings.artifact_dir)
    try:
        saved = reopened.get(task_id)
        assert saved["project_id"] == project["id"] and saved["test_type"] == "smoke"
        assert saved["run"]["passed"] == 1 and saved["report"]["summary"]["coverage"] == 100
        assert "Test Report" in reopened.report_content(task_id)
        assert (settings.artifact_dir / task_id / "report.html").is_file()
        with Session(reopened.engine) as session:
            assert session.scalar(select(CaseRow.status).where(CaseRow.task_id == task_id)) == "passed"
    finally:
        reopened.engine.dispose()


def test_workflow_inputs_outputs_and_timestamps_are_persistent(store, settings):
    task_id = completed_task(store)
    store.event(task_id, "step_started", {"index": 0, "step": {"password": "canary"}})
    store.event(task_id, "step_finished", {"index": 0, "ok": True, "duration_ms": 4})
    reopened = Store(settings.database_url)
    rows = reopened.get(task_id)["workflow_steps"]
    step = next(row for row in rows if row["step_name"].startswith("Case/Step"))
    assert step["status"] == "Completed" and step["started_at"] and step["finished_at"]
    assert "canary" not in json.dumps(rows)
    reopened.engine.dispose()


def test_archiving_project_keeps_tasks_and_report(store):
    project = store.create_project(ProjectCreate(name="Archive me", url="https://staging.example"))
    task_id = completed_task(store, project["id"])
    store.change_project(project["id"], {"archived": True})
    assert store.project(project["id"]) is None
    assert store.get(task_id)["project_name"] == "Archive me"
    assert store.report_content(task_id)
    with pytest.raises(ValueError):
        store.create(TaskCreate(project_id=project["id"], target_url="https://staging.example", goal="Check title"))
    assert store.dashboard()["tasks"] == 1


def test_dashboard_counts_all_history_not_only_recent_list(store):
    for _ in range(51):
        store.create(TaskCreate(target_url="http://127.0.0.1:8765/demo/form.html", goal="Check title"))
    assert len(store.list()) == 50
    assert store.dashboard()["tasks"] == 51
    assert store.dashboard()["running"] == 51


def test_legacy_database_backfill_is_additive_and_idempotent(settings):
    engine = create_engine(settings.database_url)
    for table in [TaskRow.__table__, StepRow.__table__, EventRow.__table__]:
        table.create(engine)
    task_id = "d74b1f32-91d4-446e-93c9-7b93b54680d4"
    with Session(engine) as session:
        session.add(TaskRow(id=task_id, status="passed", request={"goal": "Legacy task",
            "target_url": "http://127.0.0.1:8765/demo/form.html"}, plan={"summary": "Legacy", "steps": []}))
        session.commit()
    engine.dispose()
    for _ in range(2):
        migrated = Store(settings.database_url)
        assert migrated.get(task_id)["request"]["goal"] == "Legacy task"
        with Session(migrated.engine) as session:
            assert session.scalar(select(func.count()).select_from(ProjectRow)) == 2
            assert session.scalar(select(func.count()).select_from(RunRow)) == 1
            assert session.scalar(select(func.count()).select_from(ReportRow)) == 1
            assert session.get(SchemaVersionRow, 2)
        migrated.engine.dispose()


def test_restart_marks_running_tasks_interrupted_with_report(store):
    task_id = store.create(TaskCreate(target_url="http://127.0.0.1:8765/demo/form.html", goal="Check title"))
    store.update(task_id, status="running")
    store.event(task_id, "planner_started", {})
    store.recover()
    saved = store.get(task_id)
    assert saved["status"] == saved["run"]["status"] == "interrupted"
    assert saved["report"] and saved["findings"] == []
    assert any(step["status"] == "Failed" for step in saved["workflow_steps"])


def test_project_workflow_and_dashboard_require_auth(settings):
    protected = settings.model_copy(update={"api_token": "test-token"})
    with TestClient(create_app(protected)) as client:
        for path in ["/api/projects", "/api/dashboard"]:
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"Authorization": "Bearer test-token"}).status_code == 200


def test_generic_environment_aliases_and_secret_repr(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./temporary.db")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LLM_MODEL", "selected-model")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "canary-secret")
    value = Settings(_env_file=None)
    assert value.database_url.endswith("temporary.db") and value.model_enabled
    assert value.model_name == "selected-model" and "canary-secret" not in repr(value)


def test_mysql_connection_url_encodes_password_without_connecting():
    value = Settings(_env_file=None, database_url="", mysql_host="mysql", mysql_user="autoqa",
                     mysql_password="a@b/c#d", mysql_database="studio")
    from sqlalchemy.engine import make_url
    assert make_url(value.database_url).password == "a@b/c#d"
    assert "a@b/c#d" not in repr(value)


async def test_provider_failure_does_not_leak_credentials_or_fallback(monkeypatch):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": "canary-secret"}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    value = Settings(_env_file=None, llm_provider="openai", openai_api_key="canary-secret", provider_model="test")
    with pytest.raises(ProviderError, match="HTTP 401") as error:
        await OpenAIProvider(value).generate("Return JSON", {}, {})
    assert "canary-secret" not in str(error.value)


async def test_mock_plan_is_valid_and_does_not_accept_invented_expectations():
    data = await MockProvider().generate("", {}, APIPlan.model_json_schema())
    plan = APIPlan.model_validate(data)
    assert len(plan.cases) == 6
    data["cases"][1]["expected_status"] = 200
    with pytest.raises(ValueError):
        APIPlan.model_validate(data)


def test_api_plan_resume_after_restart_executes_real_endpoint_contract(settings, monkeypatch):
    app = create_app(settings)
    with TestClient(app) as client:
        task_id = client.post("/api/tasks", json={"target_url": "http://127.0.0.1:8765/demo/api/login",
            "goal": "Validate login API contract", "test_type": "api"}).json()["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            saved = client.get(f"/api/tasks/{task_id}").json()
            if saved["status"] == "awaiting_approval":
                break
            time.sleep(0.02)
        assert saved["status"] == "awaiting_approval"
    app = create_app(settings)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.ASGITransport(app=app), **kwargs))
    with TestClient(app) as client:
        assert client.post(f"/api/tasks/{task_id}/approve", json={"approved": True}).status_code == 202
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            saved = client.get(f"/api/tasks/{task_id}").json()
            if saved["status"] in {"passed", "failed"}:
                break
            time.sleep(0.02)
        assert saved["status"] == "failed" and len(saved["steps"]) == 6
        assert saved["run"]["passed"] == 5 and saved["run"]["failed"] == 1
        assert saved["run"]["coverage"] == 100 and saved["findings"][0]["severity"] == "High"
        assert saved["findings"][0]["evidence"]["steps"][0]["evidence"]["received_status"] == 200
        assert "autoqa123" not in json.dumps(saved)
        assert client.get(f"/api/tasks/{task_id}/report").status_code == 200
        with Session(app.state.store.engine) as session:
            assert session.scalar(select(PlanRow.status).where(PlanRow.task_id == task_id)) == "approved"


def test_production_requires_auth_and_port_is_not_hardcoded():
    with pytest.raises(ValueError, match="AUTOQA_API_TOKEN"):
        Settings(_env_file=None, env="production")
    value = Settings(_env_file=None, env="production", api_token="test-token", port=9000)
    assert value.studio_url == "http://127.0.0.1:9000"
