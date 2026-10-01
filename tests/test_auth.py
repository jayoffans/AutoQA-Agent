"""Phase 6-1 account lifecycle and isolation: isolated DBs, no real model calls."""
import hashlib
import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone

import jwt
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.orm import Session

from autoqa.app import create_app
from autoqa.auth import ACCESS_SECONDS, AUDIENCE, ISSUER, REFRESH_SECONDS, Tokens
from autoqa.crypto import check_password, token_hash
from autoqa.migrations import upgrade
from autoqa.product_models import MemberRow, SessionRow, UserRow, WorkspaceRow
from autoqa.schemas import TaskCreate
from autoqa.store_base import now
from tests.test_product import SECRET, project, provider
from tests.test_product import product as product  # noqa: F401 - shared isolated fixture

PASSWORD = "phase6-fixture-password"


def sign_up(client, email="phase6@example.com", nickname="张三"):
    response = client.post("/api/auth/register", json={"email": email, "password": PASSWORD, "nickname": nickname})
    assert response.status_code == 201, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response.json()


def bearer(client, token):
    result = TestClient(client.application, base_url="https://testserver")
    result.headers["Authorization"] = "Bearer " + token
    return result


def test_register_success(product):
    result = sign_up(product)
    assert result["user"]["nickname"] == "张三" and result["user"]["is_active"] is True
    assert result["user"]["email"] == "phase6@example.com"
    assert "password" not in result["user"] and "password_hash" not in result["user"]
    with Session(product.application.state.store.engine) as session:
        user = session.get(UserRow, result["user"]["id"])
        assert check_password(PASSWORD, user.password_hash)
        assert user.password_hash.startswith("scrypt$16384$8$5$")
        assert PASSWORD not in user.password_hash and user.updated_at >= user.created_at


def test_duplicate_email(product):
    sign_up(product)
    response = product.post("/api/auth/register", json={"email": " PHASE6@EXAMPLE.COM ", "password": PASSWORD})
    assert response.status_code == 409
    with Session(product.application.state.store.engine) as session:
        for row in (UserRow, WorkspaceRow, MemberRow, SessionRow):
            assert session.scalar(select(func.count()).select_from(row)) == 1


