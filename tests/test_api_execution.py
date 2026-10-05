"""Review/execution acceptance uses MockTransport as an in-memory HTTP mock server."""
import asyncio
import json
import logging
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
    assert run['failed_cases'] == 0 and run['summary']['execution_error_cases'] == 8 and len(execution.api_calls) == 8
    assert run['status'] == 'execution_error'
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
    assert run['failed_cases'] == 0 and run['summary']['execution_error_cases'] == 8
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


def test_json_evidence_mixed_outcomes_and_raw_secret_comparison(execution):
    for case in execution.review['cases']:
        case['assertions']=[{'type':'json','path':'data.token','operator':'equals','expected':'response-fixture-secret'},
                            {'type':'json','path':'data.ok','operator':'equals','expected':True}]
    assert approve(execution).status_code==200
    def handler(request):
        return httpx.Response(200,json={'data':{'token':'response-fixture-secret','ok':request.method=='POST'},
                                        'echo':'response-fixture-secret'})
    execution.handler=handler
    model_calls=len(execution.model_calls)
    run=execute(execution).json()
    assert run['summary']['passed_cases']==run['summary']['failed_cases']==4
    assert run['summary']['execution_error_cases']==0
    detail=execution.get('/api/test-runs/'+run['id'])
    preview=execution.get(execution.plan_path+'/execution-preview')
    assert 'response-fixture-secret' not in detail.text+preview.text
    assert all(e['result_data']['assertion_results'][1]['status']=='passed' for e in detail.json()['executions'])
    assert len(execution.model_calls)==model_calls


@pytest.mark.parametrize('exception,code',[(RuntimeError('private executor detail'),'internal_error'),
                                         (httpx.RemoteProtocolError('private detail'),'transport_error')])
def test_case_internal_errors_saved_and_continue(execution,exception,code):
    approve(execution)
    def handler(request):
        if request.method=='POST':
            raise exception
        return httpx.Response(200)
    execution.handler=handler
    run=execute(execution).json()
    assert run['status']=='execution_error' and run['summary']['lifecycle']=='completed'
    assert run['summary']['execution_error_cases']==run['summary']['passed_cases']==4
    detail=execution.get('/api/test-runs/'+run['id'])
    assert 'private' not in detail.text
    assert {e['result_data']['error_code'] for e in detail.json()['executions']}=={None,code}


def test_dns_availability_is_execution_error_not_preflight_business_failure(execution,monkeypatch):
    approve(execution)
    def unavailable(*args,**kwargs):
        raise socket.gaierror('private DNS detail')
    monkeypatch.setattr(socket,'getaddrinfo',unavailable)
    response=execute(execution)
    assert response.status_code==201
    run=response.json()
    assert run['summary']['execution_error_cases']==8 and run['failed_cases']==0
    detail=execution.get('/api/test-runs/'+run['id'])
    assert 'private DNS' not in detail.text and not execution.api_calls
    assert all(e['result_data']['error_code']=='dns_error' for e in detail.json()['executions'])


def test_legacy_approved_config_executes_and_reruns_without_conversion(execution):
    from autoqa.test_execution_api import case_scope
    approve(execution)
    box=execution.application.state.gateway.box
    with Session(execution.application.state.store.engine) as session:
        for case in session.scalars(select(Case).where(Case.plan_id==execution.plan_id)):
            old=box.decrypt(case.execution_config['encrypted'],case_scope(case))
            old.pop('assertions')
            case.execution_config={'encrypted':box.encrypt(old,case_scope(case))}
        session.commit()
        snapshots={case.id:case.execution_config for case in session.scalars(select(Case))}
    first=execute(execution).json()
    original=execution.get('/api/test-runs/'+first['id']).json()
    second=execution.post('/api/test-runs/'+first['id']+'/rerun')
    assert second.status_code==201 and second.json()['id']!=first['id']
    assert execution.get('/api/test-runs/'+first['id']).json()==original
    with Session(execution.application.state.store.engine) as session:
        assert {case.id:case.execution_config for case in session.scalars(select(Case))}==snapshots


