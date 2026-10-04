"""Workspace-scoped API reports, exports and explicitly requested optional analysis."""

import asyncio
import hashlib
import json
from datetime import timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .api_reports import clean_evidence, export_sections, materialize, render_export, report_info
from .product_models import AnalysisAudit, MemberRow, SessionRow, TestExecution, UserRow
from .providers import ProviderError
from .result_analyzer import (
    ANALYSIS_ERROR,
    AnalyzeInput,
    ConfirmAnalysisInput,
    ResultAnalyzerAgent,
    analysis_context,
)
from .store_base import now
from .test_execution_api import owned_run
from .test_plan_api import utc_time

router = APIRouter()
NOTICE_VERSION = "analyzer-cost-v1"
INTENT_SECONDS = 300
INVOCATION_SECONDS = 90  # The Analyzer itself is bounded to 60 seconds; allow persistence grace.


def audit_info(row):
    fields = ("workspace_id", "user_id", "run_id", "report_id", "selected_execution_ids",
              "analyzed_execution_ids", "refresh", "provider", "model", "resolved_model", "notice_version",
              "status", "request_count", "error_code")
    value = {key: getattr(row, key) for key in fields}
    value["intent_id"] = row.id
    for key in ("action_at", "confirmed_at", "dispatch_at", "finished_at", "expires_at"):
        value[key] = utc_time(getattr(row, key)) if getattr(row, key) else None
    value["cached_execution_ids"] = [key for key in row.selected_execution_ids if key not in row.analyzed_execution_ids]
    value["to_analyze_execution_ids"] = list(row.analyzed_execution_ids)
    return value


def recover_stale(session, run_id):
    # Recovery only releases a lease. It never dispatches a model or invents a request count.
    session.execute(update(AnalysisAudit).execution_options(synchronize_session=False).where(AnalysisAudit.run_id == run_id,
        AnalysisAudit.status == "invoking", AnalysisAudit.expires_at <= now()).values(
            status="unknown", request_count=None, active_run_id=None, finished_at=now(), error_code="interrupted"))


def owned_intent(session, request, run, intent_id, *, confirming=False):
    row = session.scalar(select(AnalysisAudit).where(AnalysisAudit.id == intent_id,
        AnalysisAudit.run_id == run.id, AnalysisAudit.workspace_id == request.state.workspace_id,
        *([AnalysisAudit.user_id == request.state.user_id] if confirming else [])))
    if row is None:
        raise HTTPException(404, "分析授权不存在")
    return row


def eligible_selection(session, run, report, ids):
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "请勿重复选择分析项")
    eligible = {f["source_execution_id"]: f for f in report.summary["findings"]}
    if not 1 <= len(ids) <= 3 or not set(ids) <= eligible.keys():
        raise HTTPException(422, "只能分析该报告已保存的失败或执行错误")
    for key in ids:
        evidence_row(session, run, key)
    return eligible


def analyzer_config(gateway, request):
    try:
        return gateway.config(request.state.workspace_id, request.state.session_hash)
    except ProviderError:
        raise HTTPException(422, "尚未配置模型服务，请先在模型中心配置并启用模型") from None


def check_config(config, audit):
    if (config["provider"], config["model"], config.get("id")) != (
            audit.provider, audit.model, audit.provider_config_id) or config_version(config) != audit.provider_config_version:
        raise HTTPException(409, "模型配置已变更，请重新发起并确认分析")


def config_version(config):
    # Bind even an in-place/session key or endpoint change without storing the config or credentials.
    value = [config.get(key) for key in ("id", "provider", "model", "base_url", "encrypted_api_key")]
    return hashlib.sha256(json.dumps(value, ensure_ascii=True).encode()).hexdigest()


def metadata_values(metadata):
    count = metadata.get("model_request_count")
    count = count if type(count) is int and count in {0, 1} else None
    model = metadata.get("resolved_model")
    if not isinstance(model, str) or not model or len(model) > 160 or model == "auto":
        model = None
    return count, model


def finish_failed(engine, intent_id, metadata, *, interrupted=False):
    count, model = metadata_values(metadata)
    interrupted = interrupted or count is None or (count == 1 and metadata.get("completed_model_requests") != 1)
    try:
        with Session(engine) as session:
            session.execute(update(AnalysisAudit).execution_options(synchronize_session=False).where(AnalysisAudit.id == intent_id,
                AnalysisAudit.status == "invoking").values(status="unknown" if interrupted else "failed",
                    request_count=None if interrupted else count, resolved_model=model, active_run_id=None,
                    finished_at=now(), error_code="interrupted" if interrupted else "analysis_failed"))
            session.commit()
    except Exception:
        # A DB outage preserves the committed lease. Recovery later records unknown; never redispatch.
        pass


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


@router.post("/api/test-runs/{run_id}/analysis-intents", status_code=201)
def prepare_analysis(run_id: str, request: Request, data: AnalyzeInput):
    engine, gateway = request.app.state.store.engine, request.app.state.gateway
    with Session(engine) as session:
        run = revalidate(session, request, run_id)
        report = materialize(session, run)
        eligible_selection(session, run, report, data.execution_ids)
        config = analyzer_config(gateway, request)
        recover_stale(session, run_id)
        timestamp = now()
        audit = AnalysisAudit(workspace_id=run.workspace_id, user_id=request.state.user_id, run_id=run.id,
            report_id=report.id, selected_execution_ids=list(data.execution_ids),
            analyzed_execution_ids=[key for key in data.execution_ids if data.refresh or key not in (report.analysis or {})],
            refresh=data.refresh, provider=config["provider"], model=config["model"], provider_config_id=config.get("id"),
            provider_config_version=config_version(config),
            action_at=timestamp, expires_at=timestamp + timedelta(seconds=INTENT_SECONDS),
            notice_version=NOTICE_VERSION, status="prepared", request_count=0)
        session.add(audit)
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise HTTPException(503, ANALYSIS_ERROR) from None
        return audit_info(audit)


