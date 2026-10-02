"""Additive API execution migration and SQLite/MySQL scope constraints."""
import json

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from autoqa.migrations import upgrade
from autoqa.product_models import TestExecution as Execution
from autoqa.product_models import TestRun as Run
from tests.test_production_compatibility import cli_migrate
from tests.test_production_compatibility import compat_settings as compat_settings  # noqa: F401


def test_execution_additive_migration_preserves_ready_plans_and_legacy_runs(compat_settings):
    cli_migrate(compat_settings, '0007_test_planner')
    engine = create_engine(compat_settings.database_url, hide_parameters=True)
    try:
        # Use revision-7 SQL, so this tests an actual populated upgrade rather than metadata.create_all.
        with engine.begin() as c:
            c.execute(text("INSERT INTO users (id,email,password_hash,nickname,is_active,created_at,updated_at) "
                "VALUES ('u','execution-old@example.com','preserved-hash','old',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
            c.execute(text("INSERT INTO workspaces (id,name,owner_id,created_at,updated_at) "
                "VALUES ('w','old workspace','u',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
            c.execute(text("INSERT INTO projects (id,workspace_id,name,description,url,builtin,archived,project_type,"
                "environment,created_at,updated_at) VALUES ('p','w','old API','','https://qa.example',0,0,'api',"
                "'testing',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
            c.execute(text("INSERT INTO api_specs (id,workspace_id,project_id,name,version,format,raw_content,created_at,updated_at) "
                "VALUES ('s','w','p','spec','1','json',:raw,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"), {'raw':json.dumps({})})
            c.execute(text("INSERT INTO api_endpoints (id,workspace_id,project_id,spec_id,method,path,summary,parameters,"
                "request_schema,response_schema,created_at) VALUES ('e','w','p','s','GET','/ok','old endpoint',"
                "'[]','{}','{}',CURRENT_TIMESTAMP)"))
            c.execute(text("INSERT INTO ai_test_plans (id,workspace_id,project_id,api_spec_id,status,provider,model,"
                "created_by,created_at,updated_at) VALUES ('plan','w','p','s','ready','compatible','fixture','u',"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
            c.execute(text("INSERT INTO ai_test_cases (id,workspace_id,plan_id,endpoint_id,name,category,priority,"
                "description,steps,expected_result,created_at) VALUES ('case','w','plan','e','old case','positive',"
                "'P1','preserved','[]','preserved',CURRENT_TIMESTAMP)"))
        cli_migrate(compat_settings, '0008_api_execution')
        with engine.begin() as c:
            c.execute(text("INSERT INTO api_test_runs (id,workspace_id,project_id,plan_id,status,total_cases,"
                "passed_cases,failed_cases,started_at,finished_at,created_by) VALUES "
                "('old-run','w','p','plan','failed',1,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'u')"))
            c.execute(text("INSERT INTO api_test_executions (id,workspace_id,plan_id,run_id,case_id,method,url,"
                "request_data,passed,error_message,duration_ms) VALUES "
                "('old-execution','w','plan','old-run','case','GET','https://qa.example/ok','{}',0,'HTTP 请求失败',5)"))
            old_run = dict(c.execute(text("SELECT * FROM api_test_runs WHERE id='old-run'")).mappings().one())
            old_execution = dict(c.execute(text("SELECT * FROM api_test_executions WHERE id='old-execution'")).mappings().one())
        before = set(inspect(engine).get_table_names())
        upgrade(engine)
        upgrade(engine)
        inspector = inspect(engine)
        assert before <= set(inspector.get_table_names())
        assert {'api_test_runs','api_test_executions','test_runs','reports'} <= set(inspector.get_table_names())
        assert {c['name'] for c in inspector.get_columns('api_test_runs')} == {
            'id','workspace_id','project_id','plan_id','active_plan_id','status','total_cases','passed_cases',
            'failed_cases','started_at','finished_at','created_by'}
        assert {fk['referred_table'] for fk in inspector.get_foreign_keys('api_test_executions')} == {
            'workspaces','api_test_runs','ai_test_cases'}
        with engine.connect() as c:
            assert c.scalar(text('SELECT version_num FROM alembic_version')) == '0010_api_reports'
            assert c.execute(text('SELECT status,approved_base_url FROM ai_test_plans')).one() == ('ready',None)
            assert c.scalar(text('SELECT execution_config FROM ai_test_cases')) is None
            assert c.scalar(text('SELECT password_hash FROM users')) == 'preserved-hash'
            assert dict(c.execute(text("SELECT * FROM api_test_runs WHERE id='old-run'")).mappings().one()) == old_run
            saved_old = dict(c.execute(text("SELECT * FROM api_test_executions WHERE id='old-execution'")).mappings().one())
            assert saved_old.pop('status') is None and saved_old.pop('result_data') is None
            assert saved_old == old_execution
        with Session(engine) as session:
            run = Run(workspace_id='w',project_id='p',plan_id='plan',active_plan_id='plan',
                      total_cases=1,created_by='u')
            session.add(run)
            session.flush()
            run_id = run.id
            session.add(Execution(workspace_id='w',plan_id='plan',run_id=run_id,case_id='case',
                method='GET',url='https://qa.example/ok',request_data={'query':{}},response_status=200,
                response_body='{"ok":true}',passed=True,duration_ms=5))
            session.commit()
        with Session(engine) as session:
            saved = session.query(Execution).filter_by(run_id=run_id).one()
            assert saved.request_data == {'query':{}} and saved.passed and saved.response_status == 200
            session.add(Execution(workspace_id='w',plan_id='plan',run_id=run_id,case_id='foreign-case',
                method='GET',url='https://qa.example/ok',request_data={},passed=False,duration_ms=0))
            with pytest.raises(IntegrityError):
                session.commit()
        for status,passed in [('invalid',False),('passed',False),('execution_error',True)]:
            with Session(engine) as session:
                invalid_run=Run(workspace_id='w',project_id='p',plan_id='plan',total_cases=1,created_by='u')
                session.add(invalid_run)
                session.flush()
                session.add(Execution(workspace_id='w',plan_id='plan',run_id=invalid_run.id,case_id='case',
                    method='GET',url='https://qa.example/ok',request_data={},passed=passed,status=status,duration_ms=0))
                # MySQL reports CHECK violations as error 3819 (OperationalError).
                with pytest.raises((IntegrityError, OperationalError)) as rejected:
                    session.commit()
                assert 'check' in str(rejected.value.orig).lower()
    finally:
        engine.dispose()


@pytest.mark.parametrize('table', [Run.__table__,Execution.__table__])
def test_execution_mysql_ddl_compiles_with_scoped_foreign_keys(table):
    sql = str(CreateTable(table).compile(dialect=mysql.dialect()))
    assert 'workspace_id' in sql and 'FOREIGN KEY' in sql
    if table.name == 'api_test_executions':
        assert 'LONGTEXT' in sql and 'UNIQUE (run_id, case_id)' in sql
