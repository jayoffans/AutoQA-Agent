"""Same planner schema/persistence contracts on SQLite and disposable MySQL in CI."""
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autoqa.migrations import upgrade
from autoqa.product_models import ApiEndpointRow, ApiSpecRow, WorkspaceRow
from autoqa.product_models import TestCase as Case
from autoqa.product_models import TestPlan as Plan
from autoqa.studio_models import ProjectRow
from tests.production_support import random_key, register
from tests.test_api_import import SPEC, create_project
from tests.test_production_compatibility import cli_migrate
from tests.test_production_compatibility import compat as compat  # noqa: F401
from tests.test_production_compatibility import compat_settings as compat_settings  # noqa: F401
from tests.test_test_planner import mock_output


def test_planner_additive_migration_preserves_spec_and_legacy_tables(compat_settings):
    cli_migrate(compat_settings, '0006_api_import')
    engine = create_engine(compat_settings.database_url, hide_parameters=True)
    workspace_id, project_id, spec_id, endpoint_id = (str(uuid4()) for _ in range(4))
    try:
        with Session(engine) as session:
            session.add(WorkspaceRow(id=workspace_id, name='existing workspace'))
            session.flush()
            session.add(ProjectRow(id=project_id, workspace_id=workspace_id, name='existing API', url='', project_type='api'))
            session.flush()
            session.add(ApiSpecRow(id=spec_id, workspace_id=workspace_id, project_id=project_id,
                name='preserved spec', version='1.0', format='json', raw_content=json.dumps(SPEC)))
            session.flush()
            session.add(ApiEndpointRow(id=endpoint_id, workspace_id=workspace_id, project_id=project_id,
                spec_id=spec_id, method='GET', path='/existing', summary='preserved endpoint',
                parameters=[], request_schema={}, response_schema={}))
            session.commit()
        with engine.connect() as connection:
            before = set(inspect(connection).get_table_names())
        upgrade(engine)
        upgrade(engine)
        inspector = inspect(engine)
        assert before <= set(inspector.get_table_names())
        with engine.connect() as connection:
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0008_api_execution'
        assert {c['name'] for c in inspector.get_columns('ai_test_plans')} == {
            'id','workspace_id','project_id','api_spec_id','status','provider','model','created_by','created_at','updated_at',
            'approved_base_url','approved_by','approved_at'}
        assert {fk['referred_table'] for fk in inspector.get_foreign_keys('ai_test_cases')} == {
            'workspaces','ai_test_plans','api_endpoints'}
        assert {'test_plans', 'test_cases'} <= set(inspector.get_table_names())
        with Session(engine) as session:
            assert json.loads(session.get(ApiSpecRow, spec_id).raw_content) == SPEC
            assert session.get(ApiEndpointRow, endpoint_id).summary == 'preserved endpoint'
            assert session.get(ProjectRow, project_id).name == 'existing API'
    finally:
        engine.dispose()


def test_planner_database_parity_and_scope_constraints(compat):
    client, services, _ = compat
    signed, _, _ = register(client)
    project = create_project(client)
    prefix = f'/api/projects/{project["id"]}'
    spec = client.post(prefix + '/api-specs/upload', files={'file': ('openapi.json', json.dumps(SPEC))}).json()['spec']
    assert client.put('/api/provider', json={'provider': 'compatible', 'base_url': services.url,
        'api_key': random_key(), 'model': 'mock-planner-http'}).status_code == 200
    def handler(request):
        context = json.loads(json.loads(request.content)['messages'][1]['content'])
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(mock_output(context))}}]})
    client.app.state.gateway.transport_factory = lambda: httpx.MockTransport(handler)
    response = client.post(prefix + '/test-plans/generate')
    assert response.status_code == 201, 'Planner persistence failed'
    plan = response.json()
    assert plan['case_count'] == 8 and plan['created_by'] == signed['user']['id']
    assert len(client.get('/api/test-plans/' + plan['id'] + '/cases').json()) == 8
    with TestClient(client.app, base_url='https://testserver') as other:
        register(other)
        foreign_workspace = other.get('/api/auth/me').json()['workspace_id']
        assert other.get('/api/test-plans/' + plan['id']).status_code == 404
        with Session(client.app.state.store.engine) as session:
            endpoint = session.scalar(select(ApiEndpointRow))
            session.add(Case(workspace_id=foreign_workspace, plan_id=plan['id'], endpoint_id=endpoint.id,
                name='forged', category='positive', priority='P1', description='invalid', steps=['invalid'], expected_result='invalid'))
            with pytest.raises(IntegrityError):
                session.commit()
        with Session(client.app.state.store.engine) as session:
            session.add(Plan(id=str(uuid4()), workspace_id=foreign_workspace, project_id=project['id'],
                api_spec_id=spec['id'], status='ready', provider='compatible', model='mock', created_by=signed['user']['id']))
            with pytest.raises(IntegrityError):
                session.commit()
    assert client.delete(prefix + '/api-specs/' + spec['id']).status_code == 409
    with Session(client.app.state.store.engine) as session:
        assert session.get(ApiSpecRow, spec['id'])
    assert services.calls == []
