"""Real Chromium coverage of new users, persisted setup and legacy navigation."""
from pathlib import Path

import pytest
from playwright.sync_api import expect
from sqlalchemy import delete
from sqlalchemy.orm import Session

from autoqa.product_models import OnboardingRow
from tests.onboarding_support import complete_onboarding
from tests.test_auth_browser import auth_browser as auth_browser  # noqa: F401 - isolated live browser
from tests.test_auth_browser import fill_auth
from tests.test_product_browser import web_product as web_product  # noqa: F401 - fixture dependency

OUTPUT = Path("work/phase6-2a-ui")


def register_browser(page, url, email="onboard-browser@example.com"):
    page.goto(url)
    page.get_by_role("button", name="注册", exact=True).click()
    fill_auth(page, email, "首次用户")
    page.get_by_role("button", name="注册", exact=True).click()
    page.get_by_role("heading", name="欢迎使用 AutoQA Studio", exact=True).wait_for()


def capture(page, name):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUTPUT / name), full_page=True)


def test_registration_enters_chinese_onboarding_and_blocks_dashboard_deep_link(auth_browser):
    page, _, url, _, errors = auth_browser
    register_browser(page, url)
    expect(page).to_have_url(url + "/#onboarding")
    assert page.locator("html").get_attribute("lang") == "zh-CN"
    assert page.get_by_text("AI 软件质量工程平台", exact=True).is_visible()
    assert page.get_by_text("首次用户的工作空间", exact=True).is_visible()
    assert page.request.get(url + "/api/projects").json() == []
    page.goto(url + "/#dashboard")
    page.get_by_role("heading", name="欢迎使用 AutoQA Studio", exact=True).wait_for()
    expect(page).to_have_url(url + "/#onboarding")
    capture(page, "welcome.png")
    assert not errors


def test_model_entry_opens_center_and_skip_keeps_unconfigured(auth_browser):
    page, _, url, _, errors = auth_browser
    register_browser(page, url)
    page.get_by_role("button", name="开始设置", exact=True).click()
    page.get_by_role("button", name="配置模型", exact=True).click()
    page.get_by_role("heading", name="模型服务", exact=True).wait_for()
    expect(page).to_have_url(url + "/#settings/providers")
    expect(page.get_by_role("button", name="添加模型", exact=True)).to_be_visible()
    assert page.locator('[name="api_key"]').count() == 0
    assert not page.request.get(url + "/api/onboarding").json()["model_configured"]
    capture(page, "model.png")
    page.get_by_role("button", name="返回引导", exact=True).click()
    page.get_by_role("button", name="稍后设置", exact=True).click()
    page.get_by_role("heading", name="创建第一个测试项目", exact=True).wait_for()
    assert not page.request.get(url + "/api/onboarding").json()["model_configured"]
    assert not errors


@pytest.mark.parametrize("kind", ["api", "web"])
def test_complete_onboarding_saves_project_and_dashboard_progress(auth_browser, kind):
    page, _, url, _, errors = auth_browser
    register_browser(page, url)
    page.get_by_role("button", name="开始设置", exact=True).click()
    page.get_by_role("button", name="稍后设置", exact=True).click()
    page.locator('#onboarding-project-form [name="name"]').fill("第一个质量项目")
    page.locator('#onboarding-project-form [name="project_type"]').select_option(kind)
    capture(page, f"project-{kind}.png")
    page.get_by_role("button", name="创建项目", exact=True).click()
    page.get_by_role("heading", name="准备完成", exact=True).wait_for()
    capture(page, "ready.png")
    page.get_by_role("button", name="进入工作台", exact=True).click()
    page.get_by_role("heading", name="首次使用进度", exact=True).wait_for()
    expect(page).to_have_url(url + "/#dashboard")
    items = page.locator('.first-use-progress li')
    assert items.count() == 5 and page.locator('.first-use-progress li.done').count() == 3
    assert "配置模型" in items.nth(2).inner_text() and "待完成" in items.nth(2).inner_text()
    assert "第一次测试" in items.nth(4).inner_text() and "待完成" in items.nth(4).inner_text()
    state = page.request.get(url + "/api/onboarding").json()
    assert state["onboarding_completed"] and not state["first_test_completed"]
    item = page.request.get(url + "/api/projects").json()[0]
    assert item["project_type"] == kind and item["name"] == "第一个质量项目"
    capture(page, "dashboard.png")
    page.reload()
    page.get_by_role("heading", name="首次使用进度", exact=True).wait_for()
    expect(page).to_have_url(url + "/#dashboard")
    page.get_by_role("link", name="项目", exact=True).click()
    page.get_by_role("heading", name="第一个质量项目", exact=True).wait_for()
    assert page.get_by_text("待添加测试目标", exact=True).is_visible()
    assert not errors


