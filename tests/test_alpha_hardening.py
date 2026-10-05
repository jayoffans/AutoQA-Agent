"""Alpha regression: fake models, socket/MockTransport evidence, persisted consent."""

import asyncio
import copy
import gzip
import json
import logging
import ssl
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from autoqa.api_executor import ApiExecutor, CaseConfig
from autoqa.api_reports import clean_evidence
from autoqa.product_models import AnalysisAudit, MemberRow, ProviderConfigRow
from autoqa.product_models import TestExecution as Execution
from autoqa.store_base import now
from autoqa.studio_models import ReportRow
from autoqa.target_network import TargetResolutionError, safe_diagnostics
from tests.test_api_execution import approve, execute
from tests.test_api_execution import execution as execution  # noqa: F401
from tests.test_api_execution import imports as imports  # noqa: F401
from tests.test_api_execution import planner as planner  # noqa: F401
from tests.test_api_execution import product as product  # noqa: F401
from tests.test_api_reports import analysis_output, facts, mock_analyzer, selected
from tests.test_api_reports import reported as reported  # noqa: F401
from tests.test_auth import sign_up


def prepare(client, ids=None, refresh=False):
    result = client.post(
        client.report_path + "/analysis-intents",
        json={"execution_ids": ids or selected(client), "refresh": refresh},
    )
    assert result.status_code == 201, result.text
    return result.json()


def confirm(client, intent):
    return client.post(client.report_path + "/analyze", json={"intent_id": intent["intent_id"]})


def audit(client, intent):
    response = client.get(client.report_path + "/analysis-intents/" + intent["intent_id"])
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("encoding", ["identity", "gzip", "deflate"])
@pytest.mark.parametrize("status", [200, 422])
async def test_decoded_response_matches_wire_status_and_body(monkeypatch, encoding, status):
    monkeypatch.setattr("autoqa.api_executor.validate_base_url", lambda url: None)
    monkeypatch.setattr("autoqa.api_executor.resolve_target", lambda *args: "93.184.216.34")
    payload = json.dumps({"detail": "validated", "secret": "response-secret-canary"}).encode()
    encoded = (
        gzip.compress(payload)
        if encoding == "gzip"
        else zlib.compress(payload)
        if encoding == "deflate"
        else payload
    )
    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(
            status,
            headers={
                "Content-Encoding": encoding,
                "Content-Length": str(len(encoded)),
                "Content-Type": "application/json",
                "Set-Cookie": "private-cookie-canary",
            },
            stream=httpx.ByteStream(encoded),
        )

    executor = ApiExecutor()
    executor.transport_factory = lambda: httpx.MockTransport(response)
    result = await executor.execute(
        SimpleNamespace(id="c", workspace_id="w", plan_id="p", name="candidate"),
        SimpleNamespace(method="GET", path="/search"),
        "https://qa.example",
        CaseConfig.model_validate(
            {
                "request_data": {},
                "expected_status": status,
                "assertions": [
                    {"type": "json", "path": "detail", "operator": "equals", "expected": "validated"}
                ],
            }
        ),
        "run",
    )
    assert len(calls) == 1 and result.status == "passed" and result.response_status == status
    diagnostics = result.result_data["diagnostics"]
    assert diagnostics["encoding"] == encoding and diagnostics["observed_http_status"] == status
    assert diagnostics["decoded_bytes"] == len(payload) and diagnostics["response_headers_observed"]
    assert "private-cookie-canary" not in json.dumps(result.result_data)
    assert "response-secret-canary" not in result.response_body