@router.get("/api/test-runs/{run_id}/analysis-intents/{intent_id}")
def get_analysis_intent(run_id: str, intent_id: str, request: Request):
    with Session(request.app.state.store.engine) as session:
        run = owned_run(session, request, run_id)
        recover_stale(session, run_id)
        session.commit()
        return audit_info(owned_intent(session, request, run, intent_id))


@router.post("/api/test-runs/{run_id}/analyze")
async def analyze(run_id: str, request: Request, data: ConfirmAnalysisInput):
    engine, gateway = request.app.state.store.engine, request.app.state.gateway
    with Session(engine) as session:
        run = revalidate(session, request, run_id)
        recover_stale(session, run_id)
        session.commit()
        audit = owned_intent(session, request, run, data.intent_id, confirming=True)
        report = materialize(session, run)
        if audit.report_id != report.id:
            raise HTTPException(404, "分析授权不存在")
        if audit.status in {"succeeded", "cached"}:
            return report_info(report)
        if audit.status != "prepared" or audit.expires_at.replace(tzinfo=timezone.utc) <= now():
            raise HTTPException(409, "分析授权已使用或过期，请重新发起并确认")
        eligible_selection(session, run, report, audit.selected_execution_ids)
        config = analyzer_config(gateway, request)
        check_config(config, audit)
        # One atomic conditional update both consumes the intent and acquires the DB unique lease.
        timestamp = now()
        try:
            claimed = session.execute(update(AnalysisAudit).execution_options(synchronize_session=False).where(AnalysisAudit.id == audit.id,
                AnalysisAudit.status == "prepared", AnalysisAudit.expires_at > timestamp).values(
                    status="invoking", confirmed_at=timestamp, active_run_id=run.id, request_count=None,
                    expires_at=timestamp + timedelta(seconds=INVOCATION_SECONDS)))
            if claimed.rowcount != 1:
                raise HTTPException(409, "分析授权已使用，请刷新页面")
            session.commit()  # No Provider code is reachable before this succeeds.
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, "该报告正在进行 AI 分析") from None
        except HTTPException:
            raise
        except Exception:
            session.rollback()
            raise HTTPException(503, ANALYSIS_ERROR) from None
    metadata = {}
    try:
        # A new transaction sees cache updates committed before the lease was acquired (also on MySQL).
        with Session(engine) as session:
            run = revalidate(session, request, run_id)
            audit = owned_intent(session, request, run, data.intent_id, confirming=True)
            report = materialize(session, run)
            config = analyzer_config(gateway, request)
            check_config(config, audit)
            eligible = eligible_selection(session, run, report, audit.selected_execution_ids)
            missing = [key for key in audit.analyzed_execution_ids if audit.refresh or key not in (report.analysis or {})]
            cases = {c["execution_id"]: c for c in report.summary["cases"] if c["execution_id"]}
            items = [analysis_context(cases[key], eligible[key], evidence_row(session, run, key)) for key in missing]
            changed = session.execute(update(AnalysisAudit).execution_options(synchronize_session=False).where(AnalysisAudit.id == audit.id,
                AnalysisAudit.status == "invoking", AnalysisAudit.expires_at > now()).values(
                    analyzed_execution_ids=missing, dispatch_at=now() if missing else None,
                    status="invoking" if missing else "cached", request_count=None if missing else 0,
                    active_run_id=run.id if missing else None, finished_at=None if missing else now()))
            if changed.rowcount != 1:
                raise HTTPException(409, "分析调用状态已失效，请重新发起并确认")
            session.commit()
            if not missing:
                return report_info(report)
        result = await ResultAnalyzerAgent(gateway).analyze(
            request.state.workspace_id, request.state.session_hash, items, config, on_metadata=metadata.update
        )
        with Session(engine) as session:
            run = revalidate(session, request, run_id)
            report = materialize(session, run)
            count, resolved_model = metadata_values(metadata)
            changed = session.execute(update(AnalysisAudit).execution_options(synchronize_session=False).where(AnalysisAudit.id == data.intent_id,
                AnalysisAudit.status == "invoking", AnalysisAudit.active_run_id == run.id,
                AnalysisAudit.expires_at > now()).values(status="succeeded", request_count=count,
                    resolved_model=resolved_model, active_run_id=None, finished_at=now()))
            if changed.rowcount != 1:
                raise HTTPException(409, "分析调用状态已失效；未保存过期结果")
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
                    "model": resolved_model or config["model"],
                }
            report.analysis = overlay  # Full replacement, never in-place mutation of SQLAlchemy JSON.
            session.commit()
            return report_info(report)
    except asyncio.CancelledError:
        finish_failed(engine, data.intent_id, metadata, interrupted=True)
        raise
    except HTTPException:
        finish_failed(engine, data.intent_id, metadata)
        raise
    except Exception:
        finish_failed(engine, data.intent_id, metadata)
        raise HTTPException(502, ANALYSIS_ERROR) from None


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
