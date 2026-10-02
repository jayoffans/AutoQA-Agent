"""Stable API quality snapshots. Stored execution outcomes are never evaluated here."""

import html
import json
import re
from datetime import timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .crypto import scrub
from .execution_privacy import redact_assertions, safe_text
from .product_models import ApiEndpointRow, TestCase, TestExecution, TestPlan
from .store_base import now
from .studio_models import ProjectRow, ReportRow
from .test_execution_api import execution_status, run_summary
from .test_plan_api import utc_time

EXCERPT_BYTES = 4096
ASSERTION_BYTES = 512
UNAVAILABLE = "未保存 / 不可恢复 (Not saved / unavailable)"
AI_LABEL = "AI Analysis / AI Suggestion"
ROOT_LABEL = "Root Cause Not Confirmed"


def excerpt(value, limit):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, allow_nan=False)
    encoded = text.encode("utf-8")
    return encoded[:limit].decode("utf-8", errors="ignore"), len(encoded) > limit


def clean_evidence(row, *, bounded=False):
    """Defense in depth using the execution privacy helpers, without decrypting secrets."""
    data = row.result_data or {}
    assertions = redact_assertions(data.get("assertion_results", []), [])
    body = row.response_body
    if body is not None:
        try:
            parsed = json.loads(body)
            redacted = scrub(parsed, [])
            if redacted != parsed:
                body = json.dumps(redacted, ensure_ascii=False)
        except (ValueError, RecursionError):
            body = safe_text(body, [])
    result = {
        "execution_id": row.id,
        "case_id": row.case_id,
        "status": execution_status(row),
        "classification_inferred": row.status is None,
        "method": row.method,
        "url": scrub(row.url, []),
        "request_data": scrub(row.request_data, []),
        "response_status": row.response_status,
        "response_body": body,
        "duration_ms": row.duration_ms,
        "error_message": scrub(row.error_message, []),
        "error_code": data.get("error_code"),
        "expected_status": data.get("expected_status"),
        "assertion_results": assertions,
        "assertion_evidence_available": "assertion_results" in data,
        "response_body_truncated": bool(data.get("response_body_truncated")),
    }
    if bounded:
        if body is not None:
            result["response_body"], cut = excerpt(body, EXCERPT_BYTES)
            result["response_body_truncated"] |= cut
        result["request_data"], result["request_data_truncated"] = excerpt(
            result["request_data"], EXCERPT_BYTES
        )
        for item in assertions:
            for key in ("expected", "actual"):
                if key in item:
                    rendered, cut = excerpt(item[key], ASSERTION_BYTES)
                    if cut:
                        item[key] = rendered
                        item[key + "_truncated"] = True
    return result


def severity(status, category, expected_status, actual_status):
    if status == "execution_error":
        return "Low", "execution_error"
    if (
        category == "security"
        and expected_status in {401, 403}
        and actual_status is not None
        and 200 <= actual_status < 300
    ):
        return "High", "reviewed_denial_not_met"
    if (
        actual_status is not None
        and 500 <= actual_status < 600
        and actual_status != expected_status
        and expected_status is not None
    ):
        return "High", "unexpected_server_status"
    return "Medium", "assertion_failure"


