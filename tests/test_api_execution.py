"""Review/execution acceptance uses MockTransport as an in-memory HTTP mock server."""
import asyncio
import json
import socket
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autoqa.api_executor import ApiExecutor, CaseConfig, default_value, validate_base_url
from autoqa.app import create_app
from autoqa.product_models import TestCase as Case
from autoqa.product_models import TestExecution as Execution
from autoqa.product_models import TestPlan as Plan
from autoqa.product_models import TestRun as Run
from autoqa.studio_models import ProjectRow
from autoqa.target_network import PinnedTransport
from tests.test_api_import import imports as imports  # noqa: F401
from tests.test_auth import bearer, sign_up
from tests.test_product import product as product  # noqa: F401
from tests.test_test_planner import generate
from tests.test_test_planner import planner as planner  # noqa: F401


@pytest.fixture
def execution(planner):
    assert planner.patch('/api/projects/' + planner.item['id'], json={
        'name': '执行项目', 'project_type': 'api', 'url': 'https://qa.example/v1'}).status_code == 200
    planner.plan_id = generate(planner).json()['id']
    planner.plan_path = '/api/test-plans/' + planner.plan_id
    preview = planner.get(planner.plan_path + '/execution-preview').json()
    planner.review = {'cases': [{'case_id': row['case_id'], **row['config']} for row in preview['cases']]}
    # Default redaction intentionally masks schema fixture passwords; human review supplies a real fixture value.
    for row in planner.review['cases']:
        body = row['request_data']['json_body']
        if body and 'password' in body:
            body['password'] = 'execution-fixture-secret'
    planner.api_calls = []
    planner.handler = lambda request: httpx.Response(200, json={'ok': True, 'access_token': 'response-secret',
                                                              'echo': 'execution-fixture-secret'})
    async def mock_server(request):
        planner.api_calls.append(request)
        return await asyncio.to_thread(planner.handler, request)
    planner.application.state.api_executor.transport_factory = lambda: httpx.MockTransport(mock_server)
    return planner


def approve(client):
    return client.post(client.plan_path + '/approve', json=client.review)


def execute(client):
    return client.post(client.plan_path + '/execute')


def counts(client):
    with Session(client.application.state.store.engine) as session:
        return tuple(session.scalar(select(func.count()).select_from(row)) for row in (Run, Execution))


def test_review_preview_is_offline_and_has_schema_defaults(execution):
    preview = execution.get(execution.plan_path + '/execution-preview').json()
    assert preview['base_url'] == 'https://qa.example/v1'
    assert len(preview['cases']) == 8
    request = next(c['config']['request_data'] for c in preview['cases'] if c['config']['request_data']['path_params'])
    assert request['path_params'] == {'id': 1} and request['query'] == {'limit': 1}
    assert counts(execution) == (0, 0) and execution.api_calls == []


def test_approve_records_human_and_encrypts_immutable_snapshot(execution):
    response = approve(execution)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'approved' and execution.api_calls == []
    with Session(execution.application.state.store.engine) as session:
        plan = session.get(Plan, execution.plan_id)
        assert plan.approved_by == response.json()['created_by'] and plan.approved_at
        assert plan.approved_base_url == 'https://qa.example/v1'
        for case in session.scalars(select(Case)):
            assert case.execution_config['encrypted'].startswith('v1:')
            assert 'execution-fixture-secret' not in json.dumps(case.execution_config)
    assert approve(execution).status_code == 409


@pytest.mark.parametrize('status', ['draft', 'generating', 'failed', 'approved'])
def test_approve_requires_ready(execution, status):
    with Session(execution.application.state.store.engine) as session:
        session.execute(update(Plan).values(status=status))
        session.commit()
    assert approve(execution).status_code == 409 and counts(execution) == (0, 0)


