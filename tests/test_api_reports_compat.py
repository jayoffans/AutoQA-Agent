"""Real populated revision-9 upgrade, report snapshots and constraints on SQLite/MySQL."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from autoqa.api_reports import materialize
from autoqa.app import create_app
from autoqa.crypto import hash_password
from autoqa.migrations import upgrade
from autoqa.product_models import (
    AnalysisAudit,
    ApiEndpointRow,
    ApiSpecRow,
    InstallationRow,
    MemberRow,
    UserRow,
    WorkspaceRow,
)
from autoqa.product_models import TestCase as Case
from autoqa.product_models import TestExecution as Execution
from autoqa.product_models import TestPlan as Plan
from autoqa.product_models import TestRun as Run
from autoqa.store import TaskRow
from autoqa.store_base import now
from autoqa.studio_models import FindingRow, ProjectRow, ReportRow, RunRow
from tests.test_api_reports import analysis_output
from tests.test_production_compatibility import cli_migrate
from tests.test_production_compatibility import compat_settings as compat_settings  # noqa: F401


def legacy_revision9(engine):
    run_id = str(uuid4())
    with Session(engine) as s:
        s.add(InstallationRow(id=1,mode="production"))
        s.add(
            UserRow(
                id="u",
                email="report-compat@example.com",
                password_hash=hash_password("compat-fixture-password"),
            )
        )
        s.flush()
        s.add(WorkspaceRow(id="w", name="兼容工作区", owner_id="u"))
        s.flush()
        s.add(MemberRow(workspace_id="w", user_id="u", role="owner"))
        s.add(
            ProjectRow(
                id="p",
                workspace_id="w",
                name="旧 API 项目",
                description="",
                url="https://qa.example",
                project_type="api",
            )
        )
        s.flush()
        s.add(
            ApiSpecRow(
                id="spec",
                workspace_id="w",
                project_id="p",
                name="spec",
                version="1",
                format="json",
                raw_content="{}",
            )
        )
        s.flush()
        s.add(
            ApiEndpointRow(
                id="endpoint",
                workspace_id="w",
                project_id="p",
                spec_id="spec",
                method="GET",
                path="/health",
                summary="",
                parameters=[],
                request_schema={},
                response_schema={},
            )
        )
        s.add(
            Plan(
                id="plan",
                workspace_id="w",
                project_id="p",
                api_spec_id="spec",
                status="approved",
                provider="compatible",
                model="fixture",
                created_by="u",
            )
        )
        s.flush()
        s.add(
            Case(
                id="case",
                workspace_id="w",
                plan_id="plan",
                endpoint_id="endpoint",
                name="历史失败",
                category="positive",
                priority="P0",
                description="",
                steps=[],
                expected_result="AI 描述不作为执行事实",
            )
        )
        s.add(
            Run(
                id=run_id,
                workspace_id="w",
                project_id="p",
                plan_id="plan",
                status="failed",
                total_cases=1,
                failed_cases=1,
                started_at=now(),
                finished_at=now(),
                created_by="u",
            )
        )
        s.flush()
        s.add(
            Execution(
                id="execution",
                workspace_id="w",
                plan_id="plan",
                run_id=run_id,
                case_id="case",
                method="GET",
                url="https://qa.example/health",
                request_data={},
                response_status=400,
                response_body='{"ok":false}',
                passed=False,
                status="failed",
                duration_ms=2,
                result_data={
                    "version": 1,
                    "case_name": "历史失败",
                    "expected_status": 200,
                    "assertion_results": [
                        {
                            "type": "status",
                            "expected": 200,
                            "actual": 400,
                            "status": "failed",
                            "reason_code": "status_mismatch",
                        }
                    ],
                },
            )
        )
        s.add(TaskRow(id="legacy-task", status="passed", request={"goal": "legacy"}, planner_mode="demo"))
        s.flush()
        s.add(RunRow(id="legacy-run", task_id="legacy-task", status="passed", passed=1, failed=0))
        s.flush()
        s.add(
            FindingRow(
                id="legacy-finding",
                run_id="legacy-run",
                severity="High",
                title="old",
                description="old",
                suggestion="old",
                evidence={},
            )
        )
        s.execute(
            text(
                "INSERT INTO reports(id,run_id,summary,html_content,created_at) VALUES "
                "('legacy-report','legacy-run',:summary,'<p>legacy unchanged</p>',CURRENT_TIMESTAMP)"
            ),
            {"summary": json.dumps({"passed": 1})},
        )
        s.commit()
    return run_id


def test_populated_upgrade_report_concurrency_restart_and_legacy_preservation(compat_settings):
    cli_migrate(compat_settings, "0009_execution_assertions")
    engine = create_engine(compat_settings.database_url, hide_parameters=True)
    try:
        run_id = legacy_revision9(engine)
        with engine.connect() as c:
            before = {
                table: [dict(r) for r in c.execute(text("SELECT * FROM " + table)).mappings()]
                for table in ("reports", "findings", "api_test_runs", "api_test_executions")
            }
        upgrade(engine)
        upgrade(engine)
        with engine.connect() as c:
            assert c.scalar(text("SELECT version_num FROM alembic_version")) == "0011_alpha_hardening"
            for table, rows in before.items():
                after = [dict(r) for r in c.execute(text("SELECT * FROM " + table)).mappings()]
                if table == "reports":
                    for row in after:
                        assert row.pop("api_run_id") is None and row.pop("analysis") is None
                assert after == rows
        assert not inspect(engine).get_columns("reports")[0]["nullable"]
        with TestClient(create_app(compat_settings), base_url="https://testserver") as client:
            login = client.post(
                "/api/auth/login",
                json={"email": "report-compat@example.com", "password": "compat-fixture-password"},
            )
            assert login.status_code == 200, login.text
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            path = "/api/test-runs/" + run_id
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: client.get(path + "/report"), range(4)))
            assert all(r.status_code == 200 for r in results), [r.status_code for r in results]
            report = results[0].json()
            assert all(r.json() == report for r in results)
            assert report["summary"]["failed_cases"] == 1 and report["summary"]["pass_rate"] == 0
            assert client.get(path + "/report.md").status_code == 200
            assert client.get(path + "/report.html").status_code == 200
            with Session(engine) as s:
                api = s.scalar(select(ReportRow).where(ReportRow.api_run_id == run_id))
                api.analysis = {"execution": {"analysis_summary": "兼容 JSON", "root_cause_confirmed": False}}
                s.commit()
                old = s.get(ReportRow, "legacy-report")
                assert (
                    old.summary == {"passed": 1}
                    and old.html_content == "<p>legacy unchanged</p>"
                    and old.run_id == "legacy-run"
                )
            client.cookies.clear()
        with TestClient(create_app(compat_settings), base_url="https://testserver") as restarted:
            assert (
                restarted.post(
                    "/api/auth/login",
                    json={"email": "report-compat@example.com", "password": "compat-fixture-password"},
                ).status_code
                == 200
            )
            result = restarted.get(path + "/report").json()
            assert (
                result["id"] == report["id"]
                and result["analysis"]["execution"]["analysis_summary"] == "兼容 JSON"
            )
        for legacy, api in [(None, None), ("legacy-run", run_id), (None, "foreign-run"), (None, run_id)]:
            with Session(engine) as s:
                s.add(ReportRow(id=str(uuid4()), run_id=legacy, api_run_id=api, summary={}, html_content=""))
                with pytest.raises((IntegrityError, OperationalError)):
                    s.commit()
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            # With no audit history, 0011 can be removed; 0010 still protects existing reports.
            command.downgrade(config, "0010_api_reports")
            with pytest.raises(RuntimeError, match="pre-upgrade backup"):
                command.downgrade(config, "0009_execution_assertions")
        with engine.connect() as c:
            assert c.scalar(text("SELECT version_num FROM alembic_version")) == "0010_api_reports"
        upgrade(engine)
        with engine.connect() as c:
            assert c.scalar(text("SELECT version_num FROM alembic_version")) == "0011_alpha_hardening"
    finally:
        engine.dispose()


def test_empty_upgrade_downgrade_reupgrade(compat_settings):
    cli_migrate(compat_settings)
    engine = create_engine(compat_settings.database_url, hide_parameters=True)
    try:
        from alembic import command
        from alembic.config import Config

        config = Config("alembic.ini")
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0009_execution_assertions")
        assert "api_run_id" not in {c["name"] for c in inspect(engine).get_columns("reports")}
        upgrade(engine)
        assert {"api_run_id", "analysis"} <= {c["name"] for c in inspect(engine).get_columns("reports")}
    finally:
        engine.dispose()


def test_revision10_to11_preserves_facts_and_database_analysis_exclusivity(compat_settings):
    cli_migrate(compat_settings, "0009_execution_assertions")
    engine = create_engine(compat_settings.database_url, hide_parameters=True)
    try:
        run_id = legacy_revision9(engine)
        cli_migrate(compat_settings, "0010_api_reports")
        with engine.begin() as connection:
            before = {table: [dict(row) for row in connection.execute(text("SELECT * FROM " + table)).mappings()]
                      for table in ("reports", "api_test_runs", "api_test_executions")}
        cli_migrate(compat_settings)
        upgrade(engine)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0011_alpha_hardening"
            for table, rows in before.items():
                assert [dict(row) for row in connection.execute(text("SELECT * FROM " + table)).mappings()] == rows
            assert connection.scalar(text("SELECT COUNT(*) FROM api_analysis_audits")) == 0
        app1, app2 = create_app(compat_settings), create_app(compat_settings)
        with TestClient(app1, base_url="https://testserver") as first, TestClient(app2, base_url="https://testserver") as second:
            for client in (first, second):
                result = client.post("/api/auth/login", json={"email":"report-compat@example.com", "password":"compat-fixture-password"})
                assert result.status_code == 200
                client.headers['X-CSRF-Token'] = result.json()['csrf_token']
            created = first.post('/api/settings/providers', json={'provider':'compatible','model':'audit-fixture',
                'base_url':'http://127.0.0.1','api_key':'audit-key-canary-123456'})
            assert created.status_code == 201, created.text
            path = '/api/test-runs/' + run_id
            first_intent = first.post(path + '/analysis-intents', json={'execution_ids':['execution']}).json()
            second_intent = second.post(path + '/analysis-intents', json={'execution_ids':['execution']}).json()
            entered, release = Event(), Event()
            calls = []
            def model(request):
                with Session(engine) as session:
                    saved = session.scalar(select(AnalysisAudit).where(AnalysisAudit.active_run_id == run_id))
                    assert saved.status == 'invoking' and saved.confirmed_at and saved.dispatch_at
                calls.append(request.url.path)
                entered.set()
                assert release.wait(10)
                context = json.loads(json.loads(request.content)['messages'][1]['content'])
                return httpx.Response(200,json={'model':'audit-resolved','choices':[{'message':{'content':json.dumps(analysis_output(context['executions']))}}]})
            for app in (app1,app2):
                app.state.gateway.transport_factory = lambda: httpx.MockTransport(model)
            with ThreadPoolExecutor(max_workers=2) as pool:
                pending = pool.submit(first.post,path+'/analyze',json={'intent_id':first_intent['intent_id']})
                try:
                    assert entered.wait(10)
                    rejected = second.post(path+'/analyze',json={'intent_id':second_intent['intent_id']})
                    assert rejected.status_code == 409, rejected.text
                finally:
                    release.set()
                completed = pending.result()
                assert completed.status_code == 200,completed.text
            assert calls == ['/chat/completions']
            assert first.post(path+'/analyze',json={'intent_id':first_intent['intent_id']}).status_code == 200
            persisted = first.get(path+'/analysis-intents/'+first_intent['intent_id']).json()
            assert persisted['status']=='succeeded' and persisted['request_count']==1
            assert persisted['resolved_model'] == 'audit-resolved'
            cached = first.post(path+'/analysis-intents',json={'execution_ids':['execution']}).json()
            assert cached['to_analyze_execution_ids'] == []
            assert first.post(path+'/analyze',json={'intent_id':cached['intent_id']}).status_code == 200
            cached = first.get(path+'/analysis-intents/'+cached['intent_id']).json()
            assert cached['status']=='cached' and cached['request_count']==0 and calls == ['/chat/completions']
            refresh = second.post(path+'/analysis-intents',json={'execution_ids':['execution'],'refresh':True}).json()
            completed = second.post(path+'/analyze',json={'intent_id':refresh['intent_id']})
            assert completed.status_code == 200 and calls == ['/chat/completions'] * 2
            assert second.get(path+'/analysis-intents/'+refresh['intent_id']).json()['request_count'] == 1
            # Multiple NULL leases are valid, and run/workspace mismatches cannot be inserted.
            with Session(engine) as session:
                row = session.get(AnalysisAudit,second_intent['intent_id'])
                row.status, row.active_run_id, row.request_count = 'invoking',run_id,None
                row.expires_at = now()-timedelta(seconds=1)
                session.commit()
            recovered = second.get(path+'/analysis-intents/'+second_intent['intent_id']).json()
            assert recovered['status']=='unknown' and recovered['request_count'] is None
            assert calls == ['/chat/completions'] * 2
            with Session(engine) as session:
                session.add(WorkspaceRow(id='other-workspace',name='其他已存在工作区',owner_id='u'))
                session.commit()
                saved = session.get(AnalysisAudit,first_intent['intent_id'])
                saved.workspace_id = 'other-workspace'
                with pytest.raises(IntegrityError):
                    session.commit()
            with Session(engine) as session:
                saved = session.get(AnalysisAudit,cached['intent_id'])
                saved.request_count = None
                with pytest.raises((IntegrityError, OperationalError)) as rejected:
                    session.commit()
                assert 'ck_analysis_audit_cached' in str(rejected.value)
        with TestClient(create_app(compat_settings),base_url='https://testserver') as restarted:
            result=restarted.post('/api/auth/login',json={'email':'report-compat@example.com','password':'compat-fixture-password'})
            assert result.status_code==200
            assert restarted.get(path+'/analysis-intents/'+first_intent['intent_id']).json()==persisted
            assert restarted.get(path+'/report').json()==completed.json()
        import sys

        from tests.production_support import checked_process, clean_env
        with pytest.raises(RuntimeError):
            checked_process([sys.executable,'-m','alembic','downgrade','0010_api_reports'],env=clean_env(
                DATABASE_URL=compat_settings.database_url,AUTOQA_MODE='production',AUTOQA_ENV='production',AUTOQA_SECRET_KEY=compat_settings.secret_key))
    finally:
        engine.dispose()


def test_revision11_preserves_preexisting_nonempty_analysis_and_evidence(compat_settings):
    cli_migrate(compat_settings, '0009_execution_assertions')
    engine = create_engine(compat_settings.database_url, hide_parameters=True)
    try:
        run_id = legacy_revision9(engine)
        cli_migrate(compat_settings, '0010_api_reports')
        with Session(engine) as session:
            report = materialize(session, session.get(Run, run_id))
            report.analysis = {'execution': {'analysis_summary': 'Existing unconfirmed hypothesis',
                                             'root_cause_confirmed': False, 'confidence': 0.5}}
            session.commit()
        tables = ('reports', 'findings', 'api_test_runs', 'api_test_executions', 'ai_test_plans', 'ai_test_cases')
        with engine.connect() as connection:
            before = {table: [dict(row) for row in connection.execute(text('SELECT * FROM ' + table)).mappings()]
                      for table in tables}
        upgrade(engine)
        upgrade(engine)
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0011_alpha_hardening'
            for table, rows in before.items():
                assert [dict(row) for row in connection.execute(text('SELECT * FROM ' + table)).mappings()] == rows
            assert connection.scalar(text('SELECT COUNT(*) FROM api_analysis_audits')) == 0
    finally:
        engine.dispose()
