"""Real Chromium review -> run -> reload against an in-memory HTTP mock server."""
import asyncio
import json
import socket
from pathlib import Path
from threading import Event

import httpx
from playwright.sync_api import expect

from tests.test_auth_browser import auth_browser as auth_browser  # noqa: F401
from tests.test_product_browser import web_product as web_product  # noqa: F401
from tests.test_test_planner import mock_output
from tests.test_test_planner_browser import setup_planner

OUTPUT = Path('work/phase6-2c-1d-ui')


def prepare(page, url, target, app, monkeypatch, base_url='https://qa.example/v1'):
    original_dns = socket.getaddrinfo
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, *a, **kw: [
        (socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]
        if host == 'qa.example' else original_dns(host,*a,**kw))
    def model(request):
        context = json.loads(json.loads(request.content)['messages'][1]['content'])
        return httpx.Response(200, json={'choices':[{'message':{'content':json.dumps(mock_output(context))}}]})
    app.state.gateway.transport_factory = lambda: httpx.MockTransport(model)
    setup_planner(page,url,target)
    project = page.request.get(url+'/api/projects').json()[0]
    csrf = page.request.get(url+'/api/auth/me').json()['csrf_token']
    response = page.request.patch(url+'/api/projects/'+project['id'], headers={'X-CSRF-Token':csrf}, data={
        'name':project['name'],'project_type':'api','url':base_url})
    assert response.status == 200
    page.get_by_role('button',name='生成测试计划',exact=True).click()
    expect(page.locator('.planner-case')).to_have_count(8)
    return project


def test_browser_human_review_execute_status_results_reload_mobile(auth_browser,web_product,monkeypatch):
    page,_,url,target,errors = auth_browser
    _,_,app,_,_ = web_product
    prepare(page,url,target,app,monkeypatch)
    expect(page.get_by_role('button',name='执行 API 测试',exact=True)).to_be_disabled()
    expect(page.locator('.execution-request')).to_have_count(8)
    expect(page.locator('.execution-review')).to_contain_text('JSON 断言')
    expect(page.locator('.execution-review')).to_contain_text('每条 Case 仅发送一个 HTTP 请求')
    expect(page.locator('.candidate-intent')).to_have_count(8)
    assert page.locator('.planner-case').first.evaluate("node => node.querySelector('.execution-request').compareDocumentPosition(node.querySelector('.candidate-intent')) & Node.DOCUMENT_POSITION_FOLLOWING")
    gate = Event()
    calls = []
    async def server(request):
        calls.append(request)
        assert await asyncio.to_thread(gate.wait,10)
        return httpx.Response(200,text='<img src=x onerror="window.executionXSS=true">')
    app.state.api_executor.transport_factory = lambda: httpx.MockTransport(server)
    page.get_by_role('button',name='审核通过',exact=True).click()
    expect(page.locator('.execution-review')).not_to_contain_text('审核通过')
    expect(page.get_by_role('button',name='执行 API 测试',exact=True)).to_be_enabled()
    page.reload()
    expect(page.locator('.execution-request').first).to_have_attribute('readonly','')
    expect(page.locator('.planner-case').first).to_contain_text('人工批准的冻结配置')
    expect(page.locator('.planner-case').first).not_to_contain_text('尚未确认符合当前场景')
    page.get_by_role('button',name='执行 API 测试',exact=True).click()
    try:
        expect(page.locator('.api-run-summary')).to_contain_text('执行中')
        expect(page.get_by_role('button',name='执行 API 测试',exact=True)).to_be_disabled()
    finally:
        gate.set()
    expect(page.locator('.api-run-summary')).to_contain_text('已通过: 8')
    expect(page.locator('.api-run-summary')).to_contain_text('未通过: 0')
    expect(page.locator('.api-execution')).to_have_count(8)
    page.locator('.api-execution').first.locator('summary').click()
    expect(page.locator('#api-run-results')).to_contain_text('window.executionXSS')
    assert page.locator('#api-run-results img').count() == 0
    assert page.evaluate('window.executionXSS === undefined')
    OUTPUT.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(OUTPUT/'execution-desktop.png'),full_page=True)
    page.reload()
    expect(page.locator('.api-run-summary')).to_contain_text('已通过: 8')
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=str(OUTPUT/'execution-mobile.png'),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('#language').select_option('en-US')
    expect(page.locator('.execution-review')).to_contain_text('Each case sends one HTTP request')
    expect(page.locator('.candidate-intent').first).to_contain_text('Candidate Test Intent')
    expect(page.get_by_role('button',name='Execute API tests',exact=True)).to_be_enabled()
    assert len(calls) == 8 and app.state.target_fixture.mutations == 0 and not errors


