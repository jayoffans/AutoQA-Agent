"""Phase 6-1.5: run identical migrations/auth/permissions on SQLite and real MySQL."""
import logging
import os
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from autoqa.app import create_app
from autoqa.config import Settings
from autoqa.crypto import token_hash
from autoqa.product_models import MemberRow, SessionRow, UserRow, WorkspaceRow
from autoqa.store_base import Base, now
from tests.onboarding_support import complete_onboarding
from tests.production_support import (
    ROOT,
    MockServices,
    ProductionService,
    checked_process,
    clean_env,
    completed_task,
    create_project,
    random_key,
    register,
)


@pytest.fixture(params=["sqlite", "mysql"])
def compat_settings(request, tmp_path):
    admin = None
    if request.param == "mysql":
        supplied = os.getenv("AUTOQA_TEST_COMPAT_MYSQL_ADMIN_URL")
        if not supplied:
            if os.getenv("AUTOQA_REQUIRE_MYSQL") == "1":
                pytest.fail("Required disposable MySQL admin URL is missing")
            pytest.skip("Supply AUTOQA_TEST_COMPAT_MYSQL_ADMIN_URL for real MySQL acceptance")
        url = make_url(supplied)
        assert url.drivername == "mysql+pymysql"
        name = "test_autoqa_" + secrets.token_hex(8)
        admin = create_engine(url.set(database=None), hide_parameters=True)
        with admin.begin() as connection:
            connection.execute(text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
        database_url = url.set(database=name).update_query_dict({"charset": "utf8mb4"}).render_as_string(False)
    else:
        database_url = f"sqlite:///{tmp_path / 'compat.sqlite'}"
    settings = Settings(_env_file=None, mode="production", env="production", secret_key=random_key(),
        database_url=database_url, data_dir=tmp_path / "data", artifact_dir=tmp_path / "artifacts",
        allow_private_targets=True)
    try:
        yield settings
    finally:
        if admin:
            # Only the randomly named database allocated by this fixture may be destroyed.
            with admin.begin() as connection:
                connection.execute(text(f"DROP DATABASE `{name}`"))
            admin.dispose()


@pytest.fixture
def compat(compat_settings):
    with MockServices() as services, TestClient(create_app(compat_settings), base_url="https://testserver") as client:
        yield client, services, compat_settings


def cli_migrate(settings, revision="head"):
    checked_process([sys.executable, "-m", "alembic", "upgrade", revision], env=clean_env(
        DATABASE_URL=settings.database_url, AUTOQA_MODE="production", AUTOQA_ENV="production",
        AUTOQA_SECRET_KEY=settings.secret_key))


def test_empty_database_cli_migration_chain_tables_constraints(compat_settings):
    settings = compat_settings
    engine = create_engine(settings.database_url, hide_parameters=True)
    try:
        assert inspect(engine).get_table_names() == []
        for revision in ("0001_legacy", "0002_product", "0003_auth", "0004_onboarding", "0005_provider_center", "0006_api_import", "0007_test_planner", "0008_api_execution"):
            cli_migrate(settings, revision)
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == revision
        inspector = inspect(engine)
        # Sessions is the existing user_sessions table; artifacts are task-scoped files.
        assert set(Base.metadata.tables) <= set(inspector.get_table_names())
        for table in ("users", "workspaces", "workspace_members", "user_sessions", "projects", "tasks",
                      "reports", "findings"):
            assert inspector.get_pk_constraint(table)["constrained_columns"]
        assert {"nickname", "is_active", "updated_at"} <= {c["name"] for c in inspector.get_columns("users")}
        assert {"created_at", "refresh_token_hash"} <= {c["name"] for c in inspector.get_columns("user_sessions")}
        assert {"users"} <= {fk["referred_table"] for fk in inspector.get_foreign_keys("user_sessions")}
        assert {"users", "workspaces"} <= {fk["referred_table"] for fk in inspector.get_foreign_keys("workspace_members")}
        assert ["refresh_token_hash"] in [c["column_names"] for c in inspector.get_unique_constraints("user_sessions")]
    finally:
        engine.dispose()


def test_cli_upgrade_head_twice_preserves_account(compat):
    client, _, settings = compat
    signed, _, _ = register(client)
    cli_migrate(settings)
    cli_migrate(settings)
    assert client.get("/api/auth/me").json()["user"]["id"] == signed["user"]["id"]
    with Session(client.app.state.store.engine) as session:
        assert session.scalar(select(func.count()).select_from(UserRow)) == 1


def test_production_mysql_connections_use_utc_after_pool_reconnect(compat):
    client, _, settings = compat
    engine = client.app.state.store.engine
    if make_url(settings.database_url).get_backend_name() == "sqlite":
        with engine.connect() as connection:
            assert abs((now().replace(tzinfo=None) - connection.scalar(select(func.current_timestamp()))).total_seconds()) < 5
    else:
        for _ in range(2):
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT @@session.time_zone")) == "+00:00"
                assert abs((now().replace(tzinfo=None) - connection.scalar(select(func.current_timestamp()))).total_seconds()) < 5
            engine.dispose()


def test_register_atomic_workspace_unicode_owner_and_hashes(compat):
    client, _, _ = compat
    signed, _, password = register(client)
    me = client.get("/api/auth/me").json()
    assert me["workspaces"][0]["name"] == "兼容用户的工作空间"
    with Session(client.app.state.store.engine) as session:
        owner = session.get(UserRow, signed["user"]["id"])
        workspace = session.get(WorkspaceRow, me["workspace_id"])
        member = session.get(MemberRow, (workspace.id, owner.id))
        saved = session.scalar(select(SessionRow))
        assert workspace.owner_id == owner.id and member.role == "owner" and member.id
        assert member.created_at and owner.updated_at and workspace.updated_at
        assert owner.password_hash.startswith("scrypt$") and password not in owner.password_hash
        assert saved.refresh_token_hash == token_hash(signed["refresh_token"])
    assert client.get("/api/projects").json() == []
    assert client.get("/api/tasks").json() == []
    assert "password_hash" not in client.get("/api/auth/me").text
    assert client.get("/demo/login.html").status_code == 404


def test_duplicate_email_rolls_back_related_rows(compat):
    client, _, _ = compat
    _, email, password = register(client)
    response = client.post("/api/auth/register", json={"email": email.upper(), "password": password})
    assert response.status_code == 409
    with Session(client.app.state.store.engine) as session:
        for table in (UserRow, WorkspaceRow, MemberRow, SessionRow):
            assert session.scalar(select(func.count()).select_from(table)) == 1


def test_login_refresh_execution_report_logout_complete_lifecycle(compat):
    client, services, _ = compat
    signed, email, password = register(client)
    client.cookies.clear()
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    login = response.json()
    assert login["user"] == signed["user"]
    response = client.post("/api/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert response.status_code == 200
    rotated = response.json()
    client.headers["Authorization"] = "Bearer " + rotated["access_token"]
    assert rotated["refresh_token"] != login["refresh_token"]
    assert client.post("/api/auth/refresh", json={"refresh_token": login["refresh_token"]}).status_code == 401
    item, task_id, _ = completed_task(client, services.url)
    report = client.get(f"/api/tasks/{task_id}/report?format=json")
    assert report.status_code == 200 and report.json()["metrics"]["passed"] == 1
    assert client.get('/api/projects/' + item["id"]).status_code == 200
    assert "/v1/chat/completions" in services.calls and "/users/1" in services.calls
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/projects").status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": rotated["refresh_token"]}).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": login["refresh_token"]}).status_code == 401


