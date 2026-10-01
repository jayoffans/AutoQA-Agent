"""Chinese BYOK product flow in Chromium using only disposable fake credentials."""
from pathlib import Path

import httpx
from playwright.sync_api import expect

from tests.onboarding_support import complete_onboarding
from tests.test_auth import sign_up
from tests.test_auth_browser import auth_browser as auth_browser  # noqa: F401
from tests.test_onboarding_browser import register_browser
from tests.test_product import KEY
from tests.test_product_browser import web_product as web_product  # noqa: F401

OUTPUT = Path("work/phase6-2b-ui")


def capture(page, filename):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUTPUT / filename), full_page=True)


def open_dialog(page, target, model="fixture-model"):
    page.get_by_role("button", name="添加模型", exact=True).click()
    expect(page.locator('#provider-select option')).to_have_text(["DeepSeek", "OpenAI", "OpenAI 兼容服务"])
    page.locator('#provider-select').select_option("compatible")
    page.get_by_label("服务地址", exact=True).fill(target)
    page.get_by_label("模型名称", exact=True).fill(model)
    page.get_by_label("API 密钥", exact=True).fill(KEY)
    assert page.get_by_label("API 密钥", exact=True).get_attribute("type") == "password"
    assert page.get_by_label("保存到工作区", exact=True).is_checked()


def save_dialog(page):
    page.get_by_role("button", name="保存配置", exact=True).click()
    expect(page.locator('#dialog')).not_to_be_visible()


def dashboard(page, url):
    register_browser(page, url, "byok-browser@example.com")
    complete_onboarding(page)
    page.get_by_role("link", name="模型服务", exact=True).click()
    page.get_by_role("heading", name="模型服务", exact=True).wait_for()


def test_onboarding_model_center_save_advances_without_executing(auth_browser):
    page, _, url, target, errors = auth_browser
    register_browser(page, url)
    page.get_by_role("button", name="开始设置", exact=True).click()
    page.get_by_role("button", name="配置模型", exact=True).click()
    expect(page).to_have_url(url + "/#settings/providers")
    page.get_by_role("heading", name="模型服务", exact=True).wait_for()
    open_dialog(page, target)
    capture(page, "add-model.png")
    save_dialog(page)
    page.get_by_role("heading", name="创建第一个测试项目", exact=True).wait_for()
    state = page.request.get(url + "/api/onboarding").json()
    assert state["model_configured"] and state["step"] == 3 and not state["first_test_completed"]
    assert page.request.get(url + "/api/tasks").json() == page.request.get(url + "/api/projects").json() == []
    page.reload()
    page.get_by_role("heading", name="创建第一个测试项目", exact=True).wait_for()
    assert not errors


def test_model_list_connection_dashboard_default_switch_and_delete(auth_browser):
    page, _, url, target, errors = auth_browser
    dashboard(page, url)
    open_dialog(page, target)
    save_dialog(page)
    card = page.locator('.provider-card').first
    expect(card.get_by_text("sk-********1234", exact=True)).to_be_visible()
    expect(card.get_by_text("待测试连接", exact=True)).to_be_visible()
    assert KEY not in page.locator("body").inner_text() + page.locator("body").inner_html()
    card.get_by_role("button", name="测试连接", exact=True).click()
    expect(card.get_by_text("连接成功", exact=True)).to_be_visible()
    page.get_by_role("link", name="工作台", exact=True).click()
    expect(page.get_by_text("✓ 模型已连接", exact=False)).to_be_visible()
    page.get_by_role("link", name="模型服务", exact=True).click()
    open_dialog(page, target, "second-fixture")
    save_dialog(page)
    expect(page.locator('.provider-card')).to_have_count(2)
    first = page.locator('.provider-card').filter(has_text="fixture-model")
    first.get_by_role("button", name="设为默认", exact=True).click()
    expect(first.get_by_text("默认模型", exact=True)).to_be_visible()
    capture(page, "model-center-desktop.png")
    page.on("dialog", lambda dialog: dialog.accept())
    first.get_by_role("button", name="删除", exact=True).click()
    expect(page.locator('.provider-card')).to_have_count(1)
    expect(page.locator('.provider-card').get_by_text("默认模型", exact=True)).to_be_visible()
    page.locator('.provider-card').get_by_role("button", name="删除", exact=True).click()
    expect(page.locator('.provider-card')).to_have_count(0)
    page.get_by_role("link", name="工作台", exact=True).click()
    expect(page.get_by_text("尚未配置模型服务", exact=False)).to_be_visible()
    assert page.request.get(url + "/api/tasks").json() == [] and not errors


