"""Offline response-shape, accounting and privacy contracts; no live provider."""
import copy
import json
import logging
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from autoqa.planner_diagnostics import BatchDiagnostics, PlannerDiagnostics, numeric_usage
from autoqa.providers import ProviderError
from autoqa.safe_logging import SafeFormatter
from tests.test_api_import import imports as imports  # noqa: F401
from tests.test_product import KEY
from tests.test_product import product as product  # noqa: F401
from tests.test_test_planner import generate, mock_output, six_endpoints
from tests.test_test_planner import planner as planner  # noqa: F401

FIXTURE = Path(__file__).parent / 'fixtures/planner/deepseek-compatible-envelope.json'


@pytest.mark.parametrize('name', ['deepseek', 'openai', 'compatible'])
def test_reconstructed_envelope_uses_same_planner_for_all_providers(planner, name, caplog):
    configured = planner.put('/api/provider', json={'provider': name, 'base_url': 'https://model.example/v1',
                                                  'api_key': KEY, 'model': 'deepseek-chat'})
    assert configured.status_code == 200
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    assert fixture['_fixture']['label'] == 'reconstructed fixture'
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        response = copy.deepcopy(fixture)
        context = json.loads(calls[-1]['messages'][1]['content'])
        output = json.loads(response['choices'][0]['message']['content'])
        original = list(dict.fromkeys(case['endpoint_id'] for case in output['cases']))
        mapping = dict(zip(original, [e['endpoint_id'] for e in context['endpoints']], strict=True))
        for case in output['cases']:
            case['endpoint_id'] = mapping[case['endpoint_id']]
        response['choices'][0]['message']['content'] = json.dumps(output)
        return httpx.Response(200, json=response)
    planner.application.state.gateway.transport_factory = lambda: httpx.MockTransport(handler)
    with caplog.at_level('INFO', logger='autoqa.test_plan_api'):
        response = generate(planner)
    assert response.status_code == 201 and response.json()['case_count'] == 8
    assert len(calls) == 1 and not planner.calls
    record = next(r for r in caplog.records if getattr(r, 'event', None) == 'planner_generation')
    diagnostic = json.loads(SafeFormatter().format(record))['planner']
    assert diagnostic['usage_status'] == 'known' and diagnostic['known_usage'] == fixture['usage']
    assert diagnostic['model_request_count'] == diagnostic['completed_model_requests'] == 1
    assert '_fixture' not in json.dumps(diagnostic) and 'Synthetic' not in json.dumps(diagnostic)


@pytest.mark.parametrize('mode', ['all-known', 'partial', 'missing', 'invalid'])
def test_numeric_usage_is_known_subtotal_without_invented_values(planner, mode):
    spec = six_endpoints(planner)
    calls = []
    def handler(request):
        call = json.loads(request.content)
        calls.append(call)
        output = mock_output(json.loads(call['messages'][1]['content']))
        if len(calls) == 2:
            output['cases'].pop()
        usage = {'prompt_tokens': 12, 'completion_tokens': 23, 'total_tokens': 35, 'Authorization': 'usage-canary'}
        if mode == 'missing' or mode == 'partial' and len(calls) == 2:
            usage = None
        if mode == 'invalid':
            usage = {'prompt_tokens': -12, 'completion_tokens': 23.0, 'total_tokens': True, 'secret': 'usage-canary'}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(output)}}], 'usage': usage})
    planner.application.state.gateway.transport_factory = lambda: httpx.MockTransport(handler)
    response = generate(planner, api_spec_id=spec)
    diagnostics = response.json()['detail']['diagnostics']
    assert len(calls) == 2 and diagnostics['model_request_count'] == 2
    assert 'usage-canary' not in response.text and 'Authorization' not in response.text
    if mode == 'all-known':
        assert diagnostics['known_usage'] == {'prompt_tokens': 24, 'completion_tokens': 46, 'total_tokens': 70}
        assert diagnostics['usage_status'] == 'known'
    elif mode == 'partial':
        assert diagnostics['known_usage'] == {'prompt_tokens': 12, 'completion_tokens': 23, 'total_tokens': 35}
        assert diagnostics['usage_status'] == 'partial'
    else:
        assert diagnostics['known_usage'] is None and diagnostics['usage_status'] == 'unknown'


@pytest.mark.parametrize('error,code', [
    (httpx.ReadTimeout('provider-canary'), 'provider_timeout'),
    (httpx.ConnectError('provider-canary'), 'provider_failure'),
    (ProviderError('provider-canary', code='untrusted-code-canary'), 'provider_failure'),
])
def test_provider_error_and_optional_code_are_safe(planner, error, code, caplog):
    planner.model_error = error
    response = generate(planner)
    assert response.json()['detail']['code'] == code
    assert 'provider-canary' not in response.text and 'untrusted-code-canary' not in response.text
    assert 'provider-canary' not in '\n'.join(SafeFormatter().format(r) for r in caplog.records)
    assert len(planner.model_calls) == 1
    assert str(ProviderError('legacy message')) == 'legacy message'
    assert ProviderError('legacy message').code is None


def test_sanitizer_and_formatter_reject_unallowlisted_fields():
    assert numeric_usage({'total_tokens': 0, 'prompt_tokens': False, 'completion_tokens': -1,
                          'other': 'log-canary'}) == {'total_tokens': 0}
    assert numeric_usage({'total_tokens': 1.0}) is None
    diagnostic = PlannerDiagnostics(plan_id='trusted-plan', batch_count=1, max_model_requests=1,
        batches=[BatchDiagnostics(batch_index=1, endpoint_ids=['trusted-endpoint'])])
    record = logging.LogRecord('autoqa.test_plan_api', logging.INFO, '', 0, 'log-canary', (), None)
    record.event, record.planner_diagnostics = 'planner_generation', diagnostic
    record.raw_response = 'log-canary'
    encoded = SafeFormatter().format(record)
    assert 'log-canary' not in encoded and 'raw_response' not in encoded
    assert json.loads(encoded)['planner']['known_usage'] is None
    with pytest.raises(ValidationError):
        PlannerDiagnostics(batch_count=1, max_model_requests=1, secret='log-canary')
    diagnostic.batches[0].usage = {'unknown': 'log-canary'}
    assert 'planner' not in json.loads(SafeFormatter().format(record))


def test_gateway_without_metadata_reports_unknown_request_counts():
    diagnostic = PlannerDiagnostics(batch_count=1, max_model_requests=1,
        batches=[BatchDiagnostics(batch_index=1, endpoint_ids=['trusted-endpoint'],
                                 model_request_count=None, completed_model_requests=None)])
    assert diagnostic.public()['model_request_count'] is None
    assert diagnostic.public()['known_usage'] is None
