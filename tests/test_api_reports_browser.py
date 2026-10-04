"""Production report acceptance in Chromium; model and API target use fake transport."""

import json
from pathlib import Path
from uuid import uuid4

import httpx
from playwright.sync_api import expect
from sqlalchemy import update
from sqlalchemy.orm import Session

from autoqa.product_models import MemberRow, ProviderConfigRow, WorkspaceRow
from autoqa.product_models import TestExecution as Execution
from tests.test_api_execution_browser import prepare
from tests.test_api_reports import analysis_output
from tests.test_auth_browser import auth_browser as auth_browser  # noqa: F401
from tests.test_product_browser import web_product as web_product  # noqa: F401

OUTPUT = Path("work/phase6-2c-1e-ui")


def report_run(page, url, target, app, monkeypatch):
    prepare(page, url, target, app, monkeypatch)
    count = 0

    def response(request):
        nonlocal count
        count += 1
        if count > 6:
            raise httpx.ConnectError("private fixture error", request=request)
        return httpx.Response(200 if count <= 2 else 500, text="<script>window.reportXSS=true</script>")

    app.state.api_executor.transport_factory = lambda: httpx.MockTransport(response)
    page.get_by_role("button", name="审核通过", exact=True).click()
    page.get_by_role("button", name="执行 API 测试", exact=True).click()
    expect(page.locator(".api-selected-run")).to_contain_text("执行错误: 2")
    run = page.locator("#api-run-selector").input_value()
    page.get_by_role("button", name="查看测试报告", exact=True).click()
    expect(page.get_by_role("heading", name="质量报告", exact=True)).to_be_visible()
    return run