def test_browser_private_target_approval_blocked_without_requests(auth_browser,web_product,monkeypatch):
    page,_,url,target,errors = auth_browser
    _,_,app,_,_ = web_product
    prepare(page,url,target,app,monkeypatch,base_url=target)
    calls = []
    app.state.api_executor.transport_factory = lambda: httpx.MockTransport(
        lambda r: calls.append(r) or httpx.Response(200))
    page.get_by_role('button',name='审核通过',exact=True).click()
    expect(page.locator('#toast')).to_contain_text('仅允许公网目标')
    expect(page.get_by_role('button',name='执行 API 测试',exact=True)).to_be_disabled()
    assert calls == [] and not errors


def test_browser_assertions_classification_history_and_rerun(auth_browser,web_product,monkeypatch):
    page,_,url,target,errors=auth_browser
    _,_,app,_,_=web_product
    prepare(page,url,target,app,monkeypatch)
    assertions=[{'type':'json','path':'data.success','operator':'equals','expected':True},
                {'type':'json','path':'data.token','operator':'exists'}]
    for editor in page.locator('.execution-assertions').all():
        editor.fill(json.dumps(assertions))
    app.state.api_executor.transport_factory=lambda: httpx.MockTransport(lambda request: httpx.Response(200,
        json={'data':{'success':True,'token':'browser-response-secret'},'echo':'browser-response-secret'}))
    page.get_by_role('button',name='审核通过',exact=True).click()
    expect(page.locator('.execution-assertions').first).to_have_attribute('readonly','')
    page.get_by_role('button',name='执行 API 测试',exact=True).click()
    expect(page.locator('.api-selected-run')).to_contain_text('已通过: 8')
    first=page.locator('#api-run-selector').input_value()
    original=page.request.get(url+'/api/test-runs/'+first).json()
    async def mixed(request):
        if request.method=='POST':
            raise httpx.ReadTimeout('hidden detail')
        return httpx.Response(200,json={'data':{'success':False,'token':'browser-response-secret'}})
    app.state.api_executor.transport_factory=lambda: httpx.MockTransport(mixed)
    page.get_by_role('button',name='执行 API 测试',exact=True).click()
    expect(page.locator('.api-run-summary')).to_have_count(2)
    expect(page.locator('.api-selected-run')).to_contain_text('执行错误: 4')
    expect(page.locator('.api-selected-run')).to_contain_text('未通过: 4')
    page.locator('#api-run-selector').select_option(first)
    expect(page.locator('.api-selected-run')).to_have_attribute('data-run-id',first)
    page.locator('.api-execution').first.locator('summary').click()
    expect(page.locator('.api-execution').first).to_contain_text('expected')
    page.wait_for_timeout(5200)  # Covers two scheduled refreshes while a historical run is selected.
    expect(page.locator('#api-run-selector')).to_have_value(first)
    expect(page.locator('.api-execution').first).to_have_attribute('open','')
    assert 'browser-response-secret' not in page.locator('#api-run-results').inner_text()
    OUTPUT.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(OUTPUT/'history-assertions-desktop.png'),full_page=True)
    page.get_by_role('button',name='重跑此计划',exact=True).click()
    expect(page.locator('.api-run-summary')).to_have_count(3)
    assert page.request.get(url+'/api/test-runs/'+first).json()==original
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('#language').select_option('en-US')
    expect(page.locator('.api-selected-run')).to_contain_text('Execution Error')
    page.screenshot(path=str(OUTPUT/'history-errors-mobile.png'),full_page=True)
    assert not errors and app.state.target_fixture.mutations==0