@pytest.mark.parametrize('change', [
    lambda data: data['cases'].pop(),
    lambda data: data['cases'].append(data['cases'][0]),
    lambda data: data['cases'][0].update(case_id=str(uuid4())),
    lambda data: data['cases'][0].update(expected_status=999),
    lambda data: data['cases'][0].update(expected_status=True),
    lambda data: data['cases'][0]['request_data'].update(url='http://127.0.0.1'),
    lambda data: data['cases'][0]['request_data']['headers'].update(Host='localhost'),
    lambda data: data['cases'][0]['request_data']['headers'].update(Connection='keep-alive'),
    lambda data: data['cases'][0]['request_data']['headers'].update({'X-Test': 'value\r\nHost: localhost'}),
], ids=['missing','duplicate','foreign-case','invalid-status','bool-status','url-override','host','connection','header-injection'])
def test_invalid_review_is_atomic(execution, change):
    change(execution.review)
    assert approve(execution).status_code == 422
    assert execution.get(execution.plan_path).json()['status'] == 'ready'
    with Session(execution.application.state.store.engine) as session:
        assert all(case.execution_config is None for case in session.scalars(select(Case)))
    assert execution.api_calls == []


def test_prose_scenarios_need_explicit_human_request_review(execution):
    assert execution.post(execution.plan_path + '/approve').status_code == 422
    assert execute(execution).status_code == 409 and not execution.api_calls


def test_all_results_saved_with_correct_counts_no_model_execution(execution):
    assert approve(execution).status_code == 200
    model_calls = len(execution.model_calls)
    response = execute(execution)
    assert response.status_code == 201, response.text
    run = response.json()
    assert run['status'] == 'passed' and run['total_cases'] == run['passed_cases'] == 8 and run['failed_cases'] == 0
    assert run['started_at'].endswith('Z') and run['finished_at'].endswith('Z')
    assert counts(execution) == (1, 8) and len(execution.api_calls) == 8
    assert len(execution.model_calls) == model_calls
    result = execution.get('/api/test-runs/' + run['id'])
    assert 'execution-fixture-secret' not in result.text and 'response-secret' not in result.text
    assert all(e['duration_ms'] >= 0 and e['passed'] for e in result.json()['executions'])
    assert {r.url.path for r in execution.api_calls} == {'/v1/login', '/v1/users/1'}
    assert execution.get('/api/tasks').json() == []


def test_mixed_pass_fail_status_and_continues(execution):
    approve(execution)
    execution.handler = lambda r: httpx.Response(500 if r.method == 'POST' else 200, text='fixture failure')
    run = execute(execution).json()
    assert run['status'] == 'failed' and run['passed_cases'] == run['failed_cases'] == 4
    assert counts(execution) == (1, 8)
    assert any('预期 HTTP' in (e['error_message'] or '') for e in execution.get('/api/test-runs/' + run['id']).json()['executions'])


@pytest.mark.parametrize('error', [httpx.ReadTimeout('secret-url'), httpx.ConnectError('secret-url')])
def test_http_failure_saved_sanitized_and_no_retry(execution, error, caplog):
    approve(execution)
    def fail(request):
        raise error
    execution.handler = fail
    run = execute(execution).json()
    assert run['failed_cases'] == 8 and len(execution.api_calls) == 8
    assert 'secret-url' not in execution.get('/api/test-runs/' + run['id']).text + caplog.text


def test_negative_case_uses_human_status_and_payload(execution):
    case = next(c for c in execution.review['cases'] if c['request_data']['json_body'])
    case['expected_status'] = 401
    case['request_data']['json_body'] = {'username': 'invalid'}
    approve(execution)
    execution.handler = lambda r: httpx.Response(401 if r.method == 'POST' and json.loads(r.content) == {
        'username': 'invalid'} else 200, json={'ok': True})
    run = execute(execution).json()
    assert run['passed_cases'] == 8