def test_report_browser_evidence_cost_ai_exports_reload_mobile_languages(
    auth_browser, web_product, monkeypatch
):
    page, _, url, target, errors = auth_browser
    _, _, app, _, _ = web_product
    run = report_run(page, url, target, app, monkeypatch)
    expect(page.locator(".quality-metrics")).to_contain_text("25.00%")
    expect(page.locator(".quality-failures .quality-finding")).to_have_count(4)
    expect(page.locator(".quality-errors .quality-finding")).to_have_count(2)
    page.locator(".quality-failures .quality-evidence summary").first.click()
    expect(page.locator(".quality-failures")).to_contain_text("window.reportXSS")
    assert (
        page.evaluate("window.reportXSS===undefined")
        and page.locator("#api-quality-report script").count() == 0
    )
    calls = []
    canaries = ['sk-fixture-1234', 'browser-authorization-canary', 'browser-prompt-canary', 'browser-evidence-canary']
    with Session(app.state.store.engine) as session:
        for row in session.query(Execution).filter(Execution.run_id == run):
            row.request_data = {'headers': {'Authorization': 'Bearer ' + canaries[1]}}
            row.response_body = json.dumps({'secret': canaries[3]})
        session.commit()
    from autoqa.result_analyzer import SYSTEM
    monkeypatch.setattr('autoqa.result_analyzer.SYSTEM', SYSTEM + '\nAuthorization: Bearer ' + canaries[2])

    def model(request):
        payload = json.loads(request.content)
        assert canaries[2] in payload['messages'][0]['content']
        context = json.loads(payload["messages"][1]["content"])
        calls.append(context)
        output = analysis_output(context['executions'])
        for item in output['analyses']:
            item['analysis_summary'] = ' '.join('Bearer ' + value for value in canaries)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(output)}}]},
        )

    app.state.gateway.transport_factory = lambda: httpx.MockTransport(model)
    assert page.locator(".quality-selection:checked").count() == 3
    with page.expect_response(lambda response: response.url.endswith('/analysis-intents')) as prepared:
        page.get_by_role("button", name="AI 分析失败原因", exact=True).click()
    intent = prepared.value.json()
    assert prepared.value.status == 201 and intent['status'] == 'prepared'
    expect(page.locator("#dialog")).to_contain_text("模型费用")
    expect(page.locator("#dialog .dialog-body")).to_have_attribute("data-notice-version", "analyzer-cost-v1")
    assert calls == []
    page.locator('#dialog').get_by_role('button',name='取消',exact=True).click()
    assert calls == []
    saved = page.request.get(url + '/api/test-runs/' + run + '/analysis-intents/' + intent['intent_id']).json()
    assert saved['confirmed_at'] is None and saved['request_count'] == 0
    page.get_by_role("button", name="AI 分析失败原因", exact=True).click()
    expect(page.locator("#dialog")).to_contain_text("模型费用")
    with page.expect_request(lambda request: request.url.endswith('/analyze')) as confirmation:
        page.get_by_role("button", name="确认分析", exact=True).click()
    assert set(confirmation.value.post_data_json) == {'intent_id'}
    expect(page.locator(".quality-ai")).to_have_count(3)
    expect(page.locator(".quality-ai").first).to_contain_text("Root Cause Not Confirmed")
    assert len(calls) == 1
    page.get_by_role("button", name="AI 分析失败原因", exact=True).click()
    expect(page.locator("#dialog")).to_contain_text("需要模型分析 0 项")
    with page.expect_response(lambda response: response.url.endswith('/analyze') and response.request.method == 'POST') as completed:
        page.get_by_role("button", name="确认分析", exact=True).click()
    assert completed.value.status == 200
    expect(page.locator("#dialog")).not_to_be_visible()
    expect(page.get_by_role("button", name="重新 AI 分析", exact=True)).to_be_enabled()
    assert len(calls) == 1
    page.get_by_role("button", name="重新 AI 分析", exact=True).click()
    expect(page.locator("#dialog")).to_contain_text("需要模型分析 3 项")
    with page.expect_response(lambda response: response.url.endswith('/analyze') and response.request.method == 'POST') as completed:
        page.get_by_role("button", name="确认分析", exact=True).click()
    assert completed.value.status == 200
    expect(page.locator("#dialog")).not_to_be_visible()
    expect(page.locator(".quality-ai")).to_have_count(3)
    assert len(calls) == 2
    assert all(value not in page.locator('#api-quality-report').inner_text() for value in canaries)
    for label, format in [("导出 HTML", "html"), ("导出 Markdown", "md")]:
        with page.expect_download() as downloaded:
            page.get_by_role("button", name=label, exact=True).click()
        result = downloaded.value
        assert result.suggested_filename == f"autoqa-run-{run}.{format}"
        content = Path(result.path()).read_text(encoding="utf-8")
        assert "Root Cause Not Confirmed" in content
        assert all(value not in content for value in canaries)
        if format == "html":
            assert "<script>window.reportXSS" not in content
    page.reload()
    expect(page.locator(".quality-ai")).to_have_count(3)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(OUTPUT / "report-desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(OUTPUT / "report-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    page.locator("#language").select_option("en-US")
    expect(page.get_by_role("heading", name="Quality Report", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Export Markdown", exact=True)).to_be_visible()
    page.goto(url + "/#test-runs/" + run + "/report")
    expect(page.get_by_role("heading", name="Quality Report", exact=True)).to_be_visible()
    assert len(calls) == 2 and not errors


def test_report_no_provider_and_identity_switch(auth_browser, web_product, monkeypatch):
    page, _, url, target, errors = auth_browser
    _, _, app, _, _ = web_product
    run = report_run(page, url, target, app, monkeypatch)
    with Session(app.state.store.engine) as s:
        s.execute(update(ProviderConfigRow).values(is_active=False, active_workspace_id=None))
        s.commit()
    page.reload()
    expect(page.locator(".quality-ai-controls")).to_contain_text("基础报告、证据和导出均可正常使用")
    expect(page.get_by_role("button", name="AI 分析失败原因", exact=True)).to_be_disabled()
    with page.expect_download():
        page.get_by_role("button", name="导出 Markdown", exact=True).click()
    page.get_by_role("button", name="退出登录", exact=True).click()
    expect(page.locator("#auth-form")).to_be_visible()
    assert page.locator("#api-quality-report").count() == 0
    page.goto(url + "/#test-runs/" + run + "/report")
    expect(page.locator("#auth-form")).to_be_visible()
    assert not errors


def test_workspace_switch_discards_delayed_report_export(auth_browser, web_product, monkeypatch):
    page, _, url, target, errors = auth_browser
    _, _, app, _, _ = web_product
    run = report_run(page, url, target, app, monkeypatch)
    user = page.request.get(url + "/api/auth/me").json()["user_id"]
    other = str(uuid4())
    with Session(app.state.store.engine) as session:
        session.add(WorkspaceRow(id=other, name="另一个工作区", owner_id=user))
        session.flush()
        session.add(MemberRow(workspace_id=other, user_id=user, role="owner"))
        session.commit()
    page.reload()
    expect(page.get_by_role("heading", name="质量报告", exact=True)).to_be_visible()
    pending, downloads = [], []
    page.on("download", lambda item: downloads.append(item))
    page.route("**/api/test-runs/" + run + "/report.md", lambda route: pending.append(route))
    page.get_by_role("button", name="导出 Markdown", exact=True).click()
    expect(page.get_by_role("button", name="导出 Markdown", exact=True)).to_be_disabled()
    assert len(pending) == 1
    page.locator("#workspace-selector").select_option(other)
    expect(page.locator("#api-quality-report")).to_have_count(0)
    expect(page.locator("#workspace-selector")).to_have_value(other)
    with page.expect_event("requestfinished", predicate=lambda request: request.url.endswith("/report.md")):
        pending[0].fulfill(status=200, body="old private report", headers={"Content-Type": "text/markdown"})
    page.wait_for_timeout(150)
    assert downloads == [] and "old private report" not in page.locator("body").inner_text()
    page.goto(url + "/#test-runs/" + run + "/report")
    # Reload defaults to the original workspace, so use the current selector to explicitly switch again.
    page.locator("#workspace-selector").select_option(other)
    expect(page.locator("#api-quality-report")).to_have_count(0)
    assert not errors


def test_workspace_switch_discards_delayed_consent_intent(auth_browser, web_product, monkeypatch):
    page, _, url, target, errors = auth_browser
    _, _, app, _, _ = web_product
    run = report_run(page, url, target, app, monkeypatch)
    user = page.request.get(url + '/api/auth/me').json()['user_id']
    other = str(uuid4())
    with Session(app.state.store.engine) as session:
        session.add(WorkspaceRow(id=other, name='另一个审计工作区', owner_id=user))
        session.flush()
        session.add(MemberRow(workspace_id=other, user_id=user, role='owner'))
        session.commit()
    page.reload()
    expect(page.get_by_role('heading', name='质量报告', exact=True)).to_be_visible()
    pending, calls = [], []
    app.state.gateway.transport_factory = lambda: httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(500))
    page.route('**/api/test-runs/' + run + '/analysis-intents', lambda route: pending.append(route))
    with page.expect_request(lambda request: request.url.endswith('/analysis-intents')):
        page.get_by_role('button', name='AI 分析失败原因', exact=True).click()
    assert len(pending) == 1
    # The backend has committed a prepared intent, but its response has not reached the old UI.
    prepared = pending[0].fetch()
    assert prepared.status == 201 and calls == []
    intent = prepared.json()
    page.locator('#workspace-selector').select_option(other)
    expect(page.locator('#api-quality-report')).to_have_count(0)
    with page.expect_response(lambda response: response.url.endswith('/analysis-intents')) as delivered:
        pending[0].fulfill(status=201, json=intent)
    assert delivered.value.status == 201
    expect(page.locator('#toast')).to_be_visible()
    expect(page.locator('#dialog')).not_to_be_visible()
    assert calls == []
    saved = page.request.get(url + '/api/test-runs/' + run + '/analysis-intents/' + intent['intent_id'],
                             headers={'X-Workspace-ID': intent['workspace_id']}).json()
    assert saved['status'] == 'prepared' and saved['confirmed_at'] is None and not errors
