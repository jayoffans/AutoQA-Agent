"""Chinese plan creation/details, failure and mobile XSS acceptance in real Chromium."""
import json
import threading
from pathlib import Path

import httpx
from playwright.sync_api import expect

from tests.test_api_import import SPEC
from tests.test_api_import_browser import open_import
from tests.test_auth_browser import auth_browser as auth_browser  # noqa: F401
from tests.test_product import KEY
from tests.test_product_browser import web_product as web_product  # noqa: F401
from tests.test_test_planner import mock_output

OUTPUT = Path('work/phase6-2c-1b-ui')


def setup_planner(page, url, target):
    open_import(page, url)
    csrf = page.request.get(url + '/api/auth/me').json()['csrf_token']
    assert page.request.put(url + '/api/provider', headers={'X-CSRF-Token': csrf}, data={
        'provider': 'compatible', 'base_url': target, 'api_key': KEY, 'model': 'mock-planner'}).status == 200
    page.locator('#api-spec-upload-form input').set_input_files({
        'name': 'openapi.json', 'mimeType': 'application/json', 'buffer': json.dumps(SPEC).encode()})
    page.locator('#api-spec-upload-form').get_by_role('button', name='上传接口文档', exact=True).click()
    expect(page.locator('.api-endpoint-table tbody tr')).to_have_count(2)
    page.get_by_role('button', name='刷新', exact=True).click()
    expect(page.get_by_role('button', name='生成测试计划', exact=True)).to_be_enabled()


def test_browser_planner_generation_detail_reload_mobile_and_escape(auth_browser, web_product):
    page, _, url, target, errors = auth_browser
    _, _, app, _, _ = web_product
    release = threading.Event()
    calls = []
    def handler(request):
        calls.append(request.url.path)
        assert release.wait(10)
        context = json.loads(json.loads(request.content)['messages'][1]['content'])
        output = mock_output(context)
        output['cases'][0]['name'] = '<img src=x onerror="window.plannerExecuted=true">'
        output['cases'][0]['steps'] = ['<script>window.plannerExecuted=true</script>']
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(output)}}]})
    app.state.gateway.transport_factory = lambda: httpx.MockTransport(handler)
    setup_planner(page, url, target)
    page.get_by_role('button', name='生成测试计划', exact=True).click()
    try:
        expect(page.get_by_role('button', name='正在生成测试计划…', exact=True)).to_be_disabled()
        expect(page.locator('.planner-notice')).to_have_text('正在生成测试计划…')
    finally:
        release.set()
    expect(page.locator('.planner-case')).to_have_count(8)
    expect(page.get_by_role('heading', name='测试计划详情', exact=True).first).to_be_visible()
    assert '/test-plans/' in page.url
    expect(page.locator('#project-detail')).to_contain_text('测试数量: 8')
    expect(page.locator('.planner-counts')).to_contain_text('安全场景 2')
    assert KEY not in page.locator('body').inner_html()
    assert page.locator('#project-detail img, #project-detail script').count() == 0
    assert page.evaluate('window.plannerExecuted === undefined')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUTPUT / 'planner-desktop.png'), full_page=True)
    page.reload()
    expect(page.locator('.planner-case')).to_have_count(8)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.screenshot(path=str(OUTPUT / 'planner-mobile.png'), full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.get_by_role('button', name='返回 API 测试', exact=True).click()
    expect(page.locator('.planner-card')).to_have_count(1)
    expect(page.locator('.planner-card')).to_contain_text('已就绪')
    page.get_by_role('button', name='查看', exact=True).click()
    expect(page.locator('.planner-case')).to_have_count(8)
    assert page.request.get(url + '/api/tasks').json() == []
    assert calls == ['/chat/completions'] and app.state.target_fixture.mutations == 0 and not errors


def test_browser_failure_shows_safe_message_and_saved_failed_plan(auth_browser, web_product):
    page, _, url, target, errors = auth_browser
    _, _, app, _, _ = web_product
    app.state.gateway.transport_factory = lambda: httpx.MockTransport(lambda request: httpx.Response(
        200, json={'choices': [{'message': {'content': json.dumps({'cases': []})}}]}))
    setup_planner(page, url, target)
    page.get_by_role('button', name='生成测试计划', exact=True).click()
    expect(page.locator('#toast')).to_contain_text('未保存任何测试用例')
    expect(page.locator('.planner-card')).to_contain_text('生成失败')
    expect(page.locator('.planner-card')).to_contain_text('测试数量: 0')
    expect(page.get_by_role('button', name='生成测试计划', exact=True)).to_be_enabled()
    assert not errors
