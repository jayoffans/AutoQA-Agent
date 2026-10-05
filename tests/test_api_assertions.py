"""Contract and raw JSON comparison tests; no HTTP or model dependency."""
import json
import logging

import pytest
from pydantic import ValidationError

from autoqa.api_assertions import assertion_results, json_equal
from autoqa.api_executor import CaseConfig, validate_base_structure
from autoqa.execution_privacy import (
    config_secrets,
    public_config,
    redact_assertions,
    safe_text,
    sensitive_values,
)
from autoqa.safe_logging import private_http_logs
from autoqa.security import origin


def config(assertions=()):
    return CaseConfig.model_validate({"request_data": {}, "expected_status": 200, "assertions": list(assertions)})


@pytest.mark.parametrize('left,right,equal', [
    (True,1,False),(False,0,False),(1,1.0,True),('1',1,False),(None,None,True),
    ({'b':2,'a':[True]}, {'a':[True],'b':2.0},True),([1,2],[2,1],False),([True],[1],False),
])
def test_json_equality(left,right,equal):
    assert json_equal(left,right) is equal


@pytest.mark.parametrize('path,operator,expected,passed,exists', [
    ('data.null','exists',None,True,True),('data.absent','exists',None,False,False),
    ('users.0.id','equals',1.0,True,True),('users.1.id','exists',None,False,False),
    ('data.ok','equals',1,False,True),('data.ok','type','boolean',True,True),
    ('users.0.id','type','number',True,True),('users.0.id','type','integer',True,True),
    ('data.f','type','integer',False,True),('data.null','equals',None,True,True),
])
def test_paths_and_operators(path,operator,expected,passed,exists):
    assertion={'type':'json','path':path,'operator':operator}
    if operator != 'exists':
        assertion['expected']=expected
    results=assertion_results(200,config([assertion]).assertions,200,
        json.dumps({'data':{'null':None,'ok':True,'f':1.0},'users':[{'id':1}]}))
    assert results[1]['status'] == ('passed' if passed else 'failed')
    assert results[1]['actual_exists'] is exists


@pytest.mark.parametrize('assertion', [
    {'type':'json','path':'data.x','operator':'equals'},
    {'type':'json','path':'data.x','operator':'exists','expected':True},
    {'type':'json','path':'data.x','operator':'type','expected':'bool'},
    {'type':'json','path':'users[0]','operator':'exists'},
    {'type':'json','path':'data..x','operator':'exists'},
    {'type':'json','path':'*','operator':'exists'},
    {'type':'json','path':'data','operator':'matches','expected':'x'},
    {'type':'json','path':'data','operator':'equals','expected':float('nan')},
])
def test_invalid_assertions_rejected(assertion):
    with pytest.raises(ValidationError):
        config([assertion])


@pytest.mark.parametrize('body', ['text','{broken','{"data":NaN}','{"data":1e999}',
                                r'{"data":"\ud800"}',r'{"\ud800":true}'])
def test_invalid_json_is_assertion_failure(body):
    c=config([{'type':'json','path':'data','operator':'exists'}])
    results=assertion_results(200,c.assertions,200,body)
    assert results[0]['status']=='passed'
    assert results[1]['reason_code']=='invalid_json'


def test_all_results_and_http_incomplete():
    c=config([{'type':'json','path':'data','operator':'exists'}])
    assert all(r['status']=='not_evaluated' for r in assertion_results(200,c.assertions))
    assert [r['status'] for r in assertion_results(200,c.assertions,500,'{"data":true}')] == ['failed','passed']
    assert assertion_results(302,[],302,'')[0]['reason_code']=='redirect_blocked'
    assert CaseConfig.model_validate({'request_data':{},'expected_status':200}).assertions == []


def test_sensitive_raw_comparison_then_masked_evidence():
    value='fixture-token-equality'
    c=config([{'type':'json','path':'data.token','operator':'equals','expected':value}])
    results=assertion_results(200,c.assertions,200,json.dumps({'data':{'token':value}}))
    assert results[1]['status']=='passed'
    assert value not in json.dumps(redact_assertions(results,config_secrets(c)))
    assert public_config(c)['assertions'][0]['expected']=='[REDACTED]'
    mismatch=assertion_results(200,c.assertions,200,'{"data":{"token":"different-token"}}')
    assert mismatch[1]['status']=='failed'


def test_short_and_common_values_do_not_globally_replace():
    values={'password':'a','token':'true','secret':'password','authorization':'Bearer abc','api_key':'fixture-long-secret'}
    hidden=sensitive_values(values)
    assert hidden == ['fixture-long-secret']
    assert sensitive_values({'token':'123456789012','secret':'aaaaaaaaaaaaaaaa','password':'passwordpassword'})==[]
    # The raw bearer header can be hidden; its short token must never replace unrelated text.
    assert safe_text('an abc example with password words', hidden)=='an abc example with password words'
    assert safe_text('invalid {"access_token":"fixture-leak"}',[])=='[REDACTED BODY]'


def test_assertion_display_bounded_without_affecting_comparison():
    c=config([{'type':'json','path':'data','operator':'equals','expected':'x'*6000}])
    raw=assertion_results(200,c.assertions,200,json.dumps({'data':'x'*6000}))
    clean=redact_assertions(raw,[])
    assert clean[1]['status']=='passed' and clean[1]['actual_truncated'] and clean[1]['expected_truncated']
    assert len(clean[1]['actual'].encode())<=4096


@pytest.mark.parametrize('suffix', ['/a/../b','/%2e%2e/b','/%252e%252e/b','/%0a','/%5cfoo','/a/./b'])
def test_base_path_traversal_and_controls(suffix):
    with pytest.raises(ValueError):
        validate_base_structure('https://qa.example'+suffix)


@pytest.mark.parametrize('url',['http://@qa.example','http://:@qa.example','http://user@qa.example'])
def test_all_userinfo_rejected(url):
    with pytest.raises(ValueError):
        origin(url)


def test_private_http_logging_is_scoped_and_restored(caplog):
    logger=logging.getLogger('httpx')
    with caplog.at_level(logging.INFO,logger='httpx'):
        with private_http_logs():
            logger.info('private payload %s','fixture-context-secret')
        logger.info('ordinary HTTP event')
    assert 'fixture-context-secret' not in caplog.text and 'ordinary HTTP event' in caplog.text
