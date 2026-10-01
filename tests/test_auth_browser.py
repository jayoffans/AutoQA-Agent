"""Chromium acceptance of registration, relogin, isolation and automatic refresh."""
import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from tests.test_product_browser import web_product as web_product  # noqa: F401 - isolated app fixture


@pytest.fixture
def auth_browser(web_product):
    _, target_url, app, _, _ = web_product
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1100})
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            yield page, context, f"http://127.0.0.1:{port}", target_url, errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def fill_auth(page, email, nickname=None):
    page.locator('#auth-form [name="email"]').fill(email)
    page.locator('#auth-form [name="password"]').fill("chromium-fixture-password")
    if nickname is not None:
        page.locator('#auth-form [name="nickname"]').fill(nickname)


def test_production_register_project_logout_relogin_second_user_isolation(auth_browser):
    page, context, url, target_url, errors = auth_browser
    output = Path("work/phase6-ui")
    output.mkdir(parents=True, exist_ok=True)
    page.goto(url)
    page.get_by_role("heading", name="AI 软件测试平台", exact=True).wait_for()
    assert page.get_by_role("button", name="登录", exact=True).is_visible()
    assert page.get_by_role("button", name="注册", exact=True).is_visible()
    page.screenshot(path=str(output / "landing.png"), full_page=True)
    page.get_by_role("button", name="注册", exact=True).click()
    fill_auth(page, "chromium-a@example.com", "张三")
    page.get_by_role("button", name="注册", exact=True).click()
    page.get_by_role("heading", name="欢迎回来：张三", exact=True).wait_for()
    page.get_by_role("heading", name="还没有项目", exact=True).wait_for()
    assert page.locator('.metric strong').all_text_contents() == ["0"] * 8
    assert "张三的工作空间" in page.locator("body").inner_text()
    assert "Login Demo" not in page.locator("body").inner_text()
    assert "从这里开始" not in page.locator("body").inner_text()
    page.screenshot(path=str(output / "empty-workspace.png"), full_page=True)
    page.get_by_role("button", name="创建你的第一个测试项目", exact=True).click()
    page.locator('#project-form [name="name"]').fill("用户 A 的私有项目")
    page.locator('#project-form [name="description"]').fill("用户 A 的私有描述")
    page.locator('#project-form [name="url"]').fill(target_url)
    page.locator('#project-form').get_by_role("button", name="保存", exact=True).click()
    page.get_by_role("heading", name="用户 A 的私有项目", exact=True).wait_for()
    owned = page.request.get(url + "/api/projects").json()[0]["id"]
    page.get_by_role("button", name="退出登录", exact=True).click()
    page.locator('#auth-form').wait_for()
    assert not any(cookie["name"].startswith("autoqa_") for cookie in context.cookies())
    fill_auth(page, "chromium-a@example.com")
    page.get_by_role("button", name="登录", exact=True).click()
    page.get_by_role("heading", name="欢迎回来：张三", exact=True).wait_for()
    page.get_by_role("link", name="项目", exact=True).click()
    page.get_by_role("heading", name="用户 A 的私有项目", exact=True).wait_for()
    page.get_by_role("button", name="退出登录", exact=True).click()
    page.locator('#auth-form').wait_for()
    page.get_by_role("button", name="注册", exact=True).click()
    fill_auth(page, "chromium-b@example.com", "李四")
    page.get_by_role("button", name="注册", exact=True).click()
    page.get_by_role("heading", name="欢迎回来：李四", exact=True).wait_for()
    page.get_by_role("heading", name="还没有项目", exact=True).wait_for()
    assert page.locator('.metric strong').all_text_contents() == ["0"] * 8
    assert "用户 A 的私有项目" not in page.locator("body").inner_text()
    assert page.request.get(url + "/api/projects/" + owned).status == 404
    assert page.request.get(url + "/api/projects").json() == []
    page.screenshot(path=str(output / "second-user.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.get_by_role("button", name="退出登录", exact=True).is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(output / "mobile-workspace.png"), full_page=True)
    assert not errors
    (output / "results.json").write_text(json.dumps({
        "browser": "Chromium", "production_login_page": "passed", "registration": "passed",
        "empty_workspace": "passed", "create_project": "passed", "logout": "passed",
        "relogin_project_persisted": "passed", "second_user_isolation": "passed",
        "mobile_logout": "passed", "page_errors": errors
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def test_browser_refresh_and_returning_session_after_access_cookie_expiry(auth_browser):
    page, context, url, _, errors = auth_browser
    page.goto(url)
    page.get_by_role("button", name="注册", exact=True).click()
    fill_auth(page, "refresh-browser@example.com", "刷新用户")
    page.get_by_role("button", name="注册", exact=True).click()
    page.get_by_role("heading", name="欢迎回来：刷新用户", exact=True).wait_for()
    # Simulate the browser discarding an expired access cookie, preserving refresh + CSRF binding.
    for reopen in (False, True):
        kept = [cookie for cookie in context.cookies() if cookie["name"] != "autoqa_access"]
        context.clear_cookies()
        context.add_cookies(kept)
        if reopen:
            page.reload()
        else:
            page.get_by_role("button", name="刷新", exact=True).click()
        page.wait_for_function("() => document.querySelector('#content')?.textContent.includes('欢迎回来：刷新用户')")
        # A business read proves that renewal completed, rather than relying on stale page text.
        deadline = time.monotonic() + 10
        while page.request.get(url + "/api/projects").status != 200 and time.monotonic() < deadline:
            page.wait_for_timeout(50)
        assert page.request.get(url + "/api/projects").status == 200
    page.get_by_role("button", name="退出登录", exact=True).click()
    page.locator('#auth-form').wait_for()
    assert page.request.get(url + "/api/projects").status == 401
    assert not errors