def build_snapshot(session, run, created_at):
    # Aggregate projection does not load any response body.
    rows = list(
        session.execute(
            select(
                TestExecution.id,
                TestExecution.case_id,
                TestExecution.status,
                TestExecution.passed,
                TestExecution.response_status,
                TestExecution.duration_ms,
                TestExecution.method,
            )
            .where(TestExecution.run_id == run.id, TestExecution.workspace_id == run.workspace_id)
            .order_by(TestExecution.id)
        )
    )
    summary = run_summary(run, rows)
    summary["pass_rate"] = (
        round(summary["passed_cases"] / run.total_cases * 100, 2) if run.total_cases else None
    )
    start = run.started_at.replace(tzinfo=timezone.utc)
    summary["duration_ms"] = (
        max(0, int((run.finished_at.replace(tzinfo=timezone.utc) - start).total_seconds() * 1000))
        if run.finished_at
        else None
    )
    summary["incomplete"] = summary["pending_cases"] > 0 or summary["lifecycle"] != "completed"
    cases = list(
        session.execute(
            select(TestCase.id, TestCase.name, TestCase.category, TestCase.endpoint_id)
            .where(TestCase.plan_id == run.plan_id, TestCase.workspace_id == run.workspace_id)
            .order_by(TestCase.created_at, TestCase.id)
        )
    )
    endpoints = {
        e.id: e
        for e in session.scalars(
            select(ApiEndpointRow).where(
                ApiEndpointRow.workspace_id == run.workspace_id,
                ApiEndpointRow.project_id == run.project_id,
                ApiEndpointRow.spec_id == session.get(TestPlan, run.plan_id).api_spec_id,
            )
        )
    }
    executions = {row.case_id: row for row in rows}
    outcomes, findings, tested, categories = [], [], {}, {}
    for case in cases:
        row, endpoint = executions.get(case.id), endpoints.get(case.endpoint_id)
        data = (
            (
                session.scalar(
                    select(TestExecution.result_data).where(
                        TestExecution.id == row.id,
                        TestExecution.run_id == run.id,
                        TestExecution.workspace_id == run.workspace_id,
                    )
                )
                or {}
            )
            if row
            else {}
        )
        category = case.category
        item = {
            "case_id": case.id,
            "execution_id": row.id if row else None,
            "case_name": scrub(data.get("case_name", case.name), []),
            "category": category,
            "endpoint_id": case.endpoint_id,
            "method": row.method if row else endpoint.method if endpoint else UNAVAILABLE,
            "endpoint": scrub(endpoint.path, []) if endpoint else UNAVAILABLE,
            "status": execution_status(row) if row else "pending",
            "classification_inferred": row.status is None if row else False,
            "response_status": row.response_status if row else None,
            "expected_status": data.get("expected_status"),
            "duration_ms": row.duration_ms if row else None,
            "error_code": data.get("error_code"),
            "assertion_evidence_available": "assertion_results" in data,
        }
        outcomes.append(item)
        categories[category] = categories.get(category, 0) + 1
        if row:
            tested[case.endpoint_id] = {
                "endpoint_id": case.endpoint_id,
                "method": item["method"],
                "path": item["endpoint"],
            }
        if row and item["status"] in {"failed", "execution_error"}:
            level, reason = severity(
                item["status"], category, item["expected_status"], item["response_status"]
            )
            assertion = next(
                (
                    a
                    for a in redact_assertions(data.get("assertion_results", []), [])
                    if a.get("status") == "failed"
                ),
                None,
            )
            expected = (
                {k: assertion[k] for k in ("type", "path", "operator", "expected") if k in assertion}
                if assertion
                else data.get("expected_status", UNAVAILABLE)
            )
            actual = (
                {
                    k: assertion[k]
                    for k in ("actual", "actual_exists", "status", "reason_code")
                    if k in assertion
                }
                if assertion
                else row.response_status
            )
            if item["status"] == "execution_error":
                actual = item["error_code"] or UNAVAILABLE
            expected_excerpt, expected_cut = excerpt(expected, ASSERTION_BYTES)
            actual_excerpt, actual_cut = excerpt(
                actual if actual is not None else UNAVAILABLE, ASSERTION_BYTES
            )
            findings.append(
                {
                    "id": f"API-{run.id}-{row.id}",
                    "source_execution_id": row.id,
                    "project_id": run.project_id,
                    "run_id": run.id,
                    "title": item["case_name"],
                    "severity": level,
                    "severity_reason": reason,
                    "category": category,
                    "kind": item["status"],
                    "expected": expected_excerpt,
                    "actual": actual_excerpt,
                    "expected_truncated": expected_cut
                    or bool(assertion and assertion.get("expected_truncated")),
                    "actual_truncated": actual_cut or bool(assertion and assertion.get("actual_truncated")),
                    "evidence": {
                        "execution_id": row.id,
                        "assertion_evidence_available": item["assertion_evidence_available"],
                    },
                    "recommendation": "复核已保存的执行证据与审核预期；根因尚未确认。",
                    "root_cause_confirmed": False,
                }
            )
    findings.sort(key=lambda f: ({"High": 0, "Medium": 1, "Low": 2}[f["severity"]], f["source_execution_id"]))
    project = session.get(ProjectRow, run.project_id)
    return {
        "schema_version": 1,
        "metadata": {
            "project_id": run.project_id,
            "project_name": scrub(project.name, []),
            "plan_id": run.plan_id,
            "run_id": run.id,
            "created_at": utc_time(created_at),
            "started_at": utc_time(run.started_at),
            "finished_at": utc_time(run.finished_at) if run.finished_at else None,
            "stored_run_status": run.status,
        },
        "summary": summary,
        "scope": {
            "tested_endpoints": sorted(
                tested.values(), key=lambda e: (e["method"], e["path"], e["endpoint_id"])
            ),
            "case_categories": categories,
            "executed_cases": len(rows),
        },
        "cases": outcomes,
        "findings": findings,
    }