def test_no_redirect_following_even_when_approved_status_is_redirect(execution):
    for case in execution.review['cases']:
        case['expected_status'] = 302
    approve(execution)
    execution.handler = lambda r: httpx.Response(302, headers={'Location': 'http://169.254.169.254/latest'})
    run = execute(execution).json()
    assert run['failed_cases'] == 8 and len(execution.api_calls) == 8
    assert all(r.url.host == 'qa.example' for r in execution.api_calls)


def test_oversize_response_fails_without_persisting_body(execution):
    approve(execution)
    execution.handler = lambda r: httpx.Response(200, content=b'x' * 256_001)
    run = execute(execution).json()
    assert run['failed_cases'] == 8
    assert all(e['response_body'] is None for e in execution.get('/api/test-runs/' + run['id']).json()['executions'])


def test_non_json_response_is_saved_and_escaped_by_ui(execution):
    approve(execution)
    execution.handler = lambda r: httpx.Response(200, text='<script>window.executed=true</script>')
    run = execute(execution).json()
    assert run['passed_cases'] == 8
    assert execution.get('/api/test-runs/' + run['id']).json()['executions'][0]['response_body'].startswith('<script>')


@pytest.mark.parametrize('suffix', ['/approve','/execute','/execution-preview','/runs'])
def test_cross_workspace_and_unauthenticated_denied(execution, suffix):
    signed = sign_up(execution, email='foreign-executor@example.com')
    other = bearer(execution, signed['access_token'])
    response = other.post(execution.plan_path + suffix, json=execution.review) if suffix in {
        '/approve','/execute'} else other.get(execution.plan_path + suffix)
    assert response.status_code == 404
    other.headers.clear()
    assert other.get(execution.plan_path + '/runs').status_code == 401
    assert counts(execution) == (0, 0) and not execution.api_calls


def test_csrf_and_missing_plan_denied(execution):
    execution.headers.pop('X-CSRF-Token')
    assert approve(execution).status_code == 403
    assert execute(execution).status_code == 403
    assert execution.get('/api/test-runs/missing').status_code == 404


def test_run_scope_and_archived_project_denied(execution):
    approve(execution)
    run = execute(execution).json()
    signed = sign_up(execution, email='other-run@example.com')
    assert bearer(execution, signed['access_token']).get('/api/test-runs/' + run['id']).status_code == 404
    with Session(execution.application.state.store.engine) as session:
        session.execute(update(ProjectRow).where(ProjectRow.id == execution.item['id']).values(archived=True))
        session.commit()
    assert execution.get('/api/test-runs/' + run['id']).status_code == 404


def test_target_changes_require_new_review(execution):
    approve(execution)
    with Session(execution.application.state.store.engine) as session:
        session.execute(update(ProjectRow).values(url='https://qa.example/v2'))
        session.commit()
    assert execute(execution).status_code == 409 and counts(execution) == (0, 0)


@pytest.mark.parametrize('url', ['http://localhost','http://127.0.0.1','http://10.1.2.3','http://192.168.1.1',
    'http://172.16.0.1','http://169.254.169.254','http://[::1]','http://[fc00::1]',
    'http://2130706433','http://127.1','http://0.0.0.0','file:///etc/passwd',
    'http://user:password@qa.example','https://qa.example?target=localhost','https://qa.example#fragment'])
def test_ssrf_base_url_rejected_even_when_private_setting_enabled(url):
    with pytest.raises(ValueError):
        validate_base_url(url)


def test_dns_mixed_private_addresses_are_blocked(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443))])
    with pytest.raises(ValueError):
        validate_base_url('https://mixed.example')


def test_dns_rebinding_blocked_before_mock_server(execution, monkeypatch):
    approve(execution)
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))])
    execution.settings.allow_private_targets = True
    assert execute(execution).status_code == 422
    assert not execution.api_calls and counts(execution) == (0, 0)


@pytest.mark.parametrize('value', ['..','../admin','%2e%2e','//evil','a\\b'])
def test_path_traversal_rejected_during_review(execution, value):
    case = next(c for c in execution.review['cases'] if c['request_data']['path_params'])
    case['request_data']['path_params']['id'] = value
    assert approve(execution).status_code == 422 and not execution.api_calls


