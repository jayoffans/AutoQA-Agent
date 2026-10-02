"""Human review, bounded in-process API runs and workspace-isolated persisted evidence."""
import asyncio
import json

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .api_executor import (
    MAX_RUN_CASES,
    CaseConfig,
    default_config,
    validate_base_url,
    validate_config,
)
from .execution_privacy import config_secrets, public_config
from .product_models import ApiEndpointRow, ApiSpecRow, TestCase, TestExecution, TestRun
from .product_models import TestPlan as Plan
from .store_base import now
from .studio_models import ProjectRow
from .target_network import TargetResolutionError
from .test_plan_api import owned_plan, plan_info, utc_time

router = APIRouter()
RUN_TIMEOUT = 120


class ReviewedCase(CaseConfig):
    case_id: str = Field(min_length=1, max_length=36)


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    cases: list[ReviewedCase] = Field(min_length=1, max_length=MAX_RUN_CASES)


def case_scope(case):
    return f"api-case:{case.workspace_id}:{case.plan_id}:{case.id}"


def plan_cases(session, plan):
    return list(session.scalars(select(TestCase).where(TestCase.plan_id == plan.id,
        TestCase.workspace_id == plan.workspace_id).order_by(TestCase.created_at, TestCase.id)))


def endpoint_for(session, plan, case):
    endpoint = session.scalar(select(ApiEndpointRow).where(ApiEndpointRow.id == case.endpoint_id,
        ApiEndpointRow.workspace_id == plan.workspace_id, ApiEndpointRow.project_id == plan.project_id,
        ApiEndpointRow.spec_id == plan.api_spec_id))
    if endpoint is None:
        raise ValueError("用例接口不属于计划文档")
    return endpoint


def document_for(session, plan):
    spec = session.get(ApiSpecRow, plan.api_spec_id)
    return json.loads(spec.raw_content) if spec.format == "json" else yaml.safe_load(spec.raw_content)


def saved_config(request, case):
    return CaseConfig.model_validate(request.app.state.gateway.box.decrypt(
        case.execution_config["encrypted"], case_scope(case)))


@router.get("/api/test-plans/{plan_id}/execution-preview")
def preview(plan_id: str, request: Request):
    with Session(request.app.state.store.engine) as session:
        plan = owned_plan(session, request, plan_id)
        base_url = plan.approved_base_url or session.get(ProjectRow, plan.project_id).url
        document = document_for(session, plan)
        cases = []
        for case in plan_cases(session, plan):
            try:
                config = saved_config(request, case) if case.execution_config else default_config(
                    endpoint_for(session, plan, case), document)
                value, error = public_config(config), None
            except (ValueError, KeyError, TypeError):
                value, error = {"request_data": {"path_params": {}, "query": {}, "headers": {}, "json_body": None},
                                "expected_status": None, "assertions": []}, "无法生成默认参数，请人工填写请求及预期状态码"
            cases.append({"case_id": case.id, "config": value, "error": error,
                          "requires_review": case.category != "positive"})
        return {"base_url": base_url, "approved_by": plan.approved_by,
                "approved_at": utc_time(plan.approved_at) if plan.approved_at else None, "cases": cases}


