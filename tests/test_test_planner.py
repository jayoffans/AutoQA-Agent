"""AI planner acceptance: real gateway + fake HTTP; never a real LLM or target request."""
import asyncio
import copy
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autoqa.app import create_app
from autoqa.planner_diagnostics import PlannerDiagnostics
from autoqa.product_models import ApiEndpointRow, ApiSpecRow
from autoqa.product_models import TestCase as Case
from autoqa.product_models import TestPlan as Plan
from autoqa.safe_logging import SafeFormatter
from autoqa.store import TaskRow
from autoqa.test_planner import CATEGORIES, PlannerOutput, PlanningError, TestPlannerAgent
from tests.test_api_import import SPEC, create_project, upload
from tests.test_api_import import imports as imports  # noqa: F401
from tests.test_auth import bearer, sign_up
from tests.test_product import KEY, provider
from tests.test_product import product as product  # noqa: F401


def mock_output(context):
    return {"cases": [{"endpoint_id": endpoint["endpoint_id"], "name": "验证 " + category,
        "category": category, "priority": "P1", "description": "依据接口契约验证输入和响应",
        "steps": ["准备测试数据", "按契约构造请求", "检查预期响应"], "expected_result": "响应符合接口契约"}
        for endpoint in context["endpoints"] for category in sorted(CATEGORIES)]}


@pytest.fixture
def planner(imports):
    imports.spec_id = upload(imports).json()["spec"]["id"]
    provider(imports)
    imports.model_calls = []
    imports.change_output = lambda output: output
    imports.model_error = None
    def handler(request):
        assert request.url.host == "model.example" and request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer " + KEY
        body = json.loads(request.content)
        imports.model_calls.append(body)
        if imports.model_error:
            raise imports.model_error
        context = json.loads(body["messages"][1]["content"])
        output = imports.change_output(mock_output(context))
        content = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    imports.application.state.gateway.transport_factory = lambda: httpx.MockTransport(handler)
    return imports


def generate(client, **data):
    return client.post(client.prefix + "/test-plans/generate", json=data)


def counts(client):
    with Session(client.application.state.store.engine) as session:
        return tuple(session.scalar(select(func.count()).select_from(row)) for row in (Plan, Case, TaskRow))


def test_mock_gateway_generates_and_saves_complete_cases(planner):
    response = generate(planner)
    assert response.status_code == 201, response.text
    plan = response.json()
    assert plan["status"] == "ready" and plan["case_count"] == 8
    assert plan["provider"] == "compatible" and plan["model"] == "fixture-model"
    assert plan["category_counts"] == dict.fromkeys(CATEGORIES, 2)
    assert plan["created_at"].endswith('Z') and plan["updated_at"].endswith('Z')
    assert counts(planner) == (1, 8, 0)
    assert len(planner.model_calls) == 1 and planner.calls == []
    cases = planner.get('/api/test-plans/' + plan["id"] + '/cases').json()
    assert all(case['created_at'].endswith('Z') for case in cases)
    with Session(planner.application.state.store.engine) as session:
        saved = session.get(Plan, plan["id"])
        assert saved.created_by == planner.get('/api/auth/me').json()["user_id"]
        assert saved.workspace_id == plan["workspace_id"] and saved.api_spec_id == planner.spec_id
        assert saved.created_at and saved.updated_at
        endpoints = set(session.scalars(select(ApiEndpointRow.id)))
        assert {c["endpoint_id"] for c in cases} == endpoints
        assert all(c["steps"] == ["准备测试数据", "按契约构造请求", "检查预期响应"] for c in cases)
        assert all(c["workspace_id"] == saved.workspace_id and c["plan_id"] == saved.id for c in cases)


def test_plan_get_list_and_reload_persist(planner):
    plan = generate(planner).json()
    assert planner.get('/api/test-plans/' + plan['id']).json() == plan
    assert planner.get(planner.prefix + '/test-plans').json() == [plan]
    from fastapi.testclient import TestClient
    with TestClient(create_app(planner.settings), base_url="https://testserver") as restarted:
        restarted.cookies.update(planner.cookies)
        assert restarted.get('/api/test-plans/' + plan['id']).json()['case_count'] == 8
        assert len(restarted.get('/api/test-plans/' + plan['id'] + '/cases').json()) == 8