def test_legacy_classification_is_read_only_and_summary_does_not_double_count(execution):
    approve(execution)
    def handler(request):
        if request.method=='POST':
            raise httpx.ConnectError('fixture')
        return httpx.Response(200)
    execution.handler=handler
    run=execute(execution).json()
    with Session(execution.application.state.store.engine) as session:
        session.execute(update(Execution).where(Execution.run_id==run['id']).values(status=None,result_data=None))
        session.execute(update(Run).where(Run.id==run['id']).values(status='failed',failed_cases=4))
        session.commit()
    detail=execution.get('/api/test-runs/'+run['id']).json()
    assert detail['status']=='failed' and detail['failed_cases']==4
    assert detail['summary']['outcome']=='execution_error' and detail['summary']['failed_cases']==0
    assert detail['summary']['execution_error_cases']==4 and detail['summary']['classification_inferred']
    assert all(e['classification_inferred'] and e['result_data'] is None for e in detail['executions'])
    assert execution.get(execution.plan_path+'/runs').json()[0]['summary']==detail['summary']
    with Session(execution.application.state.store.engine) as session:
        assert all(row.status is None and row.result_data is None for row in session.scalars(select(Execution)))
        assert session.get(Run,run['id']).failed_cases==4


def test_rerun_permissions_csrf_target_change_and_active_slot(execution):
    approve(execution)
    first=execute(execution).json()
    path='/api/test-runs/'+first['id']+'/rerun'
    cookies=dict(execution.cookies)
    csrf=execution.headers['X-CSRF-Token']
    signed=sign_up(execution,email='foreign-rerun@example.com')
    assert bearer(execution,signed['access_token']).post(path).status_code==404
    execution.cookies.clear()
    execution.cookies.update(cookies)
    execution.headers['X-CSRF-Token']=csrf
    assert execution.post('/api/test-runs/missing/rerun').status_code==404
    with Session(execution.application.state.store.engine) as session:
        plan=session.get(Plan,execution.plan_id)
        active=Run(workspace_id=plan.workspace_id,project_id=plan.project_id,plan_id=plan.id,
                   active_plan_id=plan.id,total_cases=8,created_by=plan.created_by)
        session.add(active)
        session.commit()
        active_id=active.id
    assert execution.post(path).status_code==409
    assert execution.post('/api/test-runs/'+active_id+'/rerun').status_code==409
    execution.headers.pop('X-CSRF-Token')
    assert execution.post(path).status_code==403
    execution.headers['X-CSRF-Token']=csrf
    with Session(execution.application.state.store.engine) as session:
        session.execute(update(Run).where(Run.id==active_id).values(status='interrupted',active_plan_id=None))
        session.execute(update(ProjectRow).where(ProjectRow.id==execution.item['id']).values(url='https://qa.example/v2'))
        session.commit()
    assert execution.post(path).status_code==409


def test_non_json_secret_fragments_and_response_echo_hidden(execution):
    approve(execution)
    execution.handler=lambda request: httpx.Response(200,text='malformed {"access_token":"new-response-secret"')
    run=execute(execution).json()
    result=execution.get('/api/test-runs/'+run['id'])
    assert 'new-response-secret' not in result.text
    assert all(e['response_body']=='[REDACTED BODY]' for e in result.json()['executions'])


def test_connection_time_rebinding_blocks_before_inner_transport(execution,monkeypatch):
    approve(execution)
    calls=[]
    def factory():
        transport=PinnedTransport(False)
        # No target request is permitted to reach this inner transport.
        transport.inner=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200))
        return transport
    execution.application.state.api_executor.transport_factory=factory
    resolutions=[]
    def dns(*args,**kwargs):
        resolutions.append(True)
        ip='93.184.216.34' if len(resolutions)<=3 else '127.0.0.1'
        return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443))]
    monkeypatch.setattr(socket,'getaddrinfo',dns)
    run=execute(execution).json()
    assert run['summary']['execution_error_cases']==8 and not calls
    detail=execution.get('/api/test-runs/'+run['id']).json()
    assert all(e['result_data']['error_code']=='policy_rejected' for e in detail['executions'])


