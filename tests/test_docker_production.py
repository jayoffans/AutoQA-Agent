"""Opt-in real Docker smoke, injected configuration and named-volume acceptance."""
import json
import os
import secrets
import shutil
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx
import pytest
import yaml
from playwright.sync_api import sync_playwright
from sqlalchemy import URL

from tests.onboarding_support import complete_onboarding
from tests.production_support import (
    ROOT,
    MockServices,
    PrivateContext,
    checked_process,
    clean_env,
    completed_task,
    mask_ci,
    random_key,
    register,
    tls_files,
)


@pytest.fixture(scope="module")
def docker_product(tmp_path_factory):
    if os.getenv("AUTOQA_RUN_DOCKER") != "1":
        pytest.skip("Set AUTOQA_RUN_DOCKER=1 to build and run disposable production containers")
    assert shutil.which("docker"), "Docker is required for this acceptance run"
    checked_process(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=30)
    directory = tmp_path_factory.mktemp("docker-production")
    project = "autoqa-compat-" + secrets.token_hex(6)
    image = project + ":test"
    root_password, db_password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    mask_ci(root_password, db_password)
    env = clean_env(AUTOQA_MODE="production", AUTOQA_ENV="production", AUTOQA_SECRET_KEY=random_key(),
        DATABASE_URL=URL.create("mysql+pymysql", username="compat", password=db_password, host="mysql",
            database="test_autoqa", query={"charset": "utf8mb4"}).render_as_string(False),
        MYSQL_DATABASE="test_autoqa", MYSQL_USER="compat", MYSQL_PASSWORD=db_password,
        MYSQL_ROOT_PASSWORD=root_password, AUTOQA_DATA_DIR="/app/data/compat",
        AUTOQA_ARTIFACT_DIR="/app/artifacts/compat", AUTOQA_ALLOW_PRIVATE_TARGETS="true", PORT="0")
    # CI builds the actual Dockerfile, not an in-process stand-in.
    checked_process(["docker", "build", "--tag", image, "."], env=env, timeout=600)
    override = directory / "override.yaml"
    override.write_text(yaml.safe_dump({"services": {"autoqa": {
        "image": image, "extra_hosts": ["host.docker.internal:host-gateway"],
        "environment": {"LLM_PROVIDER": "mock"}}}}))
    compose = ["docker", "compose", "--env-file", str(directory / "empty.env"), "-p", project,
               "-f", str(ROOT / "compose.production.yaml"), "-f", str(override)]
    (directory / "empty.env").write_text("")
    upstream = {"url": None}

    class Proxy(BaseHTTPRequestHandler):
        def forward(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            headers = {k: v for k, v in self.headers.items() if k.lower() not in {"host", "connection"}}
            response = httpx.request(self.command, upstream["url"] + self.path, content=body,
                headers=headers, timeout=30, follow_redirects=False, trust_env=False)
            self.send_response(response.status_code)
            for key, value in response.headers.multi_items():
                if key.lower() not in {"transfer-encoding", "connection", "content-length", "content-encoding"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = forward

        def log_message(self, *args):
            pass

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    key, cert = tls_files(directory)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    proxy.socket = tls.wrap_socket(proxy.socket, server_side=True)
    env["AUTOQA_PUBLIC_BASE_URL"] = f"https://localhost:{proxy.server_port}"
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)

    def command(*args, timeout=180):
        return checked_process([*compose, *args], env=env, timeout=timeout)

    def ready():
        address = command("port", "autoqa", "8765").decode().strip()
        upstream["url"] = "http://" + address
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                if httpx.get(upstream["url"] + "/health", timeout=2).json() == {"status": "ok"}:
                    return
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.5)
        raise AssertionError("Production container failed health acceptance")

    try:
        command("config", "--quiet")
        command("up", "-d", "--no-build", "--wait", timeout=240)
        ready()
        thread.start()
        with MockServices(host="0.0.0.0") as fixture:
            target = f"http://host.docker.internal:{fixture.server.server_port}"
            with httpx.Client(base_url=env["AUTOQA_PUBLIC_BASE_URL"], verify=False, timeout=30) as client:
                yield PrivateContext(client=client, command=command, ready=ready, env=env, target=target,
                    url=env["AUTOQA_PUBLIC_BASE_URL"], compose=compose, project=project, fixture_calls=fixture.calls)
    finally:
        if thread.is_alive():
            proxy.shutdown()
            thread.join(timeout=5)
        proxy.server_close()
        # Only volumes and containers under this unique acceptance project are removed.
        command("down", "--volumes", "--remove-orphans")
        checked_process(["docker", "image", "rm", image], timeout=30)