@router.post("/api/test-plans/{plan_id}/approve")
async def approve(plan_id: str, request: Request, data: ReviewInput | None = None):
    with Session(request.app.state.store.engine) as session:
        plan = owned_plan(session, request, plan_id)
        if plan.status != "ready":
            raise HTTPException(409, "仅已就绪计划可以审核")
        project = session.get(ProjectRow, plan.project_id)
        if project.project_type != "api":
            raise HTTPException(422, "仅支持 API 项目审核")
        cases = plan_cases(session, plan)
        if not cases or len(cases) > MAX_RUN_CASES:
            raise HTTPException(422, "测试用例为空或超出执行数量限制")
        try:
            async with asyncio.timeout(10):
                await asyncio.to_thread(validate_base_url, project.url)
            if data is None:
                if any(case.category != "positive" for case in cases):
                    raise ValueError("请人工确认每个场景的请求参数和预期 HTTP 状态码")
                document = document_for(session, plan)
                configs = {case.id: default_config(endpoint_for(session, plan, case), document) for case in cases}
            else:
                if len({case.case_id for case in data.cases}) != len(data.cases) or {
                        case.case_id for case in data.cases} != {case.id for case in cases}:
                    raise ValueError("审核必须包含全部用例且不得重复")
                configs = {item.case_id: CaseConfig.model_validate(item.model_dump(exclude={"case_id"})) for item in data.cases}
            for case in cases:
                validate_config(endpoint_for(session, plan, case), configs[case.id], project.url)
        except (ValueError, KeyError, TypeError, TimeoutError):
            raise HTTPException(422, "请检查项目 Base URL、全部用例的请求参数及预期 HTTP 状态码；仅允许公网目标") from None
        changed = session.execute(update(Plan).where(Plan.id == plan.id, Plan.workspace_id == plan.workspace_id,
            Plan.status == "ready").values(status="approved", approved_base_url=project.url,
                approved_by=request.state.user_id, approved_at=now(), updated_at=now()))
        if changed.rowcount != 1:
            raise HTTPException(409, "计划已审核，请刷新页面")
        for case in cases:
            case.execution_config = {"encrypted": request.app.state.gateway.box.encrypt(
                configs[case.id].model_dump(), case_scope(case))}
        session.commit()
        session.refresh(plan)
        return plan_info(session, plan)


def execution_status(row):
    return row.status or ("passed" if row.passed else "execution_error" if row.response_status is None else "failed")


def run_executions(session, row, *, detail=False):
    columns = (TestExecution,) if detail else (
        TestExecution.status, TestExecution.passed, TestExecution.response_status)
    query = select(*columns).where(TestExecution.run_id == row.id,
        TestExecution.workspace_id == row.workspace_id).order_by(TestExecution.id)
    return list(session.scalars(query) if detail else session.execute(query))


def run_summary(row, executions):
    counts = {key: 0 for key in ("passed", "failed", "execution_error")}
    for item in executions:
        counts[execution_status(item)] += 1
    pending = max(0, row.total_cases - len(executions))
    lifecycle = row.status if row.status in {"running", "interrupted"} else "completed"
    outcome = None if lifecycle != "completed" or pending else (
        "execution_error" if counts["execution_error"] else "failed" if counts["failed"] else "passed")
    return {"status": lifecycle if lifecycle != "completed" else outcome, "lifecycle": lifecycle, "outcome": outcome,
            "total_cases": row.total_cases, "passed_cases": counts["passed"], "failed_cases": counts["failed"],
            "execution_error_cases": counts["execution_error"], "pending_cases": pending,
            "classification_inferred": any(item.status is None for item in executions)}


def run_info(row, executions):
    return {key: getattr(row, key) for key in ("id", "workspace_id", "project_id", "plan_id", "status",
            "total_cases", "passed_cases", "failed_cases", "created_by")} | {
        "started_at": utc_time(row.started_at), "finished_at": utc_time(row.finished_at) if row.finished_at else None,
        "summary": run_summary(row, executions)}


def finish_run(engine, run_id, interrupted=False):
    with Session(engine) as session:
        row = session.get(TestRun, run_id)
        executions = run_executions(session, row)
        row.status = "interrupted" if interrupted else (
            "execution_error" if any(execution_status(item) == "execution_error" for item in executions)
            else "failed" if row.failed_cases else "passed")
        row.active_plan_id, row.finished_at = None, now()
        session.commit()
        return run_info(row, executions)


@router.post("/api/test-plans/{plan_id}/execute", status_code=201)
async def execute(plan_id: str, request: Request):
    return await execute_plan(plan_id, request)


