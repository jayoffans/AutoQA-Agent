"""Chromium verifies the input-only workflow, deletion, reload and escaped content."""
import copy
import json
from pathlib import Path

from playwright.sync_api import expect

from tests.onboarding_support import complete_onboarding
from tests.test_api_import import SPEC
from tests.test_auth_browser import auth_browser as auth_browser  # noqa: F401
from tests.test_onboarding_browser import register_browser
from tests.test_product_browser import web_product as web_product  # noqa: F401

OUTPUT = Path("work/phase6-2c-1a-ui")


def open_import(page, url):
    register_browser(page, url, "api-import-browser@example.com")
    complete_onboarding(page)
    page.get_by_role("link", name="项目", exact=True).click()
    page.get_by_role("button", name="打开", exact=True).click()
    page.get_by_role("button", name="API 测试", exact=True).click()
    page.locator('#api-spec-upload-form').wait_for()


def test_browser_upload_json_reload_delete_without_model(auth_browser, web_product):
    page, _, url, _, errors = auth_browser
    _, _, _, _, calls = web_product
    open_import(page, url)
    expect(page.get_by_role("button", name="生成测试计划", exact=True)).to_be_disabled()
    assert not page.get_by_role("button", name="配置模型", exact=True).count()
    page.locator('#api-spec-upload-form input').set_input_files({
        "name": "openapi.json", "mimeType": "application/json", "buffer": json.dumps(SPEC).encode()})
    page.locator('#api-spec-upload-form').get_by_role("button", name="上传接口文档", exact=True).click()
    expect(page.locator('.api-import-success')).to_have_text("解析完成 · 接口数量: 2")
    expect(page.locator('.api-endpoint-table tbody tr')).to_have_count(2)
    expect(page.locator('.api-endpoint-table tbody tr').first).to_contain_text("POST")
    expect(page.locator('.api-endpoint-table tbody tr').first).to_contain_text("/login")
    page.locator('.api-endpoint-table tbody tr').nth(1).get_by_text("参数信息 (3)", exact=True).click()
    expect(page.locator('.api-endpoint-table tbody tr').nth(1)).to_contain_text("x-token")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUTPUT / "api-import-desktop.png"), full_page=True)
    page.reload()
    expect(page.locator('.api-endpoint-table tbody tr')).to_have_count(2)
    assert "/api-testing" in page.url
    page.on("dialog", lambda dialog: dialog.accept())
    page.locator('.api-spec-list').get_by_role("button", name="删除", exact=True).click()
    expect(page.locator('.api-spec-list')).to_have_count(0)
    expect(page.get_by_text("还没有接口文档，上传文档后即可查看接口。", exact=True)).to_be_visible()
    assert page.request.get(url + "/api/tasks").json() == [] and calls == [] and not errors


def test_browser_yaml_invalid_upload_xss_and_mobile(auth_browser):
    page, _, url, _, errors = auth_browser
    open_import(page, url)
    page.locator('#api-spec-upload-form input').set_input_files({"name": "invalid.yaml", "mimeType": "application/yaml",
        "buffer": b"openapi: 3.0.3\npaths: {}"})
    page.locator('#api-spec-upload-form').get_by_role("button", name="上传接口文档", exact=True).click()
    expect(page.locator('#toast')).to_contain_text("无法解析接口规范")
    expect(page.locator('.api-endpoint-table')).to_have_count(0)
    spec = copy.deepcopy(SPEC)
    spec["info"]["title"] = '<img src=x onerror="window.importExecuted=true">'
    spec["paths"]["/login"]["post"]["summary"] = '<script>window.importExecuted=true</script>'
    import yaml
    page.locator('#api-spec-upload-form input').set_input_files({"name": "openapi.yml", "mimeType": "application/yaml",
        "buffer": yaml.safe_dump(spec).encode()})
    page.locator('#api-spec-upload-form').get_by_role("button", name="上传接口文档", exact=True).click()
    expect(page.locator('.api-import-success')).to_contain_text("解析完成")
    expect(page.locator('.api-spec-list')).to_contain_text(spec["info"]["title"])
    assert page.locator('#project-detail img, #project-detail script').count() == 0
    assert page.evaluate("window.importExecuted === undefined")
    page.set_viewport_size({"width": 390, "height": 844})
    OUTPUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUTPUT / "api-import-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator('#language').select_option("en-US")
    expect(page.get_by_role("heading", name="API Testing", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Generate test plan", exact=True)).to_be_disabled()
    assert not errors


def test_browser_create_api_project_without_base_url(auth_browser):
    page, _, url, _, errors = auth_browser
    register_browser(page, url, "create-api-import@example.com")
    complete_onboarding(page)
    page.get_by_role("link", name="项目", exact=True).click()
    page.get_by_role("button", name="创建项目", exact=True).click()
    page.locator('#project-form [name=name]').fill("仅导入文档的 API 项目")
    page.locator('#project-form [name=project_type]').select_option("api")
    expect(page.locator('#project-form [name=url]')).not_to_have_attribute("required", "")
    page.locator('#project-form').get_by_role("button", name="保存", exact=True).click()
    expect(page.locator('#dialog')).not_to_be_visible()
    expect(page.get_by_role("heading", name="仅导入文档的 API 项目", exact=True)).to_be_visible()
    assert not errors