def test_model_dialog_cancel_and_failure_clear_key(auth_browser, web_product):
    page, _, url, target, errors = auth_browser
    _, _, app, _, _ = web_product
    dashboard(page, url)
    open_dialog(page, target)
    page.keyboard.press("Escape")
    assert page.locator('[name="api_key"]').count() == 0
    open_dialog(page, target)
    app.state.gateway.transport_factory = lambda: httpx.MockTransport(lambda request: httpx.Response(
        401, json={"error": KEY, "stack": "Traceback fixture"}))
    page.get_by_role("button", name="测试连接", exact=True).click()
    expect(page.locator('[name="api_key"]')).to_have_value("")
    expect(page.locator('#toast')).to_have_text("连接失败，请检查服务地址、密钥、模型和余额")
    assert KEY not in page.locator("body").inner_text()
    page.get_by_role("button", name="取消", exact=True).click()
    assert page.locator('[name="api_key"]').count() == 0
    assert page.request.get(url + "/api/settings/providers").json() == [] and not errors


def test_provider_page_workspace_switch_isolates_and_member_view_readonly(auth_browser, web_product):
    page, _, url, target, errors = auth_browser
    client, _, _, _, _ = web_product
    dashboard(page, url)
    open_dialog(page, target, "private-workspace-a")
    save_dialog(page)
    own = page.request.get(url + "/api/auth/me").json()
    other = sign_up(client, "byok-owner-b@example.com", "工作区 B")
    other_client = client
    other_client.headers["Authorization"] = "Bearer " + other["access_token"]
    response = other_client.post("/api/settings/providers", json={"provider": "compatible",
        "model": "private-workspace-b", "base_url": target, "api_key": "sk-fake-other-5678"})
    assert response.status_code == 201
    assert other_client.post("/api/workspace/members", json={"email": own["email"]}).status_code == 201
    page.reload()
    page.get_by_role("heading", name="模型服务", exact=True).wait_for()
    page.locator('#workspace-selector').select_option(other["onboarding"]["workspace_id"])
    expect(page.locator('.provider-card')).to_have_count(1)
    expect(page.locator('.provider-card').get_by_text("private-workspace-b", exact=True)).to_be_visible()
    assert "private-workspace-a" not in page.locator("body").inner_text()
    assert page.get_by_role("button", name="添加模型", exact=True).count() == 0
    assert page.get_by_role("button", name="测试连接", exact=True).count() == 0
    assert page.get_by_role("button", name="删除", exact=True).count() == 0
    page.locator('#workspace-selector').select_option(own["workspace_id"])
    expect(page.locator('.provider-card').get_by_text("private-workspace-a", exact=True)).to_be_visible()
    assert "private-workspace-b" not in page.locator("body").inner_text() and not errors


def test_model_center_mobile_and_no_browser_secret_storage(auth_browser):
    page, _, url, target, errors = auth_browser
    dashboard(page, url)
    open_dialog(page, target)
    save_dialog(page)
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.locator('.provider-card')).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert KEY not in page.evaluate("JSON.stringify({...localStorage,...sessionStorage})")
    capture(page, "model-center-mobile.png")
    open_dialog(page, target)
    capture(page, "add-model-mobile.png")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth") and not errors