def materialize(session, run):
    if run.status == "running":
        raise HTTPException(409, "运行中的测试尚无稳定报告")
    row = session.scalar(select(ReportRow).where(ReportRow.api_run_id == run.id))
    if row is None:
        timestamp = now()
        row = ReportRow(
            id=str(uuid4()),
            api_run_id=run.id,
            summary=build_snapshot(session, run, timestamp),
            created_at=timestamp,
            html_content="",
            html_path=None,
        )
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            row = session.scalar(select(ReportRow).where(ReportRow.api_run_id == run.id))
            if row is None:
                raise
    return row


def report_info(row):
    return {"id": row.id, **row.summary, "analysis": row.analysis or {}}


def export_sections(session, run, report):
    yield "Metadata", report.summary["metadata"]
    yield "Summary · Pass Rate = passed / total × 100", report.summary["summary"]
    yield "Scope", report.summary["scope"]
    yield "Cases · Passed / Failed / Execution Error / Pending", report.summary["cases"]
    yield "Failed Cases / Failure Findings", [f for f in report.summary["findings"] if f["kind"] == "failed"]
    yield (
        "Execution Errors / Execution Error Findings",
        [f for f in report.summary["findings"] if f["kind"] == "execution_error"],
    )
    for case in report.summary["cases"]:
        if case["execution_id"]:
            row = session.scalar(
                select(TestExecution).where(
                    TestExecution.id == case["execution_id"],
                    TestExecution.run_id == run.id,
                    TestExecution.workspace_id == run.workspace_id,
                )
            )
            yield f"Evidence · {case['case_name']} · {case['status']}", clean_evidence(row, bounded=True)
    yield AI_LABEL + " · " + ROOT_LABEL, report.analysis or {"status": "not_requested"}


def render_export(sections, format):
    """Only trusted structure is markup; every value is escaped or fenced as data."""
    if format == "html":
        yield """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>AutoQA Studio · Quality Report</title><style>body{background:#101116;color:#e7eaf2;font:14px/1.7 system-ui;margin:0}main{max-width:1100px;margin:auto;padding:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#191c24;padding:16px;border-radius:8px}h2{font-size:18px}</style></head><body><main><h1>Quality Report</h1><p>Root Cause Not Confirmed · 摘录可能裁剪，完整已保存证据请在产品内查看。</p>"""
    else:
        yield "# Quality Report\n\nRoot Cause Not Confirmed · 摘录可能裁剪，完整已保存证据请在产品内查看。\n\n"
    for title, value in sections:
        data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
        if format == "html":
            if title.startswith("Summary"):
                labels = {
                    "total_cases": "Total",
                    "passed_cases": "Passed",
                    "failed_cases": "Failed",
                    "execution_error_cases": "Execution Error",
                    "pending_cases": "Pending",
                    "pass_rate": "Pass Rate (%)",
                    "duration_ms": "Duration (ms)",
                }
                cards = "".join(
                    f'<article style="display:inline-block;min-width:100px;margin:6px;padding:12px;background:#191c24"><div>{label}</div><strong>{html.escape(str(value[key] if value[key] is not None else "—"))}</strong></article>'
                    for key, label in labels.items()
                )
                yield f"<section><h2>{html.escape(title)}</h2>{cards}<p>Lifecycle: {html.escape(value['lifecycle'])} · Incomplete: {value['incomplete']}</p></section>"
                continue
            if title == "Scope":
                endpoints = "".join(
                    f"<li>{html.escape(item['method'])} {html.escape(item['path'])}</li>"
                    for item in value["tested_endpoints"]
                )
                yield f"<section><h2>Scope</h2><p>Executed cases: {value['executed_cases']}</p><ul>{endpoints}</ul><pre>{html.escape(json.dumps(value['case_categories'], ensure_ascii=False))}</pre></section>"
                continue
            yield f"<section><h2>{html.escape(title)}</h2><pre>{html.escape(data)}</pre></section>"
        else:
            # The heading is data too: titles may contain untrusted case names.
            text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", title + "\n" + data)
            fence = "`" * max(3, 1 + max((len(m[0]) for m in re.finditer(r"`+", text)), default=0))
            yield f"{fence}text\n{text}\n{fence}\n\n"
    if format == "html":
        yield "</main></body></html>"
