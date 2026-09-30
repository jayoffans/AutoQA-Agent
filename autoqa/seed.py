"""Deterministic seed identity; results computed by executing the local ASGI fixture."""
from uuid import NAMESPACE_URL, uuid5

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .demo_guard import DemoQuotaRow
from .providers import MockProvider
from .schemas import APIPlan, TaskCreate
from .security import redact
from .store import EventRow, StepRow, TaskRow
from .store_base import now
from .studio_models import ProjectRow, RunRow, TaskMetadataRow

SEED_PROJECT_ID = str(uuid5(NAMESPACE_URL, "autoqa/public/login-quality-v1"))
SEED_TASK_ID = str(uuid5(NAMESPACE_URL, "autoqa/public/authentication-regression-v1"))


async def seed_demo(store, settings, app):
    with Session(store.engine) as session:
        exists = session.get(TaskRow, SEED_TASK_ID) is not None
        has_tasks = session.scalar(select(TaskRow.id).limit(1)) is not None
    if exists:
        store.finalize(SEED_TASK_ID)
        return SEED_TASK_ID
    if has_tasks:
        return None
    generated = APIPlan.model_validate(await MockProvider().generate("", {}, APIPlan.model_json_schema()))
    steps = [{"action": "assert_api", "description": c.name, "scenario": c.scenario,
              "expected_status": c.expected_status, "method": c.method, "path": c.path} for c in generated.cases]
    plan = {"summary": generated.summary, "steps": steps, "cases": [c.model_dump() for c in generated.cases]}
    results = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.studio_url) as client:
        for step, case in zip(steps, generated.cases, strict=True):
            options = {"content": case.raw_body} if case.raw_body is not None else {"json": case.payload}
            response = await client.request(case.method, case.path, **options)
            body = response.json()
            ok = response.status_code == case.expected_status and (
                not case.expected_field or case.expected_field in body)
            results.append({"step": step, "ok": ok, "duration_ms": 0, "retryable": False,
                "error": None if ok else f"{case.name}: expected HTTP {case.expected_status}, received HTTP {response.status_code}",
                "evidence": {"source": "seed-asgi-fixture", "method": case.method, "path": case.path,
                             "expected_status": case.expected_status, "received_status": response.status_code,
                             "response": redact(body)}})
    request = TaskCreate(target_url=settings.studio_url + "/demo/api/login", project_id=SEED_PROJECT_ID,
                         title="Authentication Regression Test", goal="Validate six authentication contract scenarios",
                         test_type="api")
    failed = any(not r["ok"] for r in results)
    diagnosis = {"category": "api_contract_error", "summary": "Wrong password accepted by the controlled fixture",
                 "suggestion": "Reject invalid credentials with HTTP 401.", "root_cause_confirmed": False} if failed else None
    with Session(store.engine) as session:
        session.execute(update(DemoQuotaRow).where(DemoQuotaRow.id == "global").values(count=DemoQuotaRow.count))
        if session.get(TaskRow, SEED_TASK_ID):
            return SEED_TASK_ID
        session.add(ProjectRow(id=SEED_PROJECT_ID, name="Login API Quality Demo", url=request.target_url,
            builtin=True, description="Seeded MockProvider plan; six locally executed ASGI fixture requests, no LLM call."))
        session.flush()
        session.add(TaskRow(id=SEED_TASK_ID, request=request.model_dump(), plan=redact(plan), planner_mode="mock",
                            status="failed" if failed else "passed", diagnosis=diagnosis))
        session.flush()
        session.add(TaskMetadataRow(task_id=SEED_TASK_ID, project_id=SEED_PROJECT_ID,
                                   title=request.title, test_type="api"))
        session.add(RunRow(id=SEED_TASK_ID, task_id=SEED_TASK_ID, status="failed" if failed else "passed",
                           started_at=now(), finished_at=now()))
        store.save_plan(session, SEED_TASK_ID, plan)
        events = [("planner_started", {}), ("plan_created", {"plan": redact(plan), "mode": "mock", "source": "seed"}),
                  ("approval_required", {"scope": "plan"}), ("approval_answer", {"approved": True, "source": "seed"})]
        for index, result in enumerate(results):
            session.add(StepRow(task_id=SEED_TASK_ID, index=index, attempt=0, result=redact(result)))
            events.extend([("step_started", {"index": index}), ("step_finished", {"index": index, **result})])
        if diagnosis:
            events.append(("diagnosis", diagnosis))
        events.append(("completed", {"status": "failed" if failed else "passed"}))
        import json
        for kind, payload in events:
            session.add(EventRow(task_id=SEED_TASK_ID, kind=kind, payload=json.dumps(redact(payload))))
            store.track_workflow(session, SEED_TASK_ID, kind, payload)
        from .studio_models import CaseRow
        for case in session.scalars(select(CaseRow).where(CaseRow.task_id == SEED_TASK_ID)):
            case.status = "passed" if results[case.step_index]["ok"] else "failed"
        session.commit()
    store.finalize(SEED_TASK_ID)
    return SEED_TASK_ID
