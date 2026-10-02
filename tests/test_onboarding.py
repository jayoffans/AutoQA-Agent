"""Phase 6-2-A: account-owned setup, persistence and observed first-use milestones."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, func, select, text
from sqlalchemy.orm import Session

from autoqa.app import create_app
from autoqa.migrations import upgrade
from autoqa.product_models import MemberRow, OnboardingRow, SessionRow, UserRow, WorkspaceRow
from tests.test_auth import bearer, sign_up
from tests.test_product import product as product  # noqa: F401 - isolated production fixture
from tests.test_product import provider, wait


def start_project(client):
    assert client.patch("/api/onboarding", json={"action": "start"}).status_code == 200
    assert client.patch("/api/onboarding", json={"action": "skip_model"}).status_code == 200


def first_project(client, kind="api"):
    start_project(client)
    response = client.post("/api/onboarding/project", json={"name": "  我的首个项目  ", "project_type": kind})
    assert response.status_code == 201, response.text
    return response.json()


def test_new_user_onboarding_created(product):
    signed = sign_up(product)
    state = product.get("/api/onboarding").json()
    assert signed["onboarding"] == state == product.get("/api/auth/me").json()["onboarding"]
    assert state["step"] == 1
    assert not any(state[k] for k in ("model_configured", "project_created", "first_test_completed", "onboarding_completed"))
    with Session(product.application.state.store.engine) as session:
        row = session.get(OnboardingRow, signed["user"]["id"])
        assert row.workspace_id == state["workspace_id"]
        assert session.get(WorkspaceRow, row.workspace_id).owner_id == row.user_id


def test_existing_user_skip_onboarding(product):
    signed = sign_up(product)
    # An account without an onboarding row represents a pre-migration user.
    with Session(product.application.state.store.engine) as session:
        session.execute(delete(OnboardingRow))
        session.commit()
    product.cookies.clear()
    response = product.post("/api/auth/login", json={"email": signed["user"]["email"], "password": "phase6-fixture-password"})
    assert response.status_code == 200 and response.json()["onboarding"] is None
    product.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    assert product.get("/api/onboarding").json() is None
    assert product.get("/api/auth/me").json()["onboarding"] is None
    assert product.patch("/api/onboarding", json={"action": "start"}).status_code == 409
    assert product.get("/api/dashboard").status_code == 200


def test_onboarding_workspace_isolation(product):
    alice = sign_up(product, "onboard-a@example.com")
    first_project(product)
    alice_state = product.get("/api/onboarding").json()
    bob = sign_up(product, "onboard-b@example.com")
    bob_client = bearer(product, bob["access_token"])
    assert bob_client.get("/api/onboarding").json()["step"] == 1
    bob_client.headers["X-Workspace-ID"] = alice_state["workspace_id"]
    assert bob_client.get("/api/onboarding").status_code == 404
    assert bob_client.patch("/api/onboarding", json={"action": "complete"}).status_code == 404
    assert bearer(product, alice["access_token"]).get("/api/onboarding").json() == alice_state


def test_shared_workspace_never_exposes_another_users_progress(product):
    alice = sign_up(product, "shared-a@example.com")
    alice_state = first_project(product)["onboarding"]
    bob = sign_up(product, "shared-b@example.com")
    bob_client = bearer(product, bob["access_token"])
    with Session(product.application.state.store.engine) as session:
        session.add(MemberRow(workspace_id=alice_state["workspace_id"], user_id=bob["user"]["id"], role="member"))
        session.commit()
    bob_client.headers["X-Workspace-ID"] = alice_state["workspace_id"]
    own = bob_client.get("/api/onboarding?user_id=" + alice["user"]["id"]).json()
    assert own["workspace_id"] != alice_state["workspace_id"] and own["step"] == 1
    assert not own["project_created"] and own["project_id"] is None
    assert bob_client.patch("/api/onboarding", json={"action": "start"}).status_code == 404
    assert bob_client.post("/api/onboarding/project", json={"name": "wrong scope", "project_type": "api"}).status_code == 404


def test_skip_model_does_not_claim_configuration(product):
    sign_up(product)
    start_project(product)
    state = product.get("/api/onboarding").json()
    assert state["step"] == 3 and not state["model_configured"]
    assert product.calls == []


@pytest.mark.parametrize("kind", ["api", "web"])
def test_first_project_persisted_with_selected_type_and_no_fake_target(product, kind):
    sign_up(product)
    result = first_project(product, kind)
    item, state = result["project"], result["onboarding"]
    assert item["name"] == "我的首个项目" and item["project_type"] == kind and item["url"] == ""
    assert item["workspace_id"] == state["workspace_id"]
    assert state["step"] == 4 and state["project_created"] and not state["onboarding_completed"]
    assert product.get("/api/projects").json() == [item]
    assert product.get("/api/dashboard").json()["projects"] == 1


def test_completion_is_separate_from_model_and_first_test(product):
    sign_up(product)
    first_project(product)
    done = product.patch("/api/onboarding", json={"action": "complete"}).json()
    assert done["onboarding_completed"] and done["project_created"]
    assert not done["model_configured"] and not done["first_test_completed"]
    assert product.patch("/api/onboarding", json={"action": "start"}).json() == done
    assert product.patch("/api/onboarding", json={"action": "complete"}).json() == done


@pytest.mark.parametrize("payload", [{"name": "   ", "project_type": "api"},
    {"name": "x" * 81, "project_type": "api"}, {"name": "项目", "project_type": "mobile"},
    {"name": "项目", "project_type": "api", "user_id": "foreign"},
    {"name": "项目", "project_type": "api", "workspace_id": "foreign"}])
def test_first_project_rejects_invalid_input_and_identity_overrides(product, payload):
    sign_up(product)
    start_project(product)
    assert product.post("/api/onboarding/project", json=payload).status_code == 422
    assert product.get("/api/projects").json() == []
    assert product.get("/api/onboarding").json()["step"] == 3


def test_steps_cannot_be_skipped_or_progress_forged(product):
    sign_up(product)
    for action in ("skip_model", "complete"):
        assert product.patch("/api/onboarding", json={"action": action}).status_code == 409
    assert product.patch("/api/onboarding", json={"action": "start", "first_test_completed": True}).status_code == 422
    assert product.post("/api/onboarding/project", json={"name": "项目", "project_type": "web"}).status_code == 409
    start_project(product)
    assert product.patch("/api/onboarding", json={"action": "start"}).json()["step"] == 3
    assert product.patch("/api/onboarding", json={"action": "complete"}).status_code == 409


def test_first_project_retry_and_concurrency_create_only_one_project(product):
    signed = sign_up(product)
    start_project(product)
    def create(_):
        client = bearer(product, signed["access_token"])
        return client.post("/api/onboarding/project", json={"name": "单个项目", "project_type": "api"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, range(2)))
    assert all(result.status_code == 201 for result in results)
    assert results[0].json()["project"]["id"] == results[1].json()["project"]["id"]
    assert len(product.get("/api/projects").json()) == 1


def test_onboarding_requires_authentication_and_cookie_csrf(product):
    assert product.get("/api/onboarding").status_code == 401
    assert product.patch("/api/onboarding", json={"action": "start"}).status_code == 401
    sign_up(product)
    product.headers.pop("X-CSRF-Token")
    assert product.patch("/api/onboarding", json={"action": "start"}).status_code == 403
    assert product.post("/api/onboarding/project", json={"name": "项目", "project_type": "api"}).status_code == 403


def test_onboarding_atomic_registration_failure_leaves_no_rows(product):
    engine = product.application.state.store.engine
    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO user_onboarding"):
            raise RuntimeError("onboarding-fixture-failure")
    event.listen(engine, "before_cursor_execute", fail)
    try:
        result = product.post("/api/auth/register", json={"email": "atomic@example.com", "password": "fixture-password"})
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert result.status_code == 500 and "onboarding-fixture-failure" not in result.text
    with Session(engine) as session:
        for model in (UserRow, WorkspaceRow, MemberRow, SessionRow, OnboardingRow):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_onboarding_survives_restart_and_relogin(product):
    signed = sign_up(product)
    result = first_project(product)
    with TestClient(create_app(product.settings), base_url="https://testserver") as reopened:
        response = reopened.post("/api/auth/login", json={"email": signed["user"]["email"], "password": "phase6-fixture-password"})
        assert response.json()["onboarding"] == result["onboarding"]
        assert reopened.get("/api/onboarding").json() == result["onboarding"]
        assert reopened.get("/api/projects").json() == [result["project"]]


@pytest.mark.parametrize("save_mode", ["workspace", "session"])
def test_existing_model_configuration_marks_own_milestone(product, save_mode):
    sign_up(product)
    provider(product, save_mode)
    assert product.get("/api/onboarding").json()["model_configured"]
    assert product.calls == []  # Saving/configuration progress does not call a model.


def test_first_test_completed_requires_finished_execution(product):
    sign_up(product)
    item = first_project(product)["project"]
    provider(product)
    assert product.put(f'/api/projects/{item["id"]}/environment', json={"base_url": "https://qa.example"}).status_code == 200
    assert product.post(f'/api/projects/{item["id"]}/openapi', json={"endpoints": [{"path": "/users/1", "method": "GET"}]}).status_code == 200
    response = product.post("/api/tasks", json={"project_id": item["id"], "goal": "检查首个接口", "endpoints": ["GET /users/1"]})
    assert response.status_code == 202, response.text
    task = response.json()
    wait(product, task["id"], "awaiting_approval")
    assert not product.get("/api/onboarding").json()["first_test_completed"]
    assert product.post(f'/api/tasks/{task["id"]}/approve', json={"approved": True}).status_code == 202
    wait(product, task["id"], "passed")
    assert product.get("/api/onboarding").json()["first_test_completed"]


def test_onboarding_migration_preserves_existing_users_and_is_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.sqlite'}")
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0003_auth")
        connection.execute(text("INSERT INTO users (id,email,password_hash,nickname,is_active,created_at,updated_at) "
            "VALUES ('old','old@example.com','old-password-hash','老用户',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
    upgrade(engine)
    upgrade(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0009_execution_assertions"
        assert connection.execute(text("SELECT email,password_hash,nickname FROM users")).one() == (
            "old@example.com", "old-password-hash", "老用户")
        assert connection.scalar(text("SELECT COUNT(*) FROM user_onboarding")) == 0
        assert not connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    engine.dispose()