def test_no_model_configuration(imports):
    upload(imports)
    response = generate(imports)
    assert response.status_code == 422 and '模型中心' in response.text
    assert counts(imports) == (0, 0, 0) and imports.calls == []


def test_no_imported_spec_or_endpoints(imports):
    provider(imports)
    assert generate(imports).status_code == 422
    assert counts(imports) == (0, 0, 0)


def test_spec_selection_belongs_to_project(planner):
    other = create_project(planner)
    spec = upload(planner, prefix=f'/api/projects/{other["id"]}').json()['spec']['id']
    assert generate(planner, api_spec_id=spec).status_code == 422
    assert generate(planner, api_spec_id=str(uuid4())).status_code == 422
    assert counts(planner) == (0, 0, 0) and not planner.model_calls


def test_explicit_spec_and_latest_default(planner):
    second = upload(planner).json()['spec']['id']
    assert generate(planner).json()['api_spec_id'] == second
    assert generate(planner, api_spec_id=planner.spec_id).json()['api_spec_id'] == planner.spec_id


def test_web_or_archived_project_rejected(planner):
    web = create_project(planner, project_type='web', url='https://qa.example')
    assert planner.post(f'/api/projects/{web["id"]}/test-plans/generate').status_code == 422
    assert planner.delete('/api/projects/' + planner.item['id']).status_code == 204
    assert generate(planner).status_code == 404 and not planner.model_calls


@pytest.mark.parametrize('suffix', ['', '/cases'])
def test_foreign_plan_isolation(planner, suffix):
    plan = generate(planner).json()
    second = bearer(planner, sign_up(planner, email='other-planner@example.com')['access_token'])
    assert second.get('/api/test-plans/' + plan['id'] + suffix).status_code == 404
    assert second.get(planner.prefix + '/test-plans').status_code == 404
    assert second.post(planner.prefix + '/test-plans/generate').status_code == 404


@pytest.mark.parametrize('path', ['/api/test-plans/unknown', '/api/test-plans/unknown/cases'])
def test_missing_and_unauthenticated_plan(planner, path):
    assert planner.get(path).status_code == 404
    planner.cookies.clear()
    assert planner.get(path).status_code == 401


@pytest.mark.parametrize('mutation', [
    lambda o: {}, lambda o: {**o, 'extra': True}, lambda o: {'cases': []},
    lambda o: {'cases': 'natural language'}, lambda o: {'cases': [dict(o['cases'][0], priority='urgent')]},
    lambda o: {'cases': [dict(o['cases'][0], category='execution')]},
    lambda o: {'cases': [dict(o['cases'][0], name='  ')]},
    lambda o: {'cases': [dict(o['cases'][0], description=3)]},
    lambda o: {'cases': [dict(o['cases'][0], steps=[])]},
    lambda o: {'cases': [dict(o['cases'][0], steps=[{'action': 'execute'}])]},
    lambda o: {'cases': [dict(o['cases'][0], expected_result='')]},
    lambda o: {'cases': [dict(o['cases'][0], code='print(1)')]},
    lambda o: {'cases': [dict(o['cases'][0], name='x' * 161)]},
    lambda o: {'cases': [dict(o['cases'][0], endpoint_id=str(uuid4()))] + o['cases'][1:]},
    lambda o: {'cases': o['cases'][:-1]}, lambda o: {'cases': o['cases'] + [o['cases'][0]]},
    lambda o: 'This is not JSON', lambda o: '[1, 2]',
], ids=['missing-cases','extra','empty','text','priority','category','blank','type','no-steps','executable-step',
        'no-expected','extra-code','long-name','foreign-endpoint','coverage','duplicate','invalid-json','array'])
def test_invalid_model_output_never_saves_cases(planner, mutation):
    planner.change_output = mutation
    response = generate(planner)
    assert response.status_code == 502 and '未保存任何测试用例' in response.text
    plan_id = response.json()['detail']['plan_id']
    assert planner.get('/api/test-plans/' + plan_id).json()['status'] == 'failed'
    assert planner.get('/api/test-plans/' + plan_id + '/cases').json() == []
    assert counts(planner) == (1, 0, 0)