@pytest.mark.parametrize(
    "kind,code,category",
    [
        ("dns", "dns_error", "dns"),
        ("policy", "policy_rejected", "policy"),
        ("connection", "connection_error", "connection"),
        ("tls", "tls_error", "tls"),
        ("timeout", "timeout", "timeout"),
        ("protocol", "transport_error", "protocol"),
        ("corrupt", "transport_error", "decoding"),
        ("oversize", "response_too_large", "response_size"),
    ],
)
async def test_transport_errors_are_not_assertion_failures(monkeypatch, kind, code, category):
    calls = []

    def dns(url):
        if kind == "dns":
            raise TargetResolutionError("fake API Key / Authorization / exception-canary")
        if kind == "policy":
            raise ValueError("exception-canary")

    monkeypatch.setattr("autoqa.api_executor.validate_base_url", dns)
    monkeypatch.setattr("autoqa.api_executor.resolve_target", lambda *args: "93.184.216.34")

    def response(request):
        calls.append(request)
        if kind in {"connection", "tls"}:
            error = httpx.ConnectError("exception-canary", request=request)
            if kind == "tls":
                error.__cause__ = ssl.SSLError("exception-canary")
            raise error
        if kind == "timeout":
            raise httpx.ReadTimeout("exception-canary", request=request)
        if kind == "protocol":
            raise httpx.RemoteProtocolError("exception-canary", request=request)
        payload = b"invalid-compression-canary" if kind == "corrupt" else gzip.compress(b"x" * 256_001)
        return httpx.Response(
            422,
            headers={"Content-Encoding": "gzip", "Authorization": "secret-canary"},
            stream=httpx.ByteStream(payload),
        )

    executor = ApiExecutor()
    executor.transport_factory = lambda: httpx.MockTransport(response)
    result = await executor.execute(
        SimpleNamespace(id="c", workspace_id="w", plan_id="p"),
        SimpleNamespace(method="GET", path="/search"),
        "https://qa.example",
        CaseConfig.model_validate(
            {
                "request_data": {},
                "expected_status": 422,
                "assertions": [{"type": "json", "path": "detail", "operator": "exists"}],
            }
        ),
        "run",
    )
    assert len(calls) == (0 if kind in {"dns", "policy"} else 1)
    assert result.status == "execution_error" and result.result_data["error_code"] == code
    assert result.response_status is None and result.response_body is None
    assert all(v["status"] == "not_evaluated" for v in result.result_data["assertion_results"])
    diagnostics = result.result_data["diagnostics"]
    assert diagnostics["exception_category"] == category
    assert diagnostics["response_headers_observed"] == (kind in {"corrupt", "oversize"})
    assert "canary" not in json.dumps(result.result_data) and "canary" not in result.error_message


def test_review_candidate_does_not_replace_frozen_configuration(execution):
    before = execution.get(execution.plan_path + "/cases").json()
    chosen = execution.review["cases"][0]
    chosen["request_data"]["query"] = {"reviewed": "human"}
    chosen["expected_status"] = 422
    chosen["assertions"] = [{"type": "json", "path": "detail", "operator": "equals", "expected": "human"}]
    execution.handler = lambda req: httpx.Response(422, json={"detail": "human"})
    assert approve(execution).status_code == 200
    assert execution.get(execution.plan_path + "/cases").json() == before
    preview = execution.get(execution.plan_path + "/execution-preview").json()
    saved = next(c for c in preview["cases"] if c["case_id"] == chosen["case_id"])
    assert saved["config"]["request_data"]["query"] == {"reviewed": "human"}
    assert saved["config"]["expected_status"] == 422 and preview["approved_by"] and preview["approved_at"]
    result = execute(execution)
    detail = execution.get("/api/test-runs/" + result.json()["id"]).json()
    row = next(e for e in detail["executions"] if e["case_id"] == chosen["case_id"])
    assert row["status"] == "passed" and row["request_data"]["query"] == {"reviewed": "human"}
    assert row["result_data"]["expected_status"] == 422
    assert len(execution.api_calls) == 8