def test_docker_build_container_nonroot_and_health(docker_product):
    live = docker_product
    assert live["client"].get("/health").json() == {"status": "ok"}
    uid = live["command"]("exec", "-T", "autoqa", "id", "-u").decode().strip()
    assert uid == "10001"
    container = live["command"]("ps", "-q", "autoqa").decode().strip()
    status = checked_process(["docker", "inspect", "--format", "{{.State.Health.Status}}", container]).decode().strip()
    assert status == "healthy"
    current = live["command"]("exec", "-T", "autoqa", "python", "-m", "alembic", "current").decode()
    assert "0011_alpha_hardening" in current


def test_docker_all_required_environment_variables_reach_settings(docker_product):
    live = docker_product
    # Return booleans only: docker inspect/config must never expose credentials in output.
    code = ("import os,json; from autoqa.config import Settings; s=Settings(); "
        "print(json.dumps({'mode':s.mode==os.environ['AUTOQA_MODE']=='production',"
        "'env':s.env==os.environ['AUTOQA_ENV']=='production',"
        "'key':s.secret_key==os.environ['AUTOQA_SECRET_KEY'],"
        "'db':s.database_url==os.environ['DATABASE_URL'],"
        "'data':str(s.data_dir)==os.environ['AUTOQA_DATA_DIR'],"
        "'artifacts':str(s.artifact_dir)==os.environ['AUTOQA_ARTIFACT_DIR']}))")
    result = json.loads(live["command"]("exec", "-T", "autoqa", "python", "-c", code))
    assert len(result) == 6 and all(result.values())


def test_named_volumes_survive_down_up_container_recreation(docker_product):
    live, client = docker_product, docker_product["client"]
    signed, email, password = register(client)
    workspace = client.get("/api/auth/me").json()["workspace_id"]
    item, task_id, _ = completed_task(client, live["target"])
    report = client.get(f"/api/tasks/{task_id}/report").content
    artifact = client.get(f"/api/tasks/{task_id}/artifacts/report.html").content
    ids = {key: str(uuid4()) for key in ('spec', 'endpoint', 'plan', 'case', 'run', 'execution')}
    ids.update(workspace=workspace, user=signed['user']['id'], project=item['id'], target=live['target'])
    # Populate an API execution fixture directly; preparing consent must never invoke its Provider.
    code = f"""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from autoqa.config import Settings
from autoqa.store_base import now
from autoqa.studio_models import ReportRow
from autoqa.product_models import ApiSpecRow,ApiEndpointRow,TestPlan,TestCase,TestRun,TestExecution
ids={ids!r}
engine=create_engine(Settings().database_url,hide_parameters=True)
with Session(engine) as s:
    s.add(ApiSpecRow(id=ids['spec'],workspace_id=ids['workspace'],project_id=ids['project'],name='fixture',version='1',format='json',raw_content='{{}}'))
    s.flush()
    s.add(ApiEndpointRow(id=ids['endpoint'],workspace_id=ids['workspace'],project_id=ids['project'],spec_id=ids['spec'],method='GET',path='/users/1',summary='',parameters=[],request_schema={{}},response_schema={{}}))
    s.add(TestPlan(id=ids['plan'],workspace_id=ids['workspace'],project_id=ids['project'],api_spec_id=ids['spec'],status='approved',provider='compatible',model='mock-http-fixture',created_by=ids['user']))
    s.flush()
    s.add(TestCase(id=ids['case'],workspace_id=ids['workspace'],plan_id=ids['plan'],endpoint_id=ids['endpoint'],name='candidate fixture',category='positive',priority='P1',description='',steps=[],expected_result='candidate only'))
    s.add(TestRun(id=ids['run'],workspace_id=ids['workspace'],project_id=ids['project'],plan_id=ids['plan'],status='failed',total_cases=1,passed_cases=0,failed_cases=1,created_by=ids['user'],started_at=now(),finished_at=now()))
    s.flush()
    s.add(TestExecution(id=ids['execution'],workspace_id=ids['workspace'],plan_id=ids['plan'],run_id=ids['run'],case_id=ids['case'],method='GET',url=ids['target']+'/users/1',request_data={{}},response_status=400,response_body='{{"ok":false}}',passed=False,status='failed',duration_ms=1,result_data={{'version':1,'expected_status':200,'assertion_results':[{{'type':'status','expected':200,'actual':400,'status':'failed','reason_code':'status_mismatch'}}]}}))
    s.commit()
engine.dispose()
"""
    live['command']('exec', '-T', 'autoqa', 'python', '-c', code)
    api_path = '/api/test-runs/' + ids['run']
    api_report = client.get(api_path + '/report').json()
    call_count = len(live['fixture_calls'])
    prepared = client.post(api_path + '/analysis-intents', json={'execution_ids': [ids['execution']]})
    assert prepared.status_code == 201
    intent = prepared.json()
    interrupted = client.post(api_path + '/analysis-intents', json={'execution_ids': [ids['execution']], 'refresh': True})
    assert interrupted.status_code == 201 and len(live['fixture_calls']) == call_count
    code = f"""
from datetime import timedelta
from sqlalchemy import create_engine,update
from sqlalchemy.orm import Session
from autoqa.config import Settings
from autoqa.product_models import AnalysisAudit
from autoqa.studio_models import ReportRow
from autoqa.store_base import now
engine=create_engine(Settings().database_url,hide_parameters=True)
with Session(engine) as s:
    s.execute(update(AnalysisAudit).where(AnalysisAudit.id=={interrupted.json()['intent_id']!r}).values(status='invoking',active_run_id={ids['run']!r},request_count=None,confirmed_at=now(),dispatch_at=now(),expires_at=now()-timedelta(seconds=1)))
    s.commit()
engine.dispose()
"""
    live['command']('exec', '-T', 'autoqa', 'python', '-c', code)
    marker = "compatibility-volume-marker"
    live["command"]("exec", "-T", "autoqa", "python", "-c",
        "from autoqa.config import Settings; s=Settings(); s.data_dir.mkdir(parents=True,exist_ok=True); "
        f"(s.data_dir/'compat.txt').write_text('{marker}')")
    before = live["command"]("ps", "-q", "autoqa").decode().strip()
    mysql_before = live["command"]("ps", "-q", "mysql").decode().strip()
    live["command"]("down")  # Keep all named volumes, discard both containers.
    live["command"]("up", "-d", "--no-build", "--wait", timeout=240)
    live["ready"]()
    assert live["command"]("ps", "-q", "autoqa").decode().strip() != before
    assert live["command"]("ps", "-q", "mysql").decode().strip() != mysql_before
    assert client.get("/api/auth/me").json()["workspace_id"] == workspace
    assert client.get('/api/projects/' + item["id"]).json()["id"] == item["id"]
    assert client.get(f"/api/tasks/{task_id}/report").content == report
    assert client.get(f"/api/tasks/{task_id}/artifacts/report.html").content == artifact
    assert client.get(api_path + '/report').json() == api_report
    assert client.get(api_path + '/analysis-intents/' + intent['intent_id']).json() == intent
    recovered = client.get(api_path + '/analysis-intents/' + interrupted.json()['intent_id']).json()
    assert recovered['status'] == 'unknown' and recovered['request_count'] is None
    assert client.post(api_path + '/analyze', json={'intent_id': interrupted.json()['intent_id']}).status_code == 409
    assert len(live['fixture_calls']) == call_count
    persisted = live["command"]("exec", "-T", "autoqa", "python", "-c",
        "from autoqa.config import Settings; print((Settings().data_dir/'compat.txt').read_text())").decode().strip()
    assert persisted == marker
    assert client.post("/api/auth/refresh", json={"refresh_token": signed["refresh_token"]}).status_code == 200
    assert client.post("/api/auth/login", json={"email": email, "password": password}).status_code == 200
    logs = live["command"]("logs", "--no-color").decode()
    for value in (password, signed["access_token"], signed["refresh_token"], live["env"]["AUTOQA_SECRET_KEY"],
                  live["env"]["MYSQL_PASSWORD"], live["env"]["MYSQL_ROOT_PASSWORD"]):
        assert value not in logs