async def execute_plan(plan_id: str, request: Request, source_run_id=None):
    engine = request.app.state.store.engine
    with Session(engine, expire_on_commit=False) as session:
        plan = owned_plan(session, request, plan_id)
        if source_run_id:
            source = owned_run(session, request, source_run_id)
            if source.status == "running":
                raise HTTPException(409, "运行中的测试不能重跑")
        if plan.status != "approved":
            raise HTTPException(409, "请先审核测试计划")
        project = session.get(ProjectRow, plan.project_id)
        if project.url != plan.approved_base_url or project.project_type != "api":
            raise HTTPException(409, "项目目标已变更，请生成并审核新的测试计划")
        cases = plan_cases(session, plan)
        if not cases or len(cases) > MAX_RUN_CASES:
            raise HTTPException(422, "测试用例为空或超出执行数量限制")
        try:
            jobs = [(case, endpoint_for(session, plan, case), saved_config(request, case)) for case in cases]
            for case, endpoint, config in jobs:
                validate_config(endpoint, config, plan.approved_base_url)
            try:
                async with asyncio.timeout(10):
                    await asyncio.to_thread(validate_base_url, plan.approved_base_url)
            except (TargetResolutionError, TimeoutError):
                pass  # An approved target's availability failure is recorded per case, not as a business failure.
        except (ValueError, KeyError, TypeError, TimeoutError):
            raise HTTPException(422, "执行配置不可用或目标被安全策略拒绝") from None
        run = TestRun(workspace_id=plan.workspace_id, project_id=plan.project_id, plan_id=plan.id,
                      active_plan_id=plan.id, total_cases=len(cases), created_by=request.state.user_id)
        session.add(run)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, "该计划已有运行中的测试") from None
        run_id, base_url = run.id, plan.approved_base_url
        hidden = [value for _, _, config in jobs for value in config_secrets(config)]
    try:
        async with asyncio.timeout(RUN_TIMEOUT):
            for case, endpoint, config in jobs:
                result = await request.app.state.api_executor.execute(case, endpoint, base_url, config, run_id, hidden)
                with Session(engine) as session:
                    row = session.get(TestRun, run_id)
                    session.add(result)
                    if result.passed:
                        row.passed_cases += 1
                    elif result.status == "failed":
                        row.failed_cases += 1
                    session.commit()  # Evidence and its counter are one transaction, after each case.
        return finish_run(engine, run_id)
    except asyncio.CancelledError:
        finish_run(engine, run_id, interrupted=True)
        raise
    except Exception:
        result = finish_run(engine, run_id, interrupted=True)
        raise HTTPException(503, {"message": "执行已中断，已保存完成的结果；重试可能重复产生业务写入",
                                  "run_id": result["id"]}) from None


@router.get("/api/test-plans/{plan_id}/runs")
def list_runs(plan_id: str, request: Request):
    with Session(request.app.state.store.engine) as session:
        plan = owned_plan(session, request, plan_id)
        return [run_info(row, run_executions(session, row)) for row in session.scalars(select(TestRun).where(
            TestRun.plan_id == plan.id, TestRun.workspace_id == plan.workspace_id)
            .order_by(TestRun.started_at.desc(), TestRun.id))]


def owned_run(session, request, run_id):
    run = session.scalar(select(TestRun).where(TestRun.id == run_id,
                                              TestRun.workspace_id == request.state.workspace_id))
    if run is None:
        raise HTTPException(404, "测试运行不存在")
    owned_plan(session, request, run.plan_id)
    return run


@router.post("/api/test-runs/{run_id}/rerun", status_code=201)
async def rerun(run_id: str, request: Request):
    with Session(request.app.state.store.engine) as session:
        source = owned_run(session, request, run_id)
        plan_id = source.plan_id
    return await execute_plan(plan_id, request, source_run_id=run_id)


@router.get("/api/test-runs/{run_id}")
def get_run(run_id: str, request: Request):
    with Session(request.app.state.store.engine) as session:
        run = owned_run(session, request, run_id)
        executions = run_executions(session, run, detail=True)
        return run_info(run, executions) | {"executions": [{key: getattr(row, key) for key in ("id", "workspace_id", "run_id",
            "case_id", "method", "url", "request_data", "response_status", "response_body", "passed",
            "error_message", "duration_ms", "result_data")} | {"status": execution_status(row),
            "classification_inferred": row.status is None} for row in executions]}