def test_prepare_commit_confirmation_order_cached_refresh_and_duplicate(reported):
    intent = prepare(reported)
    baseline = copy.deepcopy(facts(reported))
    original_summary = reported.get(reported.report_path + "/report").json()["summary"]
    assert intent["status"] == "prepared" and intent["confirmed_at"] is None and intent["request_count"] == 0
    calls = []

    def model(request):
        with Session(reported.application.state.store.engine) as session:
            saved = session.get(AnalysisAudit, intent["intent_id"])
            assert saved.status == "invoking" and saved.confirmed_at and saved.dispatch_at
            assert saved.active_run_id == reported.run_id and saved.request_count is None
        calls.append(request)
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        return httpx.Response(
            200,
            json={
                "model": "resolved-fixture-model",
                "choices": [{"message": {"content": json.dumps(analysis_output(context["executions"]))}}],
            },
        )

    reported.application.state.gateway.transport_factory = lambda: httpx.MockTransport(model)
    assert calls == []
    result = confirm(reported, intent)
    assert result.status_code == 200, result.text
    saved = audit(reported, intent)
    assert saved["status"] == "succeeded" and saved["request_count"] == 1 and saved["resolved_model"]
    assert saved["action_at"] <= saved["confirmed_at"] <= saved["dispatch_at"] <= saved["finished_at"]
    assert confirm(reported, intent).status_code == 200 and len(calls) == 1
    cached = prepare(reported)
    assert cached["to_analyze_execution_ids"] == []
    assert confirm(reported, cached).status_code == 200 and audit(reported, cached)["request_count"] == 0
    intent = prepare(reported, refresh=True)
    assert confirm(reported, intent).status_code == 200 and len(calls) == 2
    assert facts(reported) == baseline and result.json()["summary"] == original_summary


def test_expiry_config_change_and_legacy_body_never_dispatch(reported):
    calls = mock_analyzer(reported)
    intent = prepare(reported)
    with Session(reported.application.state.store.engine) as session:
        session.execute(
            update(AnalysisAudit)
            .where(AnalysisAudit.id == intent["intent_id"])
            .values(expires_at=now() - timedelta(seconds=1))
        )
        session.commit()
    assert confirm(reported, intent).status_code == 409
    intent = prepare(reported)
    with Session(reported.application.state.store.engine) as session:
        session.execute(update(ProviderConfigRow).values(model_name="different-fixture"))
        session.commit()
    assert confirm(reported, intent).status_code == 409
    assert (
        reported.post(
            reported.report_path + "/analyze", json={"execution_ids": selected(reported)}
        ).status_code
        == 422
    )
    assert calls == []


def test_two_intents_share_database_exclusivity_and_stale_never_redispatch(reported):
    one, two = prepare(reported), prepare(reported)
    entered, release = Event(), Event()
    calls = []

    async def model(request):
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(context)
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(analysis_output(context["executions"]))}}]},
        )

    reported.application.state.gateway.transport_factory = lambda: httpx.MockTransport(model)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(confirm, reported, one)
        try:
            assert entered.wait(5)
            assert confirm(reported, two).status_code == 409
            assert confirm(reported, one).status_code == 409
        finally:
            release.set()
        assert first.result().status_code == 200
    assert len(calls) == 1
    with Session(reported.application.state.store.engine) as session:
        session.execute(
            update(AnalysisAudit)
            .where(AnalysisAudit.id == two["intent_id"])
            .values(
                status="invoking",
                active_run_id=reported.run_id,
                confirmed_at=now(),
                dispatch_at=now(),
                request_count=None,
                expires_at=now() - timedelta(seconds=1),
            )
        )
        session.commit()
    stale = audit(reported, two)
    assert stale["status"] == "unknown" and stale["request_count"] is None
    assert confirm(reported, two).status_code == 409 and len(calls) == 1
    new = prepare(reported, refresh=True)
    assert len(calls) == 1
    assert confirm(reported, new).status_code == 200 and len(calls) == 2


def test_audit_run_execution_intent_isolation_and_secret_allowlist(reported):
    intent = prepare(reported)
    assert reported.get(reported.report_path + "/analysis-intents/" + str(uuid4())).status_code == 404
    assert (
        reported.post(
            reported.report_path + "/analyze", json={"intent_id": intent["intent_id"], "user_id": "forged"}
        ).status_code
        == 422
    )
    assert (
        reported.post(
            reported.report_path + "/analysis-intents", json={"execution_ids": [str(uuid4())]}
        ).status_code
        == 422
    )
    assert set(intent) <= {
        "workspace_id",
        "user_id",
        "run_id",
        "report_id",
        "selected_execution_ids",
        "analyzed_execution_ids",
        "refresh",
        "provider",
        "model",
        "resolved_model",
        "notice_version",
        "status",
        "request_count",
        "error_code",
        "intent_id",
        "action_at",
        "confirmed_at",
        "dispatch_at",
        "finished_at",
        "expires_at",
        "cached_execution_ids",
        "to_analyze_execution_ids",
    }
    for forbidden in (
        "api_key",
        "Authorization",
        "session_hash",
        "prompt",
        "evidence",
        "encrypted",
        "sk-fixture",
    ):
        assert forbidden not in json.dumps(intent)