@pytest.mark.parametrize('error', [httpx.ConnectError(KEY), httpx.ReadTimeout(KEY), RuntimeError(KEY)])
def test_exception_sanitized_and_failure_retry(planner, error, caplog):
    planner.model_error = error
    response = generate(planner)
    assert response.status_code == 502
    assert KEY not in response.text and KEY not in caplog.text
    assert counts(planner) == (1, 0, 0)
    planner.model_error = None
    assert generate(planner).json()['status'] == 'ready'
    assert counts(planner) == (2, 8, 0)


def test_prompt_schema_refs_and_secret_redaction(planner, caplog):
    def echo(output):
        output['cases'][0]['description'] = 'echo ' + KEY
        return output
    planner.change_output = echo
    plan = generate(planner).json()
    call = planner.model_calls[0]
    prompt = call['messages'][0]['content']
    context = json.loads(call['messages'][1]['content'])
    assert all(category in prompt for category in CATEGORIES)
    assert '不可信' in prompt and 'JSON' in prompt and 'PlannerOutput' in prompt
    assert call['response_format'] == {'type': 'json_object'}
    assert context['schema_definitions']['schemas']['Login']['required'] == ['username']
    assert context['endpoints'][0]['request_schema']['content']['application/json']['schema']['$ref']
    cases = planner.get('/api/test-plans/' + plan['id'] + '/cases')
    assert KEY not in cases.text and '[REDACTED]' in cases.text and KEY not in caplog.text
    assert KEY not in json.dumps(call) and 'encrypted_api_key' not in json.dumps(call)


def test_active_session_configuration_used(planner):
    provider(planner, save_mode='session')
    assert generate(planner).json()['status'] == 'ready'


def test_mock_provider_forbidden_in_production(planner, monkeypatch):
    original = planner.application.state.gateway.config
    monkeypatch.setattr(planner.application.state.gateway, 'config',
                       lambda *args: {**original(*args), 'provider': 'mock'})
    assert generate(planner).status_code == 502 and not planner.model_calls
    assert counts(planner) == (1, 0, 0)


def test_batched_generation_is_atomic(planner):
    spec = copy.deepcopy(SPEC)
    spec['paths']['/third'] = copy.deepcopy(spec['paths']['/users/{id}'])
    del spec['paths']['/third']['parameters']
    upload(planner, spec=spec)
    def change(output):
        return output if len(planner.model_calls) == 1 else {'cases': []}
    planner.change_output = change
    assert generate(planner).status_code == 502
    assert len(planner.model_calls) == 2 and counts(planner) == (1, 0, 0)


def test_case_insert_failure_rolls_back_and_marks_failed(planner):
    def reject(*args):
        raise RuntimeError(KEY)
    event.listen(Case, 'before_insert', reject)
    try:
        response = generate(planner)
        assert response.status_code == 502
        assert response.json()['detail']['code'] == 'persistence_failure'
        assert response.json()['detail']['diagnostics']['persisted_case_count'] == 0
        assert counts(planner) == (1, 0, 0)
        assert planner.get(planner.prefix + '/test-plans').json()[0]['status'] == 'failed'
    finally:
        event.remove(Case, 'before_insert', reject)


def test_schema_is_strict_and_json_compatible():
    output = mock_output({'endpoints': [{'endpoint_id': str(uuid4())}]})
    assert len(PlannerOutput.model_validate(output).cases) == 4
    output['cases'][0]['steps'] = [42]
    with pytest.raises(ValidationError):
        PlannerOutput.model_validate(output)


async def test_agent_timeout_is_friendly(monkeypatch):
    class SlowGateway:
        async def generate(self, *args, **kwargs):
            await asyncio.sleep(1)
    monkeypatch.setattr('autoqa.test_planner.PLANNER_TIMEOUT', 0.001)
    with pytest.raises(PlanningError, match='未保存任何测试用例'):
        await TestPlannerAgent(SlowGateway()).generate('w', 's', [{'endpoint_id': 'e'}], {}, [], {})


