"""Approved plan -> real pinned transport -> socket target -> persisted results."""
from types import SimpleNamespace

import pytest

from autoqa.api_executor import ApiExecutor, CaseConfig, validate_base_url
from autoqa.target_network import PinnedTransport
from tests.api_execution_support import allow_exact_test_target
from tests.api_execution_support import socket_api as socket_api  # noqa: F401
from tests.api_execution_support import tls_socket_api as tls_socket_api  # noqa: F401
from tests.test_api_execution import approve, execute
from tests.test_api_execution import execution as execution  # noqa: F401
from tests.test_api_execution import imports as imports  # noqa: F401
from tests.test_api_execution import planner as planner  # noqa: F401
from tests.test_api_execution import product as product  # noqa: F401


def prepare(execution,socket_api,monkeypatch,expected=200,assertions=True):
    allow_exact_test_target(monkeypatch,execution.settings,socket_api.url)
    assert execution.patch('/api/projects/'+execution.item['id'],json={
        'name':'socket fixture','project_type':'api','url':socket_api.url+'/v1'}).status_code==200
    for case in execution.review['cases']:
        case['expected_status']=expected
        if assertions:
            case['assertions']=[{'type':'json','path':'data.success','operator':'equals','expected':True},
                               {'type':'json','path':'data.token','operator':'exists'},
                               {'type':'json','path':'data.id','operator':'type','expected':'integer'}]
    execution.application.state.api_executor=ApiExecutor()  # Real PinnedTransport and inner AsyncHTTPTransport.
    assert approve(execution).status_code==200


def test_socket_review_execute_assertions_and_rerun(execution,socket_api,monkeypatch):
    prepare(execution,socket_api,monkeypatch)
    assert socket_api.calls==[]
    model_calls=len(execution.model_calls)
    response=execute(execution)
    assert response.status_code==201,response.text
    first=response.json()
    assert first['summary']['passed_cases']==8 and first['summary']['outcome']=='passed'
    detail=execution.get('/api/test-runs/'+first['id'])
    assert len(socket_api.calls)==8 and {c['path'].split('?')[0] for c in socket_api.calls}=={'/v1/login','/v1/users/1'}
    assert all(c['host']==socket_api.url.removeprefix('http://') for c in socket_api.calls)
    assert any(c['body'] and c['body']['password']=='execution-fixture-secret' for c in socket_api.calls)
    assert 'socket-response-secret' not in detail.text and 'execution-fixture-secret' not in detail.text
    assert all(len(e['result_data']['assertion_results'])==4 for e in detail.json()['executions'])
    second=execution.post('/api/test-runs/'+first['id']+'/rerun')
    assert second.status_code==201 and second.json()['id']!=first['id']
    assert execution.get('/api/test-runs/'+first['id']).json()==detail.json()
    assert len(socket_api.calls)==16 and len(execution.model_calls)==model_calls


@pytest.mark.parametrize('mode',['normal','gzip-normal','deflate-normal'])
@pytest.mark.parametrize('status',[200,422])
def test_real_socket_decoded_responses(execution,socket_api,monkeypatch,mode,status):
    prepare(execution,socket_api,monkeypatch,status)
    socket_api.mode,socket_api.status=mode,status
    run=execute(execution).json()
    detail=execution.get('/api/test-runs/'+run['id']).json()
    assert run['summary']['passed_cases']==8 and run['summary']['execution_error_cases']==0
    assert len(socket_api.calls)==8
    assert all(e['response_status']==status and e['result_data']['diagnostics']['response_headers_observed']
               for e in detail['executions'])


@pytest.mark.parametrize('expected,outcome',[ (200,'failed'),(500,'passed')])
def test_socket_http_500_is_completed_http(execution,socket_api,monkeypatch,expected,outcome):
    prepare(execution,socket_api,monkeypatch,expected)
    socket_api.mode='500'
    run=execute(execution).json()
    assert run['summary']['outcome']==outcome and run['summary']['execution_error_cases']==0
    assert len(socket_api.calls)==8


@pytest.mark.parametrize('mode,code',[('timeout','timeout'),('large','response_too_large'),('gzip','response_too_large')])
def test_socket_execution_errors(execution,socket_api,monkeypatch,mode,code):
    prepare(execution,socket_api,monkeypatch)
    socket_api.mode=mode
    if mode=='timeout':
        monkeypatch.setattr('autoqa.api_executor.CASE_TIMEOUT',1)
    run=execute(execution).json()
    detail=execution.get('/api/test-runs/'+run['id']).json()
    assert run['summary']['execution_error_cases']==8 and run['summary']['failed_cases']==0
    assert run['summary']['pending_cases']==0 and len(socket_api.calls)==8
    assert all(e['status']=='execution_error' and e['result_data']['error_code']==code for e in detail['executions'])
    assert all(e['response_body'] is None for e in detail['executions'])


def test_socket_redirect_not_followed(execution,socket_api,monkeypatch):
    prepare(execution,socket_api,monkeypatch,302)
    socket_api.mode='redirect'
    run=execute(execution).json()
    assert run['summary']['failed_cases']==8 and run['summary']['execution_error_cases']==0
    assert len(socket_api.calls)==8


async def test_socket_health_and_exact_response_boundary(socket_api,monkeypatch):
    settings=SimpleNamespace(env='test')
    allow_exact_test_target(monkeypatch,settings,socket_api.url)
    executor=ApiExecutor()
    case=SimpleNamespace(id='c',plan_id='p',workspace_id='w',name='health')
    endpoint=SimpleNamespace(method='GET',path='/health')
    config=CaseConfig.model_validate({'request_data':{},'expected_status':200})
    result=await executor.execute(case,endpoint,socket_api.url,config,'run')
    assert result.status=='passed' and socket_api.calls[0]['path']=='/health'
    socket_api.mode='boundary'
    result=await executor.execute(case,endpoint,socket_api.url,config,'run')
    assert result.status=='passed' and len(result.response_body)==256_000
    assert isinstance(executor.transport_factory(),PinnedTransport)


def test_default_test_and_production_still_block_localhost(socket_api,monkeypatch):
    for env in ('test','production'):
        with pytest.raises(ValueError):
            validate_base_url(socket_api.url)
        if env=='production':
            with pytest.raises(AssertionError):
                allow_exact_test_target(monkeypatch,SimpleNamespace(env=env),socket_api.url)


def test_socket_tls_verification_failure_is_execution_error(execution,tls_socket_api,monkeypatch):
    prepare(execution,tls_socket_api,monkeypatch)
    run=execute(execution).json()
    assert run['summary']['execution_error_cases']==8 and run['summary']['failed_cases']==0
    detail=execution.get('/api/test-runs/'+run['id']).json()
    assert all(e['result_data']['error_code']=='tls_error' for e in detail['executions'])
    assert tls_socket_api.calls==[]  # TCP/TLS was attempted, but no untrusted HTTP request was sent.