def test_confirm_commit_failure_never_dispatches(reported, monkeypatch):
    intent = prepare(reported)
    calls = mock_analyzer(reported)
    original = Session.commit

    def fail_confirm(session):
        if any(isinstance(obj, AnalysisAudit) for obj in session.identity_map.values()):
            raise RuntimeError("exception-secret-canary")
        return original(session)

    monkeypatch.setattr(Session, "commit", fail_confirm)
    assert confirm(reported, intent).status_code == 503
    assert calls == []


def test_prepare_persistence_failure_never_dispatches_or_leaves_intent(reported, monkeypatch, caplog):
    calls = mock_analyzer(reported)
    original = Session.commit

    def fail_prepare(session):
        if any(isinstance(obj, AnalysisAudit) for obj in session.new):
            raise RuntimeError("prepare-exception-secret-canary")
        return original(session)

    monkeypatch.setattr(Session, "commit", fail_prepare)
    result = reported.post(
        reported.report_path + "/analysis-intents", json={"execution_ids": selected(reported)}
    )
    assert result.status_code == 503 and calls == []
    with Session(reported.application.state.store.engine) as session:
        assert session.query(AnalysisAudit).count() == 0
    assert "prepare-exception-secret-canary" not in result.text + caplog.text


@pytest.mark.parametrize("save_mode", ["workspace", "session"])
def test_key_and_endpoint_changes_require_new_consent(reported, save_mode):
    calls = mock_analyzer(reported)
    data = {
        "provider": "compatible",
        "model": "fixture-model",
        "base_url": "https://model.example/v1",
        "api_key": "configuration-key-canary-original",
        "save_mode": save_mode,
    }
    assert reported.put("/api/provider", json=data).status_code == 200
    intent = prepare(reported)
    # The legacy/session configuration can keep its ID and model while replacing a key or URL.
    assert (
        reported.put(
            "/api/provider", json={**data, "api_key": "configuration-key-canary-replacement"}
        ).status_code
        == 200
    )
    assert confirm(reported, intent).status_code == 409
    intent = prepare(reported)
    assert (
        reported.put("/api/provider", json={**data, "base_url": "https://model.example/v2"}).status_code
        == 200
    )
    assert confirm(reported, intent).status_code == 409 and calls == []


@pytest.mark.parametrize(
    "mode,status,count",
    [
        ("http", "failed", 1),
        ("invalid-json", "failed", 1),
        ("connection", "unknown", None),
        ("timeout", "unknown", None),
    ],
)
def test_provider_failure_audit_is_safe_and_never_retries(reported, mode, status, count, caplog):
    baseline = copy.deepcopy(facts(reported))
    original = reported.get(reported.report_path + "/report").json()
    intent, calls = prepare(reported), []

    def model(request):
        calls.append(request.url.path)
        if mode == "connection":
            raise httpx.ConnectError("Bearer provider-exception-secret-canary", request=request)
        if mode == "timeout":
            raise httpx.ReadTimeout("Bearer provider-exception-secret-canary", request=request)
        return httpx.Response(500 if mode == "http" else 200, text="raw-provider-response-secret-canary")

    reported.application.state.gateway.transport_factory = lambda: httpx.MockTransport(model)
    caplog.set_level(logging.DEBUG)
    result = confirm(reported, intent)
    assert result.status_code == 502 and len(calls) == 1
    saved = audit(reported, intent)
    assert saved["status"] == status and saved["request_count"] == count and saved["finished_at"]
    assert confirm(reported, intent).status_code == 409 and len(calls) == 1
    assert reported.get(reported.report_path + "/report").json() == original and facts(reported) == baseline
    assert "secret-canary" not in result.text + json.dumps(saved) + caplog.text


