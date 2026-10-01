"""Real Chromium acceptance against a user's HTTP fixture, with a fixture-only model transport."""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from sqlalchemy import delete
from sqlalchemy.orm import Session

from autoqa.app import create_app
from autoqa.config import Settings
from autoqa.product_models import OnboardingRow
from tests.test_product import KEY, SECRET, SPEC, api_plan, register, wait

WEB_HTML = b'''<!doctype html><html><body><h1>Customer app</h1><input id="username">
<input id="password" type="password"><button id="submit">Sign in</button><div id="result"></div>
<script>fetch('/onload', {method:'POST'}).catch(()=>{});
document.querySelector('#submit').onclick=()=>document.querySelector('#result').textContent='Order created';</script>
</body></html>'''


@pytest.fixture
def web_product(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(WEB_HTML)

        def log_message(self, *args):
            pass

        def do_POST(self):
            self.server.mutations += 1
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
    target = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    target.mutations = 0
    thread = threading.Thread(target=target.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{target.server_address[1]}"
    settings = Settings(_env_file=None, mode="production", env="test", secret_key=SECRET, allow_private_targets=True,
        database_url=f"sqlite:///{tmp_path / 'product.db'}", artifact_dir=tmp_path / "artifacts")
    app = create_app(settings)
    app.state.target_fixture = target
    plan = {"summary": "登录后创建订单", "scope": "Customer app", "preconditions": ["测试账号有效"], "steps": [
        {"action": "fill", "selector": "#username", "value": "{{USERNAME}}", "description": "填写账号"},
        {"action": "fill", "selector": "#password", "value": "{{PASSWORD}}", "description": "填写密码"},
        {"action": "click", "selector": "#submit", "description": "创建订单"},
        {"action": "assert_text", "selector": "#result", "value": "Order created", "description": "校验订单"}]}
    calls = []
    def handler(request):
        calls.append(request)
        if "/chat/completions" not in request.url.path:
            return httpx.Response(200, json={"id": 1})
        system = json.loads(request.content)["messages"][0]["content"]
        data = plan if "WebPlan" in system else api_plan() if "RealPlan" in system else {"ok": True}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(data)}}]})
    with TestClient(app, base_url="http://testserver") as client:
        app.state.gateway.transport_factory = lambda: httpx.MockTransport(handler)
        yield client, url, app, tmp_path, calls
    target.shutdown()
    target.server_close()
    thread.join(timeout=3)


def test_real_web_target_plan_edit_approval_browser_evidence_and_run_only_account(web_product):
    client, url, app, tmp_path, calls = web_product
    register(client)
    config = client.put("/api/provider", json={"provider": "compatible", "base_url": url, "api_key": KEY, "model": "fixture"})
    assert config.status_code == 200
    project = client.post("/api/projects", json={"name": "用户 Web 项目", "url": url, "project_type": "web"}).json()
    result = client.post("/api/tasks", json={"project_id": project["id"], "goal": "登录后创建订单",
        "session_secrets": {"USERNAME": "fixture-web-user", "PASSWORD": "fixture-web-password"}})
    assert result.status_code == 202
    task_id = result.json()["id"]
    review = wait(client, task_id, "awaiting_approval")
    assert review["steps"] == [] and len(calls) == 1
    assert app.state.target_fixture.mutations == 0
    assert "fixture-web-password" not in json.dumps(review)
    plan = review["plan"]
    plan["steps"][-1]["description"] = "人工修改后的订单断言"
    assert client.patch(f"/api/tasks/{task_id}/plan", json=plan).status_code == 200
    assert client.post(f"/api/tasks/{task_id}/approve", json={"approved": True}).status_code == 202
    final = wait(client, task_id, "passed")
    assert final["metrics"]["passed"] == 1 and final["steps"][-1]["step"]["description"] == "人工修改后的订单断言"
    # Terminal status is persisted before asynchronous browser/session cleanup finishes.
    deadline = time.monotonic() + 10
    while task_id in app.state.runner.transient and time.monotonic() < deadline:
        time.sleep(0.05)
    assert final["findings"] == [] and task_id not in app.state.runner.transient
    assert not list((tmp_path / "artifacts").rglob("*.zip"))
    assert not list((tmp_path / "artifacts").rglob("*.png"))
    for file in (tmp_path / "artifacts").rglob("*"):
        if file.is_file():
            assert b"fixture-web-password" not in file.read_bytes()
    assert b"fixture-web-password" not in (tmp_path / "product.db").read_bytes()


def test_real_web_requires_review_even_for_read_only_assertions(web_product):
    client, url, app, _, _ = web_product
    register(client)
    client.put("/api/provider", json={"provider": "compatible", "base_url": url, "api_key": KEY, "model": "fixture"})
    plan = {"summary": "只读断言", "scope": "页面标题", "preconditions": [], "steps": [
        {"action": "assert_text", "value": "Customer app", "description": "检查标题"}]}
    app.state.gateway.transport_factory = lambda: httpx.MockTransport(lambda r: httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(plan)}}]}))
    project = client.post("/api/projects", json={"name": "只读项目", "url": url}).json()
    response = client.post("/api/tasks", json={"project_id": project["id"], "goal": "检查标题"})
    task_id = response.json()["id"]
    assert wait(client, task_id, "awaiting_approval")["steps"] == []
    assert app.state.target_fixture.mutations == 0
    client.post(f"/api/tasks/{task_id}/approve", json={"approved": False})
    assert wait(client, task_id, "cancelled")["steps"] == []


