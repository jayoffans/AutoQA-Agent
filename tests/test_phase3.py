import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import update
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from autoqa.app import create_app
from autoqa.checkpoints import SQLCheckpointSaver
from autoqa.config import Settings
from autoqa.demo_guard import DemoLimitError, DemoQuotaRow
from autoqa.safe_logging import SafeFormatter
from autoqa.schemas import TaskCreate
from autoqa.seed import SEED_PROJECT_ID, SEED_TASK_ID
from autoqa.store import Store
from autoqa.store_base import Base


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, env="production", database_url=f"sqlite:///{tmp_path / 'demo.db'}",
                    data_dir=tmp_path / "data", artifact_dir=tmp_path / "artifacts", port=9187)


def client_for(settings):
    return TestClient(create_app(settings), base_url="https://testserver")


def payload(settings):
    return {"test_type": "api", "target_url": settings.studio_url + "/demo/api/login",
            "goal": "Check login contract"}


def wait(client, task_id, status):
    for _ in range(150):
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] == status:
            return task
        time.sleep(0.02)
    raise AssertionError(task)


def test_public_defaults_and_seed_restart_idempotency(settings):
    assert settings.demo_mode and settings.llm_provider == "mock" and not settings.mcp_enabled
    for _ in range(3):
        with client_for(settings) as client:
            assert client.get("/health").json() == {"status": "ok"}
            assert len(client.get("/api/tasks").json()) == 1
            task = client.get(f"/api/tasks/{SEED_TASK_ID}").json()
            assert task["title"] == "Authentication Regression Test"
            assert task["run"]["passed"] == 5 and task["run"]["failed"] == 1
            assert task["run"]["coverage"] == 100 and len(task["findings"]) == 1
            assert task["findings"][0]["severity"] == "High"
            assert client.get(f"/api/projects/{SEED_PROJECT_ID}").json()["name"] == "Login API Quality Demo"
            assert "autoqa123" not in json.dumps(task)
            assert "seed-asgi-fixture" in json.dumps(task)
            assert client.get("/api/dashboard").json()["tasks"] == 1


def test_production_private_mode_disables_debugging():
    settings = Settings(_env_file=None, env="production", demo_mode=False,
                        api_token="test-only-token", mcp_enabled=True)
    assert not settings.mcp_enabled and not create_app(settings).debug


def test_public_mutation_and_file_boundaries(settings):
    with client_for(settings) as client:
        for path in ["/docs", "/openapi.json", "/debug", "/.env", "/data/autoqa.db"]:
            assert client.get(path).status_code == 404
        for method in [client.post, client.patch, client.delete]:
            assert method(f"/api/projects/{SEED_PROJECT_ID}").status_code == 403
        for action in ["approve", "cancel"]:
            assert client.post(f"/api/tasks/{SEED_TASK_ID}/{action}", json={"approved": True}).status_code == 403
        assert client.get(f"/api/tasks/{SEED_TASK_ID}/artifacts/report.html").status_code == 403
        assert client.get(f"/api/tasks/{SEED_TASK_ID}/report").status_code == 200
        assert client.post("/api/tasks", json={**payload(settings), "OPENAI_API_KEY": "canary"}).status_code == 422
        assert client.post("/api/tasks", json={**payload(settings), "steps": [{"action": "shell"}]}).status_code == 422
        assert client.post("/api/tasks", json={**payload(settings), "target_url": "http://169.254.169.254/"}).status_code == 422
        assert client.post("/api/tasks", content="x" * 17000).status_code == 413


def test_demo_mock_workflow_ownership_and_ip_limit(settings, monkeypatch):
    settings.demo_ip_limit = 1
    app = create_app(settings)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        **{**kwargs, "transport": httpx.ASGITransport(app=app)}))
    with TestClient(app, base_url="https://testserver") as client:
        response = client.post("/api/tasks", json=payload(settings))
        assert response.status_code == 202, response.text
        assert "HttpOnly" in response.headers["set-cookie"] and "Secure" in response.headers["set-cookie"]
        task_id = response.json()["id"]
        task = wait(client, task_id, "awaiting_approval")
        assert task["planner_mode"] == "mock" and len(task["plan"]["cases"]) == 6
        cookie = client.cookies.get("autoqa_visitor")
        client.cookies.clear()
        assert client.post(f"/api/tasks/{task_id}/approve", json={"approved": True}).status_code == 403
        assert client.get(f"/api/tasks/{task_id}").status_code == 403
        limited = client.post("/api/tasks", json=payload(settings))
        assert limited.status_code == 429 and "明天" in limited.json()["detail"]
        client.cookies.clear()
        client.cookies.set("autoqa_visitor", cookie)
        assert client.post(f"/api/tasks/{task_id}/approve", json={"approved": True}).status_code == 202
        task = wait(client, task_id, "failed")
        assert task["run"]["passed"] == 5 and task["run"]["failed"] == 1
        assert "canary" not in json.dumps(task)
    with client_for(settings) as client:
        assert client.post("/api/tasks", json=payload(settings)).status_code == 429