def test_refresh_concurrency_single_winner(compat):
    client, _, _ = compat
    signed, _, _ = register(client)

    # Use clients without entering additional lifespans on the same app.
    def once(_):
        other = TestClient(client.app, base_url="https://testserver")
        try:
            return other.post("/api/auth/refresh", json={"refresh_token": signed["refresh_token"]}).status_code
        finally:
            other.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(once, range(2))) == [200, 401]


def test_database_expired_refresh_rejected_in_utc(compat):
    client, _, _ = compat
    signed, _, _ = register(client)
    with Session(client.app.state.store.engine) as session:
        session.scalar(select(SessionRow)).expires_at = now() - timedelta(seconds=1)
        session.commit()
    assert client.post("/api/auth/refresh", json={"refresh_token": signed["refresh_token"]}).status_code == 401
    assert client.get("/api/projects").status_code == 401


def test_foreign_workspace_project_and_header_isolation(compat):
    client, services, _ = compat
    register(client)
    workspace = client.get("/api/auth/me").json()["workspace_id"]
    item = create_project(client, services.url)
    other = TestClient(client.app, base_url="https://testserver")
    try:
        register(other)
        assert other.get('/api/projects/' + item["id"]).status_code == 404
        assert other.get("/api/projects", headers={"X-Workspace-ID": workspace}).status_code == 404
        assert other.get("/api/projects").json() == []
    finally:
        other.close()


def test_foreign_task_report_findings_and_artifact_isolation(compat):
    client, services, settings = compat
    register(client)
    _, task_id, _ = completed_task(client, services.url)
    assert (settings.artifact_dir / task_id / "report.html").is_file()
    other = TestClient(client.app, base_url="https://testserver")
    try:
        register(other)
        for suffix in ("", "/report", "/report?format=json", "/findings", "/workflow", "/artifacts/report.html"):
            path = f"/api/tasks/{task_id}{suffix}"
            assert client.get(path).status_code == 200
            response = other.get(path)
            assert response.status_code == 404 and "持久项目" not in response.text
    finally:
        other.close()


def test_member_removal_takes_effect_on_mysql_and_sqlite(compat):
    client, services, _ = compat
    register(client)
    item = create_project(client, services.url)
    workspace = client.get("/api/auth/me").json()["workspace_id"]
    other = TestClient(client.app, base_url="https://testserver")
    try:
        signed, email, _ = register(other)
        assert client.post("/api/workspace/members", json={"email": email}).status_code == 201
        other.headers["X-Workspace-ID"] = workspace
        assert other.get('/api/projects/' + item["id"]).status_code == 200
        with Session(client.app.state.store.engine) as session:
            session.delete(session.get(MemberRow, (workspace, signed["user"]["id"])))
            session.commit()
        assert other.get('/api/projects/' + item["id"]).status_code == 404
    finally:
        other.close()