def test_docker_https_browser_registration_reload_logout_relogin(docker_product):
    live = docker_product
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            email, password = secrets.token_hex(8) + "@example.com", secrets.token_urlsafe(24)
            page.goto(live["url"])
            page.get_by_role("button", name="注册", exact=True).click()
            page.locator('#auth-form [name="email"]').fill(email)
            page.locator('#auth-form [name="password"]').fill(password)
            page.locator('#auth-form [name="nickname"]').fill("容器用户")
            page.get_by_role("button", name="注册", exact=True).click()
            complete_onboarding(page)
            page.get_by_role("link", name="项目", exact=True).click()
            page.get_by_role("button", name="创建项目", exact=True).click()
            page.locator('#project-form [name="name"]').fill("容器浏览器项目")
            page.locator('#project-form [name="url"]').fill(live["target"])
            page.locator('#project-form').get_by_role("button", name="保存", exact=True).click()
            page.get_by_role("heading", name="容器浏览器项目", exact=True).wait_for()
            page.reload()
            page.get_by_role("heading", name="容器浏览器项目", exact=True).wait_for()
            page.get_by_role("button", name="退出登录", exact=True).click()
            page.locator('#auth-form').wait_for()
            page.locator('#auth-form [name="email"]').fill(email)
            page.locator('#auth-form [name="password"]').fill(password)
            page.get_by_role("button", name="登录", exact=True).click()
            page.get_by_role("link", name="项目", exact=True).click()
            page.get_by_role("heading", name="容器浏览器项目", exact=True).wait_for()
            assert not errors
            output = ROOT / "work/production-compat"
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output / "browser-docker.png"), full_page=True)
        finally:
            browser.close()