def test_cancelled_invocation_is_unknown_without_second_request(reported):
    intent, calls = prepare(reported), []
    original = reported.get(reported.report_path + "/report").json()

    async def interrupted(request):
        calls.append(request.url.path)
        raise asyncio.CancelledError()

    reported.application.state.gateway.transport_factory = lambda: httpx.MockTransport(interrupted)
    # Starlette's outer middleware returns its generic unavailable response after cancellation.
    assert confirm(reported, intent).status_code == 500
    saved = audit(reported, intent)
    assert saved["status"] == "unknown" and saved["request_count"] is None
    assert confirm(reported, intent).status_code == 409 and len(calls) == 1
    assert reported.get(reported.report_path + "/report").json() == original


@pytest.mark.parametrize("outage", [False, True])
def test_result_persistence_failure_preserves_facts_and_committed_lease(reported, monkeypatch, outage):
    intent = prepare(reported)
    before = copy.deepcopy(facts(reported))
    report = reported.get(reported.report_path + "/report").json()
    calls = mock_analyzer(reported)
    original = Session.commit
    failed = False

    def fail_result(session):
        nonlocal failed
        writing_analysis = any(isinstance(obj, ReportRow) and obj.analysis for obj in session.dirty)
        if writing_analysis or (outage and failed):
            failed = True
            raise RuntimeError("persistence-exception-secret-canary")
        return original(session)

    monkeypatch.setattr(Session, "commit", fail_result)
    result = confirm(reported, intent)
    assert result.status_code == 502 and len(calls) == 1
    monkeypatch.setattr(Session, "commit", original)
    with Session(reported.application.state.store.engine) as session:
        saved = session.get(AnalysisAudit, intent["intent_id"])
        assert saved.status == ("invoking" if outage else "failed")
        if outage:
            assert saved.active_run_id == reported.run_id and saved.request_count is None
            saved.expires_at = now() - timedelta(seconds=1)
            session.commit()
    saved = audit(reported, intent)
    assert saved["status"] == ("unknown" if outage else "failed")
    assert saved["request_count"] == (None if outage else 1)
    assert confirm(reported, intent).status_code == 409 and len(calls) == 1
    assert reported.get(reported.report_path + "/report").json() == report and facts(reported) == before
    assert "persistence-exception-secret-canary" not in result.text + json.dumps(saved)


def test_foreign_workspace_run_execution_and_actor_cannot_confirm_intent(reported):
    intent = prepare(reported)
    calls = mock_analyzer(reported)
    other_run = execute(reported).json()["id"]
    other_path = "/api/test-runs/" + other_run
    assert reported.post(other_path + "/analyze", json={"intent_id": intent["intent_id"]}).status_code == 404
    assert reported.get(other_path + "/analysis-intents/" + intent["intent_id"]).status_code == 404
    other_ids = reported.get(other_path + "/findings").json()
    assert (
        reported.post(
            reported.report_path + "/analysis-intents",
            json={"execution_ids": [other_ids[0]["source_execution_id"]]},
        ).status_code
        == 422
    )
    with TestClient(reported.application, base_url="https://testserver") as other:
        identity = sign_up(other, email="audit-other@example.com")
        assert other.get(reported.report_path + "/analysis-intents/" + intent["intent_id"]).status_code == 404
        assert (
            other.post(
                reported.report_path + "/analysis-intents", json={"execution_ids": selected(reported)}
            ).status_code
            == 404
        )
        assert (
            other.post(reported.report_path + "/analyze", json={"intent_id": intent["intent_id"]}).status_code
            == 404
        )
        with Session(reported.application.state.store.engine) as session:
            session.add(
                MemberRow(workspace_id=intent["workspace_id"], user_id=identity["user"]["id"], role="member")
            )
            session.commit()
        other.headers["X-Workspace-ID"] = intent["workspace_id"]
        assert other.get(reported.report_path + "/analysis-intents/" + intent["intent_id"]).status_code == 200
        assert (
            other.post(reported.report_path + "/analyze", json={"intent_id": intent["intent_id"]}).status_code
            == 404
        )
    assert calls == [] and audit(reported, intent)["status"] == "prepared"