def test_invalid_json_review_is_atomic_and_configured_non_json_is_failed(execution):
    execution.review['cases'][0]['assertions']=[{'type':'json','path':'data','operator':'exists','expected':True}]
    assert approve(execution).status_code==422
    assert execution.get(execution.plan_path).json()['status']=='ready'
    for case in execution.review['cases']:
        case['assertions']=[{'type':'json','path':'data','operator':'exists'}]
    assert approve(execution).status_code==200
    execution.handler=lambda request: httpx.Response(200,text='plain body')
    run=execute(execution).json()
    assert run['summary']['failed_cases']==8 and run['summary']['execution_error_cases']==0


@pytest.mark.parametrize('url',['http://169.254.1.1','http://[fe80::1]','http://[fd00:ec2::254]',
    'http://[::ffff:127.0.0.1]','http://metadata.google.internal','ftp://qa.example',
    'http://@qa.example','http://qa.example/v1/%2e%2e/admin'])
def test_additional_ssrf_and_base_path_cases_rejected(url):
    with pytest.raises(ValueError):
        validate_base_url(url)


def test_sensitive_query_is_not_exposed_by_http_client_logging(execution,caplog):
    for case in execution.review['cases']:
        case['request_data']['query']['token']='fixture-query-secret'
    approve(execution)
    with caplog.at_level(logging.INFO,logger='httpx'):
        run=execute(execution).json()
    assert run['passed_cases']==8
    assert 'fixture-query-secret' not in caplog.text
    assert sum(getattr(record,'event',None)=='api_http_event' for record in caplog.records)==8


async def test_redacted_body_remains_bounded_and_comparison_uses_raw_values(monkeypatch):
    monkeypatch.setattr('autoqa.api_executor.validate_base_url',lambda *args: None)
    monkeypatch.setattr('autoqa.api_executor.resolve_target',lambda *args: '93.184.216.34')
    raw=json.dumps({'token':'fixture-redaction-token','data':[True]*45000},separators=(',',':')).encode()
    assert len(raw)<256000
    executor=ApiExecutor()
    executor.transport_factory=lambda: httpx.MockTransport(lambda request: httpx.Response(200,content=raw))
    config=CaseConfig.model_validate({'request_data':{},'expected_status':200,'assertions':[
        {'type':'json','path':'data.0','operator':'equals','expected':True}]})
    case=SimpleNamespace(workspace_id='w',plan_id='p',id='c')
    endpoint=SimpleNamespace(method='GET',path='/ok')
    result=await executor.execute(case,endpoint,'https://qa.example',config,'run')
    assert result.status=='passed' and result.result_data['response_body_truncated']
    assert len(result.response_body.encode())<=256000 and 'fixture-redaction-token' not in result.response_body
    assert result.result_data['assertion_results'][1]['actual'] is True


async def test_valid_json_processing_limit_is_not_business_assertion_failure(monkeypatch):
    monkeypatch.setattr('autoqa.api_executor.validate_base_url',lambda *args: None)
    monkeypatch.setattr('autoqa.api_executor.resolve_target',lambda *args: '93.184.216.34')
    body='{"data":'+ '['*66+'true'+']'*66+'}'
    executor=ApiExecutor()
    executor.transport_factory=lambda: httpx.MockTransport(lambda request: httpx.Response(200,content=body))
    config=CaseConfig.model_validate({'request_data':{},'expected_status':200,'assertions':[
        {'type':'json','path':'data','operator':'exists'}]})
    result=await executor.execute(SimpleNamespace(workspace_id='w',plan_id='p',id='c'),
        SimpleNamespace(method='GET',path='/ok'),'https://qa.example',config,'run')
    assert result.status=='execution_error' and result.result_data['error_code']=='response_too_complex'
    assert all(r['status']=='not_evaluated' for r in result.result_data['assertion_results'])