def test_refresh_resumes_each_saved_step_without_duplicate_projects(auth_browser):
    page, _, url, _, errors = auth_browser
    register_browser(page, url)
    for heading, action in [("欢迎使用 AutoQA Studio", "开始设置"), ("配置你的 AI 模型", "稍后设置")]:
        page.reload()
        page.get_by_role("heading", name=heading, exact=True).wait_for()
        page.get_by_role("button", name=action, exact=True).click()
    page.reload()
    page.get_by_role("heading", name="创建第一个测试项目", exact=True).wait_for()
    page.locator('#onboarding-project-form [name="name"]').fill("保持状态的项目")
    page.get_by_role("button", name="创建项目", exact=True).click()
    page.get_by_role("heading", name="准备完成", exact=True).wait_for()
    page.reload()
    page.get_by_role("heading", name="准备完成", exact=True).wait_for()
    assert len(page.request.get(url + "/api/projects").json()) == 1
    page.get_by_role("button", name="进入工作台", exact=True).click()
    page.reload()
    page.get_by_role("heading", name="欢迎回来：首次用户", exact=True).wait_for()
    assert len(page.request.get(url + "/api/projects").json()) == 1
    assert not errors


def test_existing_user_direct_dashboard_even_with_onboarding_hash(auth_browser, web_product):
    page, _, url, _, errors = auth_browser
    register_browser(page, url)
    app = web_product[2]
    with Session(app.state.store.engine) as session:
        session.execute(delete(OnboardingRow))
        session.commit()
    page.goto(url + "/#onboarding")
    page.reload()
    page.get_by_role("heading", name="欢迎回来：首次用户", exact=True).wait_for()
    expect(page).to_have_url(url + "/#dashboard")
    assert page.locator('.setup-shell').count() == 0
    assert page.locator('.metric strong').all_text_contents() == ["0"] * 8
    page.get_by_role("button", name="退出登录", exact=True).click()
    fill_auth(page, "onboard-browser@example.com")
    page.get_by_role("button", name="登录", exact=True).click()
    page.get_by_role("heading", name="欢迎回来：首次用户", exact=True).wait_for()
    expect(page).to_have_url(url + "/#dashboard")
    assert not errors


def test_mobile_and_i18n_onboarding(auth_browser):
    page, _, url, _, errors = auth_browser
    page.set_viewport_size({"width": 390, "height": 844})
    register_browser(page, url)
    capture(page, "mobile.png")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.locator('#language').select_option("en-US")
    page.get_by_role("heading", name="Welcome to AutoQA Studio", exact=True).wait_for()
    page.get_by_role("button", name="Start setup", exact=True).click()
    page.get_by_role("heading", name="Configure your AI model", exact=True).wait_for()
    page.locator('#language').select_option("zh-CN")
    page.get_by_role("heading", name="配置你的 AI 模型", exact=True).wait_for()
    assert page.request.get(url + "/api/onboarding").json()["step"] == 2
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not errors


def test_switching_accounts_clears_onboarding_and_project_state(auth_browser):
    page, _, url, _, errors = auth_browser
    register_browser(page, url, "switch-a@example.com")
    complete_onboarding(page, "用户 A 的初始项目")
    page.get_by_role("button", name="退出登录", exact=True).click()
    register_browser(page, url, "switch-b@example.com")
    assert page.request.get(url + "/api/onboarding").json()["step"] == 1
    assert page.request.get(url + "/api/projects").json() == []
    assert "用户 A 的初始项目" not in page.locator('body').inner_text()
    assert not errors