def test_login_success(product):
    signed = sign_up(product)
    product.cookies.clear()
    response = product.post("/api/auth/login", json={"email": "PHASE6@example.com", "password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["user"] == signed["user"]
    assert bearer(product, response.json()["access_token"]).get("/api/projects").json() == []


def test_wrong_password(product):
    sign_up(product)
    wrong = product.post("/api/auth/login", json={"email": "phase6@example.com", "password": "incorrect-password"})
    unknown = product.post("/api/auth/login", json={"email": "unknown@example.com", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401 and wrong.json() == unknown.json()
    short = product.post("/api/auth/login", json={"email": "phase6@example.com", "password": "wrong"})
    assert short.status_code == 401 and short.json() == wrong.json()


def test_register_creates_workspace(product):
    signed = sign_up(product)
    me = product.get("/api/auth/me").json()
    assert len(me["workspaces"]) == 1 and me["workspaces"][0]["name"] == "张三的工作空间"
    with Session(product.application.state.store.engine) as session:
        workspace = session.get(WorkspaceRow, me["workspace_id"])
        assert workspace.owner_id == signed["user"]["id"] and workspace.updated_at >= workspace.created_at


def test_workspace_owner(product):
    signed = sign_up(product)
    me = product.get("/api/auth/me").json()
    assert me["workspaces"][0]["owner_id"] == signed["user"]["id"]
    assert me["workspaces"][0]["role"] == "owner"


def test_member_relation(product):
    signed = sign_up(product)
    workspace = product.get("/api/auth/me").json()["workspace_id"]
    with Session(product.application.state.store.engine) as session:
        member = session.get(MemberRow, (workspace, signed["user"]["id"]))
        assert member.role == "owner" and member.id and member.created_at


def test_refresh_token(product):
    signed = sign_up(product)
    # Explicit refresh works without browser cookies or an access token.
    client = TestClient(product.application, base_url="https://testserver")
    response = client.post("/api/auth/refresh", json={"refresh_token": signed["refresh_token"]})
    assert response.status_code == 200
    updated = response.json()
    assert updated["access_token"] != signed["access_token"]
    assert updated["refresh_token"] != signed["refresh_token"]
    assert bearer(product, updated["access_token"]).get("/api/auth/me").json()["user"] == signed["user"]
    with Session(product.application.state.store.engine) as session:
        saved = session.scalar(select(SessionRow))
        assert saved.refresh_token_hash == token_hash(updated["refresh_token"])
        assert signed["refresh_token"] not in saved.refresh_token_hash


def test_jwt_lifetime_claims_and_no_plaintext_storage(product):
    product.settings.env = "production"
    signed = sign_up(product)
    tokens = Tokens(SECRET)
    access, refresh = [tokens.decode(signed[key], kind) for key, kind in
                       [("access_token", "access"), ("refresh_token", "refresh")]]
    assert ACCESS_SECONDS - 1 <= access["exp"] - access["iat"] <= ACCESS_SECONDS
    assert REFRESH_SECONDS - 1 <= refresh["exp"] - refresh["iat"] <= REFRESH_SECONDS
    assert access["sid"] == refresh["sid"] and access["jti"] != refresh["jti"]
    assert access["iss"] == ISSUER and access["aud"] == AUDIENCE
    stored = (product.settings.data_dir.parent / "production.sqlite").read_bytes()
    for credential in (PASSWORD, signed["access_token"], signed["refresh_token"], product.cookies.get("autoqa_session")):
        assert credential.encode() not in stored
    for cookie in product.cookies.jar:
        assert cookie.secure and cookie._rest["HttpOnly"] is None and cookie._rest["SameSite"] == "strict"


def test_refresh_cookie_csrf_and_bootstrap(product):
    signed = sign_up(product)
    product.cookies.delete("autoqa_access")
    assert product.get("/api/projects").status_code == 401
    assert product.get("/api/auth/csrf").json()["csrf_token"] == signed["csrf_token"]
    assert product.post("/api/auth/refresh", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert product.post("/api/auth/refresh", headers={"Origin": "https://evil.example"}).status_code == 403
    assert product.post("/api/auth/refresh").status_code == 200
    assert product.get("/api/projects").status_code == 200


def test_refresh_replay_rejected(product):
    signed = sign_up(product)
    payload = {"refresh_token": signed["refresh_token"]}
    assert product.post("/api/auth/refresh", json=payload).status_code == 200
    assert product.post("/api/auth/refresh", json=payload).status_code == 401


def test_concurrent_refresh_is_single_use(product):
    signed = sign_up(product)
    def renew(_):
        client = TestClient(product.application, base_url="https://testserver")
        return client.post("/api/auth/refresh", json={"refresh_token": signed["refresh_token"]}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(renew, range(2))) == [200, 401]


def test_logout_revokes_refresh_and_access(product):
    signed = sign_up(product)
    assert product.post("/api/auth/logout").status_code == 204
    assert not list(product.cookies)
    assert bearer(product, signed["access_token"]).get("/api/projects").status_code == 401
    assert product.post("/api/auth/refresh", json={"refresh_token": signed["refresh_token"]}).status_code == 401


def test_logout_after_access_expiry(product):
    sign_up(product)
    provider(product, "session")
    product.cookies.delete("autoqa_access")
    assert product.post("/api/auth/logout").status_code == 204
    assert product.application.state.gateway.session_configs == {}


def test_bearer_logout_and_separate_login_sessions(product):
    first = sign_up(product)
    second = product.post("/api/auth/login", json={"email": "phase6@example.com", "password": PASSWORD}).json()
    client = bearer(product, first["access_token"])
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/projects").status_code == 401
    assert bearer(product, second["access_token"]).get("/api/projects").status_code == 200


@pytest.mark.parametrize("mutation", ["expired", "signature", "issuer", "audience", "missing_exp", "future", "none"])
def test_invalid_jwt_rejected(product, mutation):
    signed = sign_up(product)
    tokens = Tokens(SECRET)
    claims = tokens.decode(signed["access_token"], "access")
    key, algorithm = tokens.key, "HS256"
    if mutation == "expired":
        claims["exp"] = int((now() - timedelta(seconds=1)).timestamp())
    elif mutation == "signature":
        key = b"wrong-fixture-signing-key" * 2
    elif mutation == "issuer":
        claims["iss"] = "other-issuer"
    elif mutation == "audience":
        claims["aud"] = "other-audience"
    elif mutation == "missing_exp":
        claims.pop("exp")
    elif mutation == "future":
        claims["nbf"] = int((now() + timedelta(hours=1)).timestamp())
    else:
        key, algorithm = None, "none"
    invalid = jwt.encode(claims, key, algorithm=algorithm)
    assert bearer(product, invalid).get("/api/projects").status_code == 401


def test_refresh_token_cannot_access_business_or_access_token_refresh(product):
    signed = sign_up(product)
    assert bearer(product, signed["refresh_token"]).get("/api/projects").status_code == 401
    assert product.post("/api/auth/refresh", json={"refresh_token": signed["access_token"]}).status_code == 401
    raw = product.cookies.get("autoqa_session")
    product.cookies.clear()
    product.cookies.set("autoqa_session", raw)
    assert product.get("/api/projects").status_code == 401


def test_disabled_user_denied_login_access_refresh(product):
    signed = sign_up(product)
    provider(product, "session")
    with Session(product.application.state.store.engine) as session:
        user = session.get(UserRow, signed["user"]["id"])
        user.is_active = False
        session.commit()
    assert product.get("/api/projects").status_code == 401
    assert product.application.state.gateway.session_configs == {}
    assert product.post("/api/auth/refresh", json={"refresh_token": signed["refresh_token"]}).status_code == 401
    assert product.post("/api/auth/login", json={"email": "phase6@example.com", "password": PASSWORD}).status_code == 401


def test_expired_refresh_denied(product):
    signed = sign_up(product)
    with Session(product.application.state.store.engine) as session:
        session.scalar(select(SessionRow)).expires_at = now() - timedelta(seconds=1)
        session.commit()
    assert product.post("/api/auth/refresh", json={"refresh_token": signed["refresh_token"]}).status_code == 401


def test_registration_transaction_rolls_back(product):
    engine = product.application.state.store.engine
    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO workspace_members"):
            raise RuntimeError("database-fixture-private-detail")
    event.listen(engine, "before_cursor_execute", fail)
    try:
        response = product.post("/api/auth/register", json={"email": "atomic@example.com", "password": PASSWORD})
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert response.status_code == 500 and "database-fixture-private-detail" not in response.text
    with Session(engine) as session:
        for row in (UserRow, WorkspaceRow, MemberRow, SessionRow):
            assert session.scalar(select(func.count()).select_from(row)) == 0


def test_password_tokens_and_validation_not_logged(product, caplog):
    caplog.set_level(logging.DEBUG)
    signed = sign_up(product)
    response = product.post("/api/auth/login", json={"email": "bad", "password": PASSWORD})
    assert response.status_code == 422 and PASSWORD not in response.text
    assert product.post("/api/auth/refresh", json={"refresh_token": "private-token-canary"}).status_code == 401
    for secret in (PASSWORD, signed["access_token"], signed["refresh_token"], "private-token-canary"):
        assert secret not in caplog.text
    assert "password_hash" not in product.get("/api/auth/me").text


@pytest.fixture
def isolated_users(product):
    alice = sign_up(product)
    item = project(product)
    store = product.application.state.store
    task_id = store.create(TaskCreate(project_id=item["id"], target_url=item["url"], goal="private-alice-goal"))
    store.update(task_id, status="passed")
    directory = product.settings.artifact_dir / task_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "screenshot.png").write_bytes(b"private-alice-screenshot")
    (directory / "evidence.json").write_text('{"evidence":"private-alice-evidence"}', encoding="utf-8")
    bob = TestClient(product.application, base_url="https://testserver")
    sign_up(bob, "bob6@example.com", "李四")
    return alice, item, task_id, bob


def assert_private(response):
    assert response.status_code == 404
    for secret in ("真实测试项目", "private-alice-goal", "private-alice-screenshot", "private-alice-evidence"):
        assert secret not in response.text


def test_user_cannot_access_other_workspace_project(product, isolated_users):
    alice, item, _, bob = isolated_users
    assert_private(bob.get('/api/projects/' + item["id"]))
    assert_private(bob.get('/api/projects/' + item["id"] + '/environment'))
    assert_private(bob.get("/api/projects", headers={"X-Workspace-ID": product.get("/api/auth/me").json()["workspace_id"]}))
    assert bob.get("/api/projects").json() == []
    assert alice["user"]["id"] != bob.get("/api/auth/me").json()["user_id"]


def test_user_cannot_access_other_workspace_task(product, isolated_users):
    _, _, task_id, bob = isolated_users
    for suffix in ("", "/workflow", "/findings", "/events"):
        assert_private(bob.get(f"/api/tasks/{task_id}{suffix}"))
    assert bob.get("/api/tasks").json() == []


def test_user_cannot_access_other_workspace_report(product, isolated_users):
    _, _, task_id, bob = isolated_users
    assert product.get(f"/api/tasks/{task_id}/report").status_code == 200
    for suffix in ("", "?format=json"):
        assert_private(bob.get(f"/api/tasks/{task_id}/report{suffix}"))


def test_user_cannot_access_other_workspace_artifact(product, isolated_users):
    _, _, task_id, bob = isolated_users
    for name in ("screenshot.png", "evidence.json", "report.html"):
        assert product.get(f"/api/tasks/{task_id}/artifacts/{name}").status_code == 200
        assert_private(bob.get(f"/api/tasks/{task_id}/artifacts/{name}"))
    assert bob.get(f"/artifacts/{task_id}/screenshot.png").status_code == 404


def test_artifact_path_traversal_rejected(product, isolated_users):
    _, _, task_id, _ = isolated_users
    for name in ("%2e%2e%2fproduction.sqlite", "..%5cevidence.json", "%2fetc%2fpasswd", "evidence.json%00", "..%252fsecret.json"):
        assert product.get(f"/api/tasks/{task_id}/artifacts/{name}").status_code == 404


def test_artifact_symlink_rejected(product, isolated_users, tmp_path):
    _, _, task_id, _ = isolated_users
    target = tmp_path / "outside.json"
    target.write_text("private-symlink-target", encoding="utf-8")
    link = product.settings.artifact_dir / task_id / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable; this test runs in Linux CI")
    response = product.get(f"/api/tasks/{task_id}/artifacts/link.json")
    assert response.status_code == 404 and "private-symlink-target" not in response.text


def test_artifact_directory_alias_to_other_task_rejected(product, isolated_users):
    _, item, foreign_task_id, bob = isolated_users
    # Give the foreign task to Bob, while Alice retains an owned task whose directory is redirected.
    foreign_project = project(bob)
    store = product.application.state.store
    with Session(store.engine) as session:
        from autoqa.studio_models import TaskMetadataRow
        session.get(TaskMetadataRow, foreign_task_id).project_id = foreign_project["id"]
        session.commit()
    owned_task_id = store.create(TaskCreate(project_id=item["id"], target_url=item["url"], goal="owned-alice-goal"))
    link = product.settings.artifact_dir / owned_task_id
    target = product.settings.artifact_dir / foreign_task_id
    if os.name == "nt":
        # NTFS junctions can be created without the Windows symbolic-link privilege.
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "New-Item -ItemType Junction -Path $env:AUTOQA_TEST_LINK -Target $env:AUTOQA_TEST_TARGET | Out-Null"],
            env={**os.environ, "AUTOQA_TEST_LINK": str(link), "AUTOQA_TEST_TARGET": str(target)},
            capture_output=True, timeout=15)
        assert result.returncode == 0
    else:
        link.symlink_to(target, target_is_directory=True)
    response = product.get(f"/api/tasks/{owned_task_id}/artifacts/screenshot.png")
    assert response.status_code == 404 and "private-alice-screenshot" not in response.text


def test_removed_member_loses_access_immediately(product, isolated_users):
    _, item, task_id, bob = isolated_users
    workspace = product.get("/api/auth/me").json()["workspace_id"]
    user_id = bob.get("/api/auth/me").json()["user_id"]
    assert product.post("/api/workspace/members", json={"email": "bob6@example.com"}).status_code == 201
    bob.headers["X-Workspace-ID"] = workspace
    assert bob.get('/api/projects/' + item["id"]).status_code == 200
    with Session(product.application.state.store.engine) as session:
        session.delete(session.get(MemberRow, (workspace, user_id)))
        session.commit()
    assert_private(bob.get('/api/projects/' + item["id"]))
    assert_private(bob.get(f'/api/tasks/{task_id}/report'))


def test_workspace_owner_cannot_be_added_as_role(product, isolated_users):
    assert product.post("/api/workspace/members", json={"email": "bob6@example.com", "role": "owner"}).status_code == 422
    assert product.get("/api/auth/me").json()["workspaces"][0]["role"] == "owner"


@pytest.mark.parametrize("path", ["/api/projects", "/api/tasks", "/api/tasks/unknown/workflow",
                                  "/api/tasks/unknown/report", "/api/tasks/unknown/findings",
                                  "/api/tasks/unknown/artifacts/screenshot.png"])
def test_production_business_requires_auth(product, path):
    assert product.get(path).status_code == 401


def test_production_no_seed(product):
    assert product.settings.seed_demo is False
    assert product.application.state.store.projects() == []
    assert product.application.state.store.list() == []


def test_new_user_empty_workspace(product, isolated_users):
    _, _, _, bob = isolated_users
    assert bob.get("/api/projects").json() == bob.get("/api/tasks").json() == []
    assert all(value == 0 for value in bob.get("/api/dashboard").json().values())


def test_refresh_after_restart_preserves_project(product):
    signed = sign_up(product)
    item = project(product)
    with TestClient(create_app(product.settings), base_url="https://testserver") as reopened:
        renewed = reopened.post("/api/auth/refresh", json={"refresh_token": signed["refresh_token"]})
        assert renewed.status_code == 200
        reopened.headers["Authorization"] = "Bearer " + renewed.json()["access_token"]
        assert reopened.get('/api/projects/' + item["id"]).json()["id"] == item["id"]


@pytest.mark.parametrize("backend", ["sqlite", "mysql"])
def test_phase5_migration_preserves_accounts_and_upgrades_password_on_login(tmp_path, backend):
    from autoqa.config import Settings
    path = tmp_path / "phase5.sqlite"
    url = f"sqlite:///{path}" if backend == "sqlite" else os.getenv("AUTOQA_TEST_AUTH_MYSQL_URL")
    if not url:
        pytest.skip("No disposable Phase 6 MySQL supplied; migration acceptance runs in CI")
    engine = create_engine(url)
    config = Config("alembic.ini")
    salt = "ab" * 16
    legacy_hash = "scrypt$" + salt + "$" + hashlib.scrypt(PASSWORD.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0002_product")
        connection.execute(text("INSERT INTO users VALUES ('legacy-user','legacy@example.com',:hash,CURRENT_TIMESTAMP)"), {"hash": legacy_hash})
        connection.execute(text("INSERT INTO workspaces VALUES ('legacy-workspace','Existing workspace',CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO workspace_members VALUES ('legacy-workspace','legacy-user','owner')"))
        connection.execute(text("INSERT INTO user_sessions VALUES (:hash,'legacy-user',:expires)"),
                           {"hash": token_hash("old-cookie"), "expires": now() + timedelta(hours=12)})
        connection.execute(text("INSERT INTO installation_mode VALUES (1,'production')"))
        connection.execute(text("INSERT INTO projects (id,name,description,url,builtin,archived,created_at,updated_at,workspace_id,project_type,environment) "
            "VALUES ('legacy-project','Existing project','Private legacy description','https://qa.example',0,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'legacy-workspace','web','staging')"))
    upgrade(engine)
    upgrade(engine)
    with Session(engine) as session:
        assert session.get(UserRow, "legacy-user").password_hash == legacy_hash
        assert session.get(UserRow, "legacy-user").nickname == "legacy"
        assert session.get(WorkspaceRow, "legacy-workspace").owner_id == "legacy-user"
        member = session.get(MemberRow, ("legacy-workspace", "legacy-user"))
        assert member.id and member.created_at
        assert session.get(SessionRow, token_hash("old-cookie")).refresh_token_hash is None
    assert {"nickname", "is_active", "updated_at"} <= {col["name"] for col in inspect(engine).get_columns("users")}
    settings = Settings(_env_file=None, mode="production", env="test", secret_key=SECRET,
                        database_url=url, artifact_dir=tmp_path / "artifacts")
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        client.cookies.set("autoqa_session", "old-cookie")
        assert client.get("/api/projects").status_code == 401
        response = client.post("/api/auth/login", json={"email": "legacy@example.com", "password": PASSWORD})
        assert response.status_code == 200
        assert client.get("/api/projects").json()[0]["name"] == "Existing project"
        with Session(client.app.state.store.engine) as session:
            user = session.get(UserRow, "legacy-user")
            assert user.password_hash.startswith("scrypt$16384$8$5$") and check_password(PASSWORD, user.password_hash)
            assert user.updated_at.replace(tzinfo=timezone.utc) >= user.created_at.replace(tzinfo=timezone.utc)
    engine.dispose()