def test_generating_state_visible_during_model_call(planner):
    def inspect_output(output):
        with Session(planner.application.state.store.engine) as session:
            state = session.scalar(select(Plan))
            assert state.status == 'generating'
            assert session.scalar(select(func.count()).select_from(Case)) == 0
        return output
    planner.change_output = inspect_output
    assert generate(planner).status_code == 201


def test_restart_marks_interrupted_generation_failed(planner):
    plan = generate(planner).json()
    with Session(planner.application.state.store.engine) as session:
        # Simulate interruption before a case transaction was started.
        failed = Plan(workspace_id=plan['workspace_id'], project_id=plan['project_id'], api_spec_id=plan['api_spec_id'],
                      provider=plan['provider'], model=plan['model'], created_by=plan['created_by'], status='generating')
        session.add(failed)
        session.commit()
        failed_id = failed.id
    from fastapi.testclient import TestClient
    with TestClient(create_app(planner.settings), base_url='https://testserver') as restarted:
        restarted.cookies.update(planner.cookies)
        assert restarted.get('/api/test-plans/' + failed_id).json()['status'] == 'failed'
        assert restarted.get('/api/test-plans/' + plan['id']).json()['status'] == 'ready'


def test_referenced_spec_deletion_friendly_and_preserves_cases(planner):
    generate(planner)
    assert planner.delete(planner.prefix + '/api-specs/' + planner.spec_id).status_code == 409
    assert counts(planner) == (1, 8, 0)
    with Session(planner.application.state.store.engine) as session:
        assert session.get(ApiSpecRow, planner.spec_id) is not None


def test_database_rejects_invalid_status(planner):
    plan = generate(planner).json()
    with Session(planner.application.state.store.engine) as session:
        with pytest.raises(IntegrityError):
            session.execute(update(Plan).where(Plan.id == plan['id']).values(status='executing'))
            session.commit()


def test_endpoint_limit_prevents_model_calls(planner):
    with Session(planner.application.state.store.engine) as session:
        row = session.get(ApiSpecRow, planner.spec_id)
        session.add_all(ApiEndpointRow(workspace_id=row.workspace_id, project_id=row.project_id, spec_id=row.id,
            method='GET', path=f'/extra/{i}', summary='', parameters=[], request_schema={}, response_schema={})
            for i in range(19))
        session.commit()
    assert generate(planner).status_code == 422
    assert not planner.model_calls and counts(planner) == (0, 0, 0)


def test_context_size_limit_before_any_model_call(planner, monkeypatch):
    monkeypatch.setattr('autoqa.test_planner.MAX_CONTEXT_BYTES', 100)
    assert generate(planner).status_code == 502
    assert counts(planner) == (1, 0, 0) and not planner.model_calls


def test_input_extra_fields_rejected_before_generation(planner):
    assert generate(planner, provider='openai', api_key=KEY).status_code == 422
    assert counts(planner) == (0, 0, 0) and not planner.model_calls


def test_empty_endpoint_document_rejected(planner):
    from sqlalchemy import delete
    with Session(planner.application.state.store.engine) as session:
        session.execute(delete(ApiEndpointRow).where(ApiEndpointRow.spec_id == planner.spec_id))
        session.commit()
    assert generate(planner).status_code == 422
    assert counts(planner) == (0, 0, 0) and not planner.model_calls


@pytest.mark.parametrize('status', [401, 429, 500])
def test_provider_http_failure_never_exposes_body(planner, status, caplog):
    planner.application.state.gateway.transport_factory = lambda: httpx.MockTransport(
        lambda request: httpx.Response(status, text=KEY))
    response = generate(planner)
    assert response.status_code == 502
    assert counts(planner) == (1, 0, 0)
    assert KEY not in response.text and KEY not in caplog.text


def counted_output(context, totals):
    categories = sorted(CATEGORIES)
    return {'cases': [{'endpoint_id': endpoint['endpoint_id'], 'name': f'contract case {i}',
        'category': categories[i % 4], 'priority': 'P1', 'description': 'Synthetic contract scenario',
        'steps': ['Review the request against the contract'], 'expected_result': 'Confirm the contract'}
        for endpoint, total in zip(context['endpoints'], totals, strict=True) for i in range(total)]}