def test_global_quota_is_atomic_across_sources_and_restart(settings):
    settings.demo_daily_limit = 3
    store = Store(settings.database_url)
    request = TaskCreate(**payload(settings))

    def submit(index):
        try:
            store.create(request, source=str(index), owner=str(index), settings=settings)
            return True
        except DemoLimitError:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(submit, range(12))) == 3
    with Session(store.engine) as session:
        assert session.get(DemoQuotaRow, "global").count == 3
        session.execute(update(DemoQuotaRow).values(day="2000-01-01"))
        session.commit()
    assert submit(0)
    store.engine.dispose()
    reopened = Store(settings.database_url)
    with Session(reopened.engine) as session:
        assert session.get(DemoQuotaRow, "global").count == 1
    reopened.engine.dispose()


def test_invalid_tasks_do_not_spend_quota_and_zero_limit(settings):
    settings.demo_daily_limit = 0
    with client_for(settings) as client:
        assert client.post("/api/tasks", json={**payload(settings), "test_type": "smoke"}).status_code == 422
        assert client.post("/api/tasks", json=payload(settings)).status_code == 429
        assert client.get("/api/dashboard").json()["tasks"] == 1


def test_public_seed_remains_visible_after_large_shared_history(settings):
    app = create_app(settings)
    with TestClient(app, base_url="https://testserver") as client:
        for index in range(51):
            app.state.store.create(TaskCreate(**payload(settings)), source=str(index), owner="another-visitor",
                                   settings=settings.model_copy(update={"demo_daily_limit": 100}))
        rows = client.get("/api/tasks").json()
        assert len(rows) == 1 and rows[0]["id"] == SEED_TASK_ID


def test_safe_errors_validation_and_logs(settings, monkeypatch):
    app = create_app(settings)
    with TestClient(app, base_url="https://testserver") as client:
        monkeypatch.setattr(app.state.store, "dashboard", lambda: (_ for _ in ()).throw(
            RuntimeError("canary-secret C:/private/credentials")))
        response = client.get("/api/dashboard")
        assert response.status_code == 500 and "canary" not in response.text and "private" not in response.text
        response = client.post("/api/tasks", json={"goal": "canary-secret"})
        assert response.status_code == 422 and "canary" not in response.text
    record = logging.LogRecord("test", logging.ERROR, "C:/private", 1, "canary-secret", (), None)
    assert "canary" not in SafeFormatter().format(record)


@pytest.mark.parametrize("driver", ["mysql", "mysql+mysqlconnector", "mysql+pymysql"])
def test_railway_mysql_url_normalization_and_schema(driver):
    from sqlalchemy.engine import make_url
    settings = Settings(_env_file=None, database_url=f"{driver}://user:a%40b%2Fc%23d@mysql.railway.internal:3306/studio")
    url = make_url(settings.database_url)
    assert url.drivername == "mysql+pymysql" and url.password == "a@b/c#d"
    assert url.query["charset"] == "utf8mb4" and "a@b/c#d" not in repr(settings)
    for table in Base.metadata.sorted_tables:
        assert "CREATE TABLE" in str(CreateTable(table).compile(dialect=mysql.dialect()))


async def test_sql_checkpointer_persists_interrupt_and_resumes(settings):
    store = Store(settings.database_url)

    def review(state):
        approved = interrupt("review")
        return {"approved": approved}

    def graph(saver):
        builder = StateGraph(dict)
        builder.add_node("review", review)
        builder.add_edge(START, "review")
        builder.add_edge("review", END)
        return builder.compile(checkpointer=saver)

    config = {"configurable": {"thread_id": "test-approval"}}
    first = await graph(SQLCheckpointSaver(store.engine)).ainvoke({"hello": "world"}, config)
    assert first["__interrupt__"]
    store.engine.dispose()
    reopened = Store(settings.database_url)
    saver = SQLCheckpointSaver(reopened.engine)
    final = await graph(saver).ainvoke(Command(resume=True), config)
    assert final["approved"] is True
    history = [item async for item in saver.alist(config)]
    assert len(history) >= 2
    reopened.engine.dispose()