def test_production_ui_empty_onboarding_language_and_model_no_seeds(web_product):
    _, target_url, app, tmp_path, _ = web_product
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    # The TestClient owns lifespan; the UI server uses the same initialized isolated application.
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    end = time.monotonic() + 10
    while not server.started and time.monotonic() < end:
        time.sleep(0.05)
    assert server.started
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1100})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}")
            page.get_by_role("button", name="注册", exact=True).click()
            page.locator('[name="email"]').fill("browser@example.com")
            page.locator('[name="password"]').fill("fixture-browser-password")
            page.get_by_role("button", name="注册", exact=True).click()
            # Verify the unchanged empty Dashboard for a pre-onboarding account.
            page.get_by_role("heading", name="欢迎使用 AutoQA Studio", exact=True).wait_for()
            with Session(app.state.store.engine) as session:
                session.execute(delete(OnboardingRow))
                session.commit()
            page.reload()
            page.get_by_role("heading", name="还没有项目").wait_for()
            body = page.locator("body").inner_text()
            assert "创建你的第一个测试项目" in body and "尚未配置模型服务" in body
            for value in ["Login API Quality Demo", "Authentication Regression Test", "5 passed", "1 failed", "Login Demo"]:
                assert value not in body
            assert page.locator('.metric strong').all_text_contents() == ["0"] * 8
            page.locator('#language').select_option("en-US")
            page.get_by_role("heading", name="No projects yet").wait_for()
            page.locator('#language').select_option("zh-CN")
            page.get_by_role("heading", name="还没有项目").wait_for()
            page.screenshot(path=str(tmp_path / "production-empty.png"), full_page=True)
            page.get_by_role("link", name="模型服务", exact=True).click()
            page.get_by_role("heading", name="模型服务", exact=True).wait_for()
            assert "DEEPSEEK_API_KEY" not in page.locator("body").inner_text()
            assert page.locator('[name="api_key"]').get_attribute("type") == "password"
            assert page.locator('#provider-select option').all_text_contents() == ["DeepSeek", "OpenAI", "OpenAI-compatible"]
            page.locator('#provider-select').select_option('compatible')
            page.locator('[name="base_url"]').fill(target_url)
            page.locator('[name="model"]').fill('fixture-model')
            page.locator('[name="api_key"]').fill(KEY)
            page.get_by_role('button', name='测试连接', exact=True).click()
            page.wait_for_function("() => document.querySelector('[name=api_key]').value === ''")
            page.locator('[name="api_key"]').fill(KEY)
            page.get_by_role('button', name='保存配置', exact=True).click()
            page.get_by_text('sk-••••••••1234', exact=True).wait_for()
            assert page.locator('[name="api_key"]').input_value() == ''
            page.get_by_role('link', name='项目', exact=True).click()
            page.get_by_role('button', name='创建项目', exact=True).click()
            page.locator('#project-form [name="name"]').fill('浏览器 API 项目')
            page.locator('#project-form [name="url"]').fill(target_url)
            page.locator('#project-form [name="project_type"]').select_option('api')
            page.locator('#project-form').get_by_role('button', name='保存', exact=True).click()
            page.get_by_role('heading', name='浏览器 API 项目', exact=True).wait_for()
            page.get_by_role('button', name='打开', exact=True).click()
            page.locator('#openapi-file').set_input_files({'name':'openapi.json','mimeType':'application/json',
                                                         'buffer':json.dumps(SPEC).encode()})
            page.wait_for_function("() => document.querySelector('#project-detail').textContent.includes('GET /users/{userId}')")
            page.get_by_role('button', name='创建测试任务', exact=True).click()
            page.locator('#task-form [name="goal"]').fill('检查用户查询结果')
            page.locator('[name="endpoint"][value="GET /users/{userId}"]').check()
            page.get_by_role('button', name='生成测试计划', exact=True).click()
            page.get_by_role('button', name='批准并执行', exact=True).wait_for(timeout=15000)
            page.get_by_role('button', name='增加用例', exact=True).click()
            page.get_by_role('button', name='保存修改后的计划', exact=True).click()
            page.wait_for_function("() => document.querySelectorAll('#case-controls .case-row').length === 2 && JSON.parse(document.querySelector('#plan-editor').value).steps.length === 2")
            page.get_by_role('button', name='批准并执行', exact=True).click()
            page.locator('h2 .badge.passed').wait_for(timeout=20000)
            with page.expect_download() as download:
                page.get_by_role('button', name='打开 / 导出 HTML 报告', exact=True).click()
            saved = tmp_path / 'product-ui-report.html'
            download.value.save_as(saved)
            assert '测试报告' in saved.read_text(encoding='utf-8')
            assert not errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