def six_endpoints(client):
    spec = copy.deepcopy(SPEC)
    for i in range(4):
        spec['paths'][f'/extra-{i}/{{id}}'] = copy.deepcopy(spec['paths']['/users/{id}'])
    response = upload(client, spec=spec)
    assert response.status_code == 201
    return response.json()['spec']['id']


@pytest.mark.parametrize('totals', [(4, 4), (5, 6), (8, 12), (12, 4), (12, 12)])
def test_valid_quantities_remain_ready_without_exact_four_requirement(planner, totals):
    planner.change_output = lambda output: counted_output({'endpoints': [
        {'endpoint_id': output['cases'][0]['endpoint_id']}, {'endpoint_id': output['cases'][4]['endpoint_id']}]}, totals)
    response = generate(planner)
    assert response.status_code == 201
    assert response.json()['status'] == 'ready'
    assert response.json()['case_count'] == sum(totals)
    assert len(planner.model_calls) == 1


@pytest.mark.parametrize('scenario,code', [
    ('missing-one', 'missing_category_coverage'), ('missing-many', 'missing_category_coverage'),
    ('missing-endpoint', 'missing_category_coverage'), ('13+4', 'endpoint_case_limit'),
    ('25', 'schema_validation'), ('json', 'json_parse'), ('category', 'schema_validation'),
    ('unknown', 'unknown_endpoint'), ('duplicate', 'duplicate_case'),
])
def test_classified_validation_failures_and_safe_counts(planner, scenario, code, caplog):
    canary = 'scenario-canary-never-in-diagnostics'
    foreign = str(uuid4())
    def change(output):
        if scenario == 'json':
            return canary
        if scenario in {'13+4', '25'}:
            output = counted_output({'endpoints': [{'endpoint_id': output['cases'][0]['endpoint_id']},
                {'endpoint_id': output['cases'][4]['endpoint_id']}]}, (13, 4) if scenario == '13+4' else (13, 12))
        elif scenario == 'missing-one':
            output['cases'].pop()
        elif scenario == 'missing-many':
            output['cases'] = output['cases'][:5]
        elif scenario == 'missing-endpoint':
            output['cases'] = output['cases'][:4]
        elif scenario == 'category':
            output['cases'][0]['category'] = canary
        elif scenario == 'unknown':
            output['cases'][0]['endpoint_id'] = foreign
        elif scenario == 'duplicate':
            output['cases'].append(copy.deepcopy(output['cases'][0]))
        output['cases'][0]['description'] = canary
        return output
    planner.change_output = change
    response = generate(planner)
    assert response.status_code == 502
    detail = response.json()['detail']
    assert detail['code'] == code and detail['diagnostics']['error_code'] == code
    assert detail['diagnostics']['persisted_case_count'] == 0
    assert detail['diagnostics']['model_request_count'] == 1
    assert detail['diagnostics']['completed_model_requests'] == 1
    assert detail['diagnostics']['known_usage'] is None and detail['diagnostics']['usage_status'] == 'unknown'
    assert len(planner.model_calls) == 1 and counts(planner) == (1, 0, 0)
    assert canary not in response.text and foreign not in response.text
    safe_logs = '\n'.join(SafeFormatter().format(record) for record in caplog.records)
    assert canary not in safe_logs and foreign not in safe_logs
    batch = detail['diagnostics']['batches'][0]
    if scenario == '13+4':
        assert batch['candidate_case_count'] == 17
        assert [e['case_count'] for e in batch['endpoints']] == [13, 4]
        assert all(not e['missing_categories'] for e in batch['endpoints'])
    if scenario.startswith('missing'):
        assert batch['endpoints'][1]['missing_categories']
    if scenario in {'category', 'unknown', '25', 'json'}:
        assert batch['candidate_case_count'] is None and not batch['endpoints']


