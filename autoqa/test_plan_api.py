"""Workspace-isolated TestPlan API, independent of task execution and reporting."""
import asyncio
import json
from datetime import timezone

import yaml
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .api_import import project_session, scope
from .product_models import ApiEndpointRow, ApiSpecRow, TestCase, TestPlan
from .providers import ProviderError
from .store_base import now
from .studio_models import ProjectRow
from .test_planner import (
    CATEGORIES,
    MAX_PLANNER_ENDPOINTS,
    PLANNER_ERROR,
    GeneratePlanInput,
    TestPlannerAgent,
)

router = APIRouter()


def utc_time(value):
    value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def plan_info(session, row):
    categories = dict(session.execute(select(TestCase.category, func.count()).where(
        TestCase.plan_id == row.id, TestCase.workspace_id == row.workspace_id).group_by(TestCase.category)).all())
    return {key: getattr(row, key) for key in ("id", "workspace_id", "project_id", "api_spec_id", "status",
            "provider", "model", "created_by", "created_at", "updated_at")} | {
        "created_at": utc_time(row.created_at), "updated_at": utc_time(row.updated_at),
        "case_count": sum(categories.values()), "category_counts": {key: categories.get(key, 0) for key in sorted(CATEGORIES)}}


def owned_plan(session, request, plan_id):
    row = session.scalar(select(TestPlan).join(ProjectRow, ProjectRow.id == TestPlan.project_id).where(
        TestPlan.id == plan_id, TestPlan.workspace_id == request.state.workspace_id,
        ProjectRow.workspace_id == request.state.workspace_id, ProjectRow.archived.is_(False)))
    if row is None:
        raise HTTPException(404, "测试计划不存在")
    return row


def fail_plan(engine, plan_id):
    with Session(engine) as session:
        session.execute(update(TestPlan).where(TestPlan.id == plan_id, TestPlan.status == "generating")
                        .values(status="failed", updated_at=now()))
        session.commit()


@router.post("/api/projects/{project_id}/test-plans/generate", status_code=201)
async def generate_plan(project_id: str, request: Request, data: GeneratePlanInput | None = None):
    gateway = request.app.state.gateway
    # Ownership is checked before model lookup, so foreign projects reveal no configuration.
    with project_session(request, project_id) as session:
        try:
            config = gateway.config(request.state.workspace_id, request.state.session_hash)
        except ProviderError:
            raise HTTPException(422, "尚未配置模型服务，请先在模型中心配置并启用模型") from None
        spec_query = select(ApiSpecRow).where(*scope(ApiSpecRow, request, project_id))
        if data and data.api_spec_id:
            spec_query = spec_query.where(ApiSpecRow.id == data.api_spec_id)
        spec = session.scalar(spec_query.order_by(ApiSpecRow.created_at.desc(), ApiSpecRow.id).limit(1))
        if spec is None:
            raise HTTPException(422, "请先上传该项目的 OpenAPI 接口文档")
        rows = list(session.scalars(select(ApiEndpointRow).where(ApiEndpointRow.spec_id == spec.id,
            *scope(ApiEndpointRow, request, project_id)).order_by(ApiEndpointRow.path, ApiEndpointRow.method)))
        if not rows:
            raise HTTPException(422, "接口文档中没有可规划的接口")
        if len(rows) > MAX_PLANNER_ENDPOINTS:
            raise HTTPException(422, f"本阶段每份计划最多支持 {MAX_PLANNER_ENDPOINTS} 个接口，请拆分文档后重试")
        document = json.loads(spec.raw_content) if spec.format == "json" else yaml.safe_load(spec.raw_content)
        definitions = document.get("components", {}) if "openapi" in document else {
            "definitions": document.get("definitions", {}), "securityDefinitions": document.get("securityDefinitions", {})}
        endpoints = [{"endpoint_id": row.id, **{key: getattr(row, key) for key in (
            "method", "path", "summary", "parameters", "request_schema", "response_schema")},
            "security": document.get("paths", {}).get(row.path, {}).get(row.method.lower(), {}).get(
                "security", document.get("security", []))} for row in rows]
        plan = TestPlan(workspace_id=request.state.workspace_id, project_id=project_id, api_spec_id=spec.id,
                        status="generating", provider=config["provider"], model=config["model"],
                        created_by=request.state.user_id)
        session.add(plan)
        session.commit()
        plan_id = plan.id
    try:
        cases = await TestPlannerAgent(gateway).generate(request.state.workspace_id, request.state.session_hash,
            endpoints, definitions, document.get("security", []), config)
        # All batches must pass validation before any cases are saved; one atomic transaction.
        with project_session(request, project_id) as session:
            plan = owned_plan(session, request, plan_id)
            if plan.status != "generating":
                raise ValueError
            session.add_all(TestCase(workspace_id=plan.workspace_id, plan_id=plan.id,
                                     **case.model_dump()) for case in cases)
            plan.status, plan.updated_at = "ready", now()
            session.flush()
            result = plan_info(session, plan)
            session.commit()
            return result
    except asyncio.CancelledError:
        fail_plan(request.app.state.store.engine, plan_id)
        raise
    except Exception:
        fail_plan(request.app.state.store.engine, plan_id)
        raise HTTPException(502, {"message": PLANNER_ERROR, "plan_id": plan_id}) from None


@router.get("/api/projects/{project_id}/test-plans")
def list_plans(project_id: str, request: Request):
    with project_session(request, project_id) as session:
        return [plan_info(session, row) for row in session.scalars(select(TestPlan).where(
            *scope(TestPlan, request, project_id)).order_by(TestPlan.created_at.desc(), TestPlan.id))]


@router.get("/api/test-plans/{plan_id}")
def get_plan(plan_id: str, request: Request):
    with Session(request.app.state.store.engine) as session:
        return plan_info(session, owned_plan(session, request, plan_id))


@router.get("/api/test-plans/{plan_id}/cases")
def get_cases(plan_id: str, request: Request):
    with Session(request.app.state.store.engine) as session:
        plan = owned_plan(session, request, plan_id)
        return [{key: getattr(row, key) for key in ("id", "workspace_id", "plan_id", "endpoint_id", "name", "category",
            "priority", "description", "steps", "expected_result", "created_at")} | {
                "created_at": utc_time(row.created_at)} for row in session.scalars(
                select(TestCase).where(TestCase.plan_id == plan.id, TestCase.workspace_id == plan.workspace_id)
                .order_by(TestCase.created_at, TestCase.endpoint_id, TestCase.category, TestCase.id))]