@pytest.mark.parametrize('schema,expected', [({'type':'string'}, 'test'), ({'type':'integer'},1),
    ({'type':'boolean'},True), ({'type':'array','items':{'type':'integer'}},[1]),
    ({'type':'string','default':'fixture'},'fixture'), ({'type':'string','enum':['first','second']},'first'),
    ({'type':'object','properties':{'enabled':{'type':'boolean'}}},{'enabled':True}),
    ({'type':'integer','maximum':0},0)])
def test_schema_defaults(schema, expected):
    assert default_value(schema, {}) == expected


def test_schema_local_ref_and_recursion_limit():
    document={'components':{'schemas':{'ID':{'type':'integer'}}}}
    assert default_value({'$ref':'#/components/schemas/ID'}, document) == 1
    with pytest.raises(ValueError):
        default_value({'$ref':'http://localhost/schema'}, {})
    with pytest.raises(ValueError):
        default_value({'$ref':'#/recursive'}, {'recursive':{'$ref':'#/recursive'}})


async def test_production_executor_pins_ip_and_preserves_host_and_sni(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [
        (socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))])
    seen = []
    transport = PinnedTransport(False)
    await transport.inner.aclose()
    transport.inner = httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get('https://qa.example/path')
    assert seen[0].url.host == '93.184.216.34' and seen[0].headers['Host'] == 'qa.example'
    assert seen[0].extensions['sni_hostname'] == 'qa.example'


def test_completed_run_reload_and_explicit_rerun(execution):
    approve(execution)
    first = execute(execution).json()
    second = execute(execution).json()
    assert first['id'] != second['id'] and counts(execution) == (2, 16)
    with TestClient(create_app(execution.settings), base_url='https://testserver') as restarted:
        restarted.cookies.update(execution.cookies)
        assert len(restarted.get(execution.plan_path + '/runs').json()) == 2
        assert len(restarted.get('/api/test-runs/' + first['id']).json()['executions']) == 8


def test_concurrent_run_rejected_and_running_visible(execution):
    approve(execution)
    entered, release = Event(), Event()
    def handler(request):
        entered.set()
        assert release.wait(10)
        return httpx.Response(200)
    execution.handler = handler
    with ThreadPoolExecutor() as pool:
        future = pool.submit(execute, execution)
        try:
            assert entered.wait(10)
            runs = execution.get(execution.plan_path + '/runs').json()
            assert runs[0]['status'] == 'running' and runs[0]['passed_cases'] == 0
            assert execute(execution).status_code == 409
        finally:
            release.set()
        assert future.result().json()['passed_cases'] == 8


def test_storage_failure_marks_interrupted_preserves_previous_results(execution):
    approve(execution)
    def reject(mapper, connection, row):
        if row.method == 'GET':
            raise RuntimeError('do not expose storage detail')
    event.listen(Execution, 'before_insert', reject)
    try:
        response = execute(execution)
        assert response.status_code == 503 and 'storage detail' not in response.text
        run = execution.get('/api/test-runs/' + response.json()['detail']['run_id']).json()
        assert run['status'] == 'interrupted'
        assert len(run['executions']) == run['passed_cases'] + run['failed_cases']
        assert len(run['executions']) < 8
    finally:
        event.remove(Execution, 'before_insert', reject)


def test_restart_recovers_running_and_releases_slot(execution):
    approve(execution)
    with Session(execution.application.state.store.engine) as session:
        plan = session.get(Plan, execution.plan_id)
        row = Run(workspace_id=plan.workspace_id, project_id=plan.project_id, plan_id=plan.id,
                  active_plan_id=plan.id, total_cases=8, created_by=plan.created_by)
        session.add(row)
        session.commit()
        run_id = row.id
    with TestClient(create_app(execution.settings), base_url='https://testserver') as restarted:
        restarted.cookies.update(execution.cookies)
        run = restarted.get('/api/test-runs/' + run_id).json()
        assert run['status'] == 'interrupted' and run['finished_at'] and run['passed_cases'] == 0
    with Session(execution.application.state.store.engine) as session:
        assert session.get(Run, run_id).active_plan_id is None