@pytest.mark.parametrize('failure', ['limit', 'coverage'])
def test_second_of_three_batches_fails_atomically_without_retry(planner, failure):
    spec_id = six_endpoints(planner)
    def change(output):
        if len(planner.model_calls) == 2:
            if failure == 'coverage':
                return {'cases': output['cases'][:-1]}
            return counted_output({'endpoints': [{'endpoint_id': output['cases'][0]['endpoint_id']},
                {'endpoint_id': output['cases'][4]['endpoint_id']}]}, (13, 4))
        return output
    planner.change_output = change
    response = generate(planner, api_spec_id=spec_id)
    assert response.status_code == 502
    diagnostics = response.json()['detail']['diagnostics']
    assert diagnostics['max_model_requests'] == 3
    assert diagnostics['model_request_count'] == diagnostics['completed_model_requests'] == 2
    assert diagnostics['batches'][0]['candidate_case_count'] == 8
    assert diagnostics['batches'][2]['model_request_count'] == 0
    assert diagnostics['persisted_case_count'] == 0 and counts(planner) == (1, 0, 0)
    assert len(planner.model_calls) == 2 and not planner.calls
    plan_id = response.json()['detail']['plan_id']
    assert planner.get('/api/test-plans/' + plan_id).json()['status'] == 'failed'


def test_six_endpoints_exactly_three_calls_and_explicit_batch_budgets(planner, caplog):
    spec_id = six_endpoints(planner)
    with caplog.at_level('INFO', logger='autoqa.test_plan_api'):
        response = generate(planner, api_spec_id=spec_id)
    assert response.status_code == 201
    assert response.json()['status'] == 'ready' and response.json()['case_count'] == 24
    assert len(planner.model_calls) == 3 and counts(planner) == (1, 24, 0)
    for call in planner.model_calls:
        prompt = call['messages'][0]['content']
        assert '目标总数为8条' in prompt and '优先每接口正好4条' in prompt
        assert '所有类别合计不超过12条' in prompt and '不是每类别最多12条' in prompt
        assert 'name 必须唯一' in prompt and '待人工确认' in prompt
    record = next(r for r in caplog.records if getattr(r, 'event', None) == 'planner_generation')
    diagnostics = json.loads(SafeFormatter().format(record))['planner']
    assert diagnostics['model_request_count'] == 3 and diagnostics['persisted_case_count'] == 24


def test_single_endpoint_target_four(planner):
    from sqlalchemy import delete
    with Session(planner.application.state.store.engine) as session:
        endpoint = session.scalar(select(ApiEndpointRow).order_by(ApiEndpointRow.path))
        session.execute(delete(ApiEndpointRow).where(ApiEndpointRow.id != endpoint.id))
        session.commit()
    assert generate(planner).json()['case_count'] == 4
    assert '目标总数为4条' in planner.model_calls[0]['messages'][0]['content']
    assert len(planner.model_calls) == 1


@pytest.mark.parametrize('succeeds', [True, False])
def test_manual_regeneration_creates_new_plan_and_never_reuses_candidates(planner, succeeds):
    planner.change_output = lambda output: {'cases': output['cases'][:-1]}
    first = generate(planner).json()['detail']['plan_id']
    planner.change_output = (lambda output: output) if succeeds else planner.change_output
    second_response = generate(planner)
    second = second_response.json()['id'] if succeeds else second_response.json()['detail']['plan_id']
    assert first != second and len(planner.model_calls) == 2
    assert planner.get('/api/test-plans/' + first).json()['status'] == 'failed'
    assert planner.get('/api/test-plans/' + first + '/cases').json() == []
    assert counts(planner) == (2, 8 if succeeds else 0, 0)


async def test_late_context_limit_preflight_makes_zero_gateway_calls(monkeypatch):
    class FakeGateway:
        calls = 0
        async def generate(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError('Must not call any model')
    endpoints = [{'endpoint_id': f'e{i}', 'summary': 'x' * (1000 if i == 5 else 1)} for i in range(6)]
    diagnostics = PlannerDiagnostics(batch_count=3, max_model_requests=3)
    monkeypatch.setattr('autoqa.test_planner.MAX_CONTEXT_BYTES', 500)
    gateway = FakeGateway()
    with pytest.raises(PlanningError) as error:
        await TestPlannerAgent(gateway).generate('w', 's', endpoints, {}, [], {}, diagnostics=diagnostics)
    assert error.value.code == 'context_too_large' and gateway.calls == 0
    assert len(diagnostics.batches) == 3 and diagnostics.public()['model_request_count'] == 0
