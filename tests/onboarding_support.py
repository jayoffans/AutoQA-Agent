"""Shared Chromium setup steps for real product acceptance."""
from playwright.sync_api import expect


def complete_onboarding(page, name="首次测试项目", kind="api"):
    page.get_by_role("heading", name="欢迎使用 AutoQA Studio", exact=True).wait_for()
    page.get_by_role("button", name="开始设置", exact=True).click()
    page.get_by_role("button", name="稍后设置", exact=True).click()
    page.locator('#onboarding-project-form [name="name"]').fill(name)
    page.locator('#onboarding-project-form [name="project_type"]').select_option(kind)
    page.get_by_role("button", name="创建项目", exact=True).click()
    page.get_by_role("heading", name="准备完成", exact=True).wait_for()
    page.get_by_role("button", name="进入工作台", exact=True).click()
    page.get_by_role("heading", name="工作台", exact=True).wait_for()
    expect(page.locator('.metric strong').first).to_have_text("1")
