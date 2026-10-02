"""Workspace-scoped API reports, exports and explicitly requested optional analysis."""

from datetime import timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .api_reports import clean_evidence, export_sections, materialize, render_export, report_info
from .product_models import MemberRow, SessionRow, TestExecution, UserRow
from .providers import ProviderError
from .result_analyzer import ANALYSIS_ERROR, AnalyzeInput, ResultAnalyzerAgent, analysis_context
from .store_base import now
from .test_execution_api import owned_run
from .test_plan_api import utc_time

router = APIRouter()


def evidence_row(session, run, execution_id):
    row = session.scalar(
        select(TestExecution).where(
            TestExecution.id == execution_id,
            TestExecution.run_id == run.id,
            TestExecution.workspace_id == run.workspace_id,
        )
    )
    if row is None:
        raise HTTPException(404, "执行证据不存在")
    return row


def revalidate(session, request, run_id):
    identity = request.state.current_user
    saved = session.get(SessionRow, identity.session_hash)
    user = session.get(UserRow, identity.user_id)
    if (
        not saved
        or not saved.refresh_token_hash
        or saved.user_id != identity.user_id
        or not user
        or not user.is_active
        or saved.expires_at.replace(tzinfo=timezone.utc) <= now()
        or identity.access_expires_at <= now().timestamp()
    ):
        raise HTTPException(401, "登录已过期，请重新登录")
    if session.get(MemberRow, (identity.workspace_id, identity.user_id)) is None:
        raise HTTPException(404, "工作区不存在")
    return owned_run(session, request, run_id)


@router.get("/api/test-runs/{run_id}/report")
def report(run_id: str, request: Request):
    with Session(request.app.state.store.engine) as session:
        return report_info(materialize(session, owned_run(session, request, run_id)))


@router.get("/api/test-runs/{run_id}/findings")
def findings(run_id: str, request: Request):
    with Session(request.app.state.store.engine) as session:
        report = materialize(session, owned_run(session, request, run_id))
        return [
            {**item, "ai_analysis": (report.analysis or {}).get(item["source_execution_id"])}
            for item in report.summary["findings"]
        ]


@router.get("/api/test-runs/{run_id}/executions/{execution_id}")
def execution_evidence(run_id: str, execution_id: str, request: Request):
    with Session(request.app.state.store.engine) as session:
        return clean_evidence(evidence_row(session, owned_run(session, request, run_id), execution_id))


@router.post("/api/test-runs/{run_id}/analyze")
async def analyze(run_id: str, request: Request, data: AnalyzeInput):
    engine, gateway = request.app.state.store.engine, request.app.state.gateway
    with Session(engine) as session:
        run = owned_run(session, request, run_id)
        report = materialize(session, run)
        selected = set(data.execution_ids)
        if len(selected) != len(data.execution_ids):
            raise HTTPException(422, "请勿重复选择分析项")
        eligible = {f["source_execution_id"]: f for f in report.summary["findings"]}
        if not selected <= eligible.keys():
            raise HTTPException(422, "只能分析该报告已保存的失败或执行错误")
        cached = report.analysis or {}
        missing = [key for key in data.execution_ids if data.refresh or key not in cached]
        if not missing:
            return report_info(report)
        if run_id in request.app.state.report_analysis_in_flight:
            raise HTTPException(409, "该报告正在进行 AI 分析")
        try:
            config = gateway.config(request.state.workspace_id, request.state.session_hash)
        except ProviderError:
            raise HTTPException(422, "尚未配置模型服务，请先在模型中心配置并启用模型") from None
        cases = {c["execution_id"]: c for c in report.summary["cases"] if c["execution_id"]}
        items = [
            analysis_context(cases[key], eligible[key], evidence_row(session, run, key)) for key in missing
        ]
    request.app.state.report_analysis_in_flight.add(run_id)
    try:
        result = await ResultAnalyzerAgent(gateway).analyze(
            request.state.workspace_id, request.state.session_hash, items, config
        )
        with Session(engine) as session:
            run = revalidate(session, request, run_id)
            report = materialize(session, run)
            overlay = dict(report.analysis or {})
            timestamp = utc_time(now())
            for item in result["analyses"]:
                overlay[item["execution_id"]] = {
                    **item,
                    "root_cause_confirmed": False,
                    "label": "AI Analysis / AI Suggestion",
                    "root_cause_label": "Root Cause Not Confirmed",
                    "generated_at": timestamp,
                    "provider": config["provider"],
                    "model": config["model"],
                }
            report.analysis = overlay  # Full replacement, never in-place mutation of SQLAlchemy JSON.
            session.commit()
            return report_info(report)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, ANALYSIS_ERROR) from None
    finally:
        request.app.state.report_analysis_in_flight.discard(run_id)


def export_response(run_id, request, format):
    # Hold a read session only for streaming database evidence; no model/target network operations.
    session = Session(request.app.state.store.engine)
    try:
        run = owned_run(session, request, run_id)
        report = materialize(session, run)
        try:
            safe_id = str(UUID(run.id))
        except ValueError:
            # Historical IDs need not be UUIDs; the generated report ID is always safe.
            safe_id = str(UUID(report.id))
        # Freeze overlay before streaming, so one export cannot mix analysis updates.
        session.refresh(report)

        def chunks():
            try:
                yield from render_export(export_sections(session, run, report), format)
            finally:
                session.close()

        return StreamingResponse(
            chunks(),
            media_type="text/html" if format == "html" else "text/markdown",
            headers={
                "Content-Disposition": f'attachment; filename="autoqa-run-{safe_id}.{format if format == "html" else "md"}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            },
        )
    except BaseException:
        session.close()
        raise


@router.get("/api/test-runs/{run_id}/report.html")
def export_html(run_id: str, request: Request):
    return export_response(run_id, request, "html")


@router.get("/api/test-runs/{run_id}/report.md")
def export_markdown(run_id: str, request: Request):
    return export_response(run_id, request, "md")