def test_production_cookie_secure_csrf_and_origin(compat):
    client, _, _ = compat
    register(client)
    assert all(cookie.secure and cookie._rest.get("HttpOnly") is None for cookie in client.cookies.jar)
    del client.headers["Authorization"]
    assert client.post("/api/auth/refresh", headers={"X-CSRF-Token": "incorrect"}).status_code == 403
    assert client.post("/api/auth/refresh", headers={"Origin": "https://untrusted.example"}).status_code == 403
    assert client.post("/api/auth/refresh").status_code == 200


def test_secrets_absent_from_logs_and_response_validation(compat, caplog):
    client, _, settings = compat
    caplog.set_level(logging.DEBUG)
    signed, _, password = register(client)
    validation = client.post("/api/auth/login", json={"email": "invalid", "password": password})
    assert validation.status_code == 422
    for value in (password, signed["access_token"], signed["refresh_token"], settings.secret_key,
                  make_url(settings.database_url).password):
        if value:
            assert value not in caplog.text and value not in validation.text


def test_real_https_service_restart_persists_user_workspace_project_report_artifact(compat_settings, tmp_path):
    with MockServices() as services:
        server = ProductionService(compat_settings, tmp_path / "tls-service")
        try:
            server.start()
            with httpx.Client(base_url=server.url, verify=False, timeout=30) as client:
                signed, email, password = register(client)
                workspace = client.get("/api/auth/me").json()["workspace_id"]
                item, task_id, model_key = completed_task(client, services.url)
                content = client.get(f"/api/tasks/{task_id}/artifacts/report.html").content
                assert content
                server.restart()
                assert client.get("/health").json() == {"status": "ok"}
                assert client.get("/api/auth/me").json()["workspace_id"] == workspace
                assert client.get('/api/projects/' + item["id"]).json()["name"] == item["name"]
                assert client.get(f"/api/tasks/{task_id}/report?format=json").json()["metrics"]["passed"] == 1
                assert client.get(f"/api/tasks/{task_id}/artifacts/report.html").content == content
                assert client.post("/api/auth/refresh", json={"refresh_token": signed["refresh_token"]}).status_code == 200
                login = client.post("/api/auth/login", json={"email": email, "password": password})
                assert login.status_code == 200
                logs = (server.directory / "service.log").read_text(encoding="utf-8")
                for value in (password, signed["access_token"], signed["refresh_token"], model_key, compat_settings.secret_key,
                              make_url(compat_settings.database_url).password):
                    if value:
                        assert value not in logs
        finally:
            server.stop()


def test_production_https_browser_reload_logout_relogin_keeps_project(compat_settings, tmp_path):
    server = ProductionService(compat_settings, tmp_path / "browser-service")
    try:
        server.start()
        with MockServices() as target, sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(ignore_https_errors=True)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                email, password = secrets.token_hex(8) + "@example.com", secrets.token_urlsafe(24)
                page.goto(server.url)
                page.get_by_role("button", name="注册", exact=True).click()
                page.locator('#auth-form [name="email"]').fill(email)
                page.locator('#auth-form [name="password"]').fill(password)
                page.locator('#auth-form [name="nickname"]').fill("持久用户")
                page.get_by_role("button", name="注册", exact=True).click()
                complete_onboarding(page)
                assert "持久用户的工作空间" in page.locator("body").inner_text()
                page.get_by_role("link", name="项目", exact=True).click()
                page.get_by_role("button", name="创建项目", exact=True).click()
                page.locator('#project-form [name="name"]').fill("浏览器持久项目")
                page.locator('#project-form [name="url"]').fill(target.url)
                page.locator('#project-form').get_by_role("button", name="保存", exact=True).click()
                page.get_by_role("heading", name="浏览器持久项目", exact=True).wait_for()
                page.reload()
                page.get_by_role("heading", name="浏览器持久项目", exact=True).wait_for()
                server.restart()
                page.reload()
                page.get_by_role("heading", name="浏览器持久项目", exact=True).wait_for()
                page.get_by_role("button", name="退出登录", exact=True).click()
                page.locator('#auth-form').wait_for()
                assert page.request.get(server.url + "/api/projects").status == 401
                page.locator('#auth-form [name="email"]').fill(email)
                page.locator('#auth-form [name="password"]').fill(password)
                page.get_by_role("button", name="登录", exact=True).click()
                page.get_by_role("link", name="项目", exact=True).click()
                page.get_by_role("heading", name="浏览器持久项目", exact=True).wait_for()
                assert not errors
                backend = make_url(compat_settings.database_url).get_backend_name()
                output = ROOT / "work/production-compat"
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / f"browser-{backend}.png"), full_page=True)
            finally:
                browser.close()
    finally:
        server.stop()