@pytest.mark.parametrize(
    "reported_model",
    [None, "actual-resolved-model", "sk-fixture-1234", ["untrusted"], "Bearer model-secret-canary"],
)
def test_resolved_model_is_observed_or_unknown_and_never_a_secret(reported, reported_model):
    intent = prepare(reported)

    def model(request):
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        return httpx.Response(
            200,
            json={
                "model": reported_model,
                "choices": [{"message": {"content": json.dumps(analysis_output(context["executions"]))}}],
            },
        )

    reported.application.state.gateway.transport_factory = lambda: httpx.MockTransport(model)
    assert confirm(reported, intent).status_code == 200
    assert audit(reported, intent)["resolved_model"] == (
        "actual-resolved-model" if reported_model == "actual-resolved-model" else None
    )


def test_diagnostics_allowlist_and_legacy_unknown(reported):
    value = safe_diagnostics(
        {
            "phase": {},
            "exception_category": [],
            "encoding": {},
            "observed_http_status": True,
            "decoded_bytes": 9_999_999,
            "Authorization": "Bearer diagnostics-secret-canary",
        }
    )
    assert value == {
        "phase": None,
        "exception_category": None,
        "encoding": None,
        "observed_http_status": None,
        "decoded_bytes": 2_000_001,
        "response_headers_observed": False,
    }
    source = selected(reported, 1)[0]
    with Session(reported.application.state.store.engine) as session:
        row = session.get(Execution, source)
        data = dict(row.result_data)
        data.pop("diagnostics")
        row.result_data = data
        session.commit()
        assert clean_evidence(row)["diagnostics"] is None
    assert reported.get(reported.report_path + "/executions/" + source).json()["diagnostics"] is None
    assert '"diagnostics": null' in reported.get(reported.report_path + "/report.md").text


def test_sensitive_canaries_stay_out_of_audit_api_logs_and_exports(reported, monkeypatch, caplog):
    canaries = [
        "sk-fixture-1234",
        "authorization-canary-secret",
        "prompt-canary-secret",
        "evidence-canary-secret",
    ]
    source = selected(reported, 1)[0]
    with Session(reported.application.state.store.engine) as session:
        row = session.get(Execution, source)
        row.request_data = {"headers": {"Authorization": "Bearer " + canaries[1]}}
        row.response_body = json.dumps({"secret": canaries[3]})
        session.commit()
    from autoqa.result_analyzer import SYSTEM

    monkeypatch.setattr("autoqa.result_analyzer.SYSTEM", SYSTEM + "\nAuthorization: Bearer " + canaries[2])

    def model(request):
        payload = json.loads(request.content)
        assert canaries[2] in payload["messages"][0]["content"]
        context = json.loads(payload["messages"][1]["content"])
        assert canaries[1] not in json.dumps(context) and canaries[3] not in json.dumps(context)
        output = analysis_output(context["executions"])
        for item in output["analyses"]:
            item["analysis_summary"] = " ".join("Bearer " + value for value in canaries)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output)}}]})

    reported.application.state.gateway.transport_factory = lambda: httpx.MockTransport(model)
    intent = prepare(reported, [source])
    caplog.set_level(logging.DEBUG)
    result = confirm(reported, intent)
    assert result.status_code == 200
    saved = audit(reported, intent)
    with Session(reported.application.state.store.engine) as session:
        row = session.get(AnalysisAudit, intent["intent_id"])
        stored = json.dumps({col.name: str(getattr(row, col.name)) for col in row.__table__.columns})
    surfaces = result.text + json.dumps(saved) + stored + caplog.text
    for suffix in ("/report", "/report.html", "/report.md", "/executions/" + source):
        response = reported.get(reported.report_path + suffix)
        assert response.status_code == 200
        surfaces += response.text
    assert all(value not in surfaces for value in canaries)
    assert not any(
        field in stored
        for field in ("encrypted_api_key", "Authorization", "session_hash", "prompt", "response_body")
    )