async def test_executor_total_case_timeout_and_unsupported_method(monkeypatch):
    monkeypatch.setattr('autoqa.api_executor.CASE_TIMEOUT', .01)
    monkeypatch.setattr('autoqa.api_executor.validate_base_url', lambda url: None)
    monkeypatch.setattr('autoqa.api_executor.resolve_target', lambda *args: '93.184.216.34')
    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200)
    executor = ApiExecutor()
    executor.transport_factory = lambda: httpx.MockTransport(slow)
    case = SimpleNamespace(workspace_id='w', plan_id='p', id='c')
    endpoint = SimpleNamespace(method='GET',path='/ok')
    config = CaseConfig.model_validate({'request_data':{},'expected_status':200})
    result = await executor.execute(case, endpoint, 'https://qa.example', config, 'run')
    assert not result.passed and result.error_message == 'HTTP 请求超时'
    endpoint.method = 'TRACE'
    result = await executor.execute(case, endpoint, 'https://qa.example', config, 'run')
    assert not result.passed and result.response_status is None


def test_database_rejects_execution_from_another_plan(execution):
    approve(execution)
    run = execute(execution).json()
    foreign = generate(execution).json()
    with Session(execution.application.state.store.engine) as session:
        case = session.scalar(select(Case).where(Case.plan_id == foreign['id']))
        session.add(Execution(workspace_id=run['workspace_id'],plan_id=run['plan_id'],run_id=run['id'],case_id=case.id,
            method='GET',url='https://qa.example',request_data={},passed=False,duration_ms=0))
        with pytest.raises(IntegrityError):
            session.commit()


def test_plan_run_timeout_preserves_results_and_is_not_success(execution, monkeypatch):
    approve(execution)
    monkeypatch.setattr('autoqa.test_execution_api.RUN_TIMEOUT', .03)
    original = execution.application.state.api_executor.execute
    calls = []
    async def delayed(*args):
        calls.append(True)
        if len(calls) > 1:
            await asyncio.sleep(1)
        return await original(*args)
    monkeypatch.setattr(execution.application.state.api_executor, 'execute', delayed)
    response = execute(execution)
    assert response.status_code == 503
    run = execution.get('/api/test-runs/' + response.json()['detail']['run_id']).json()
    assert run['status'] == 'interrupted' and run['total_cases'] == 8
    assert len(run['executions']) == run['passed_cases'] + run['failed_cases'] < 8


async def test_asgi_mock_server_receives_json_arrays_boolean_and_query(monkeypatch):
    from fastapi import FastAPI, Request
    app = FastAPI()
    received = []
    @app.post('/v1/items/7')
    async def fixture(request: Request):
        received.append({'body': await request.json(), 'query': dict(request.query_params),
                         'tag': request.headers['x-fixture']})
        return {'created': True}
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [
        (socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))])
    executor = ApiExecutor()
    executor.transport_factory = lambda: httpx.ASGITransport(app=app)
    case = SimpleNamespace(workspace_id='w',plan_id='p',id='c')
    endpoint = SimpleNamespace(method='POST',path='/items/{id}')
    config = CaseConfig.model_validate({'expected_status':200,'request_data':{'path_params':{'id':7},
        'query':{'limit':2,'active':True},'headers':{'X-Fixture':'mock'},
        'json_body':{'name':'fixture','enabled':True,'values':[1,2]}}})
    result = await executor.execute(case, endpoint, 'https://qa.example/v1', config, 'run')
    assert result.passed and received == [{'body':{'name':'fixture','enabled':True,'values':[1,2]},
        'query':{'limit':'2','active':'true'},'tag':'mock'}]
