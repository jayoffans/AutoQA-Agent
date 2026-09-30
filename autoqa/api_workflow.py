"""Login API workflow shares Store/Runner/approval APIs; browser graph is unchanged."""
import json
import time
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import ValidationError

from .providers import ProviderError, get_provider
from .schemas import APIPlan
from .security import redact


class APIWorkflow:
    def __init__(self, settings, store, policy, demo_app=None):
        self.settings, self.store, self.policy = settings, store, policy
        self.demo_app = demo_app

    async def run(self, task_id, resume):
        task = self.store.get(task_id, private=True)
        if resume is None:
            self.store.event(task_id, "planner_started", {"node": "Test Planner", "message": "Generate API contract cases"})
            if not self.settings.model_enabled:
                raise ProviderError("Login API planning requires LLM_PROVIDER=mock or a configured real provider")
            context = {"goal": task["request"]["goal"], "target_url": task["request"]["target_url"],
                "contract": {"POST /demo/api/login": "JSON username/password; valid=200, wrong password=401, missing fields=400, malformed JSON=400",
                             "GET /demo/api/protected": "without authorization=401"},
                "fixtures": {"username": "demo", "password": "autoqa123", "invalid_password": "incorrect"}}
            system = ("Generate exactly six login API test cases from the provided contract and fixture values. "
                      "Return JSON only. Include valid_login, invalid_password, missing_username, missing_password, "
                      "malformed_payload (raw_body is invalid JSON), unauthorized_request. Use only the given paths. "
                      "Each expected_status must follow the contract, never observed implementation behavior. "
                      "Valid response contains access_token; all error responses contain error. No shell/scripts.")
            provider = get_provider(self.settings)
            data = await provider.generate(system, context, APIPlan.model_json_schema())
            try:
                generated = APIPlan.model_validate(data)
            except ValidationError:
                raise ProviderError("LLM API plan failed six-scenario contract/schema validation; nothing was executed") from None
            steps = [{"action": "assert_api", "description": case.name, "scenario": case.scenario,
                      "expected_status": case.expected_status, "method": case.method, "path": case.path}
                     for case in generated.cases]
            if len(steps) > task["request"]["max_steps"]:
                raise ProviderError("Generated API plan exceeds max_steps; nothing was executed")
            plan = {"summary": generated.summary, "steps": steps,
                    "cases": [case.model_dump() for case in generated.cases]}
            self.store.update(task_id, plan=plan, planner_mode="mock" if self.settings.llm_provider == "mock" else "llm")
            self.store.event(task_id, "plan_created", {"plan": plan, "mode": self.settings.llm_provider,
                                                      "model": getattr(provider, "model", "mock")})
            approval = {"scope": "plan", "message": "Review the six API requests and assertions before execution", "plan": plan}
            self.store.update(task_id, status="awaiting_approval", approval=redact(approval))
            self.store.event(task_id, "approval_required", approval)
            return
        if not resume:
            self.store.update(task_id, status="cancelled", approval=None)
            self.store.event(task_id, "completed", {"status": "cancelled"})
            return
        if not task["plan"]:
            raise ValueError("Saved API plan is missing; create a fresh task")
        cases = APIPlan.model_validate({"summary": task["plan"]["summary"], "cases": task["plan"]["cases"]}).cases
        parts = urlsplit(task["request"]["target_url"])
        failed = []
        # No retries for POST, and no redirect following outside the configured policy.
        options = {"transport": httpx.ASGITransport(app=self.demo_app)} if self.demo_app else {}
        async with httpx.AsyncClient(timeout=task["request"]["step_timeout_ms"] / 1000,
                                     follow_redirects=False, trust_env=False, **options) as client:
            for index, case in enumerate(cases):
                url = urlunsplit((parts.scheme, parts.netloc, case.path, "", ""))
                self.policy.validate(url)
                step = task["plan"]["steps"][index]
                self.store.event(task_id, "step_started", {"node": "API Executor", "index": index, "attempt": 0,
                                                          "step": step, "request": case.model_dump()})
                start = time.monotonic()
                response_data = None
                received_status = None
                try:
                    options = ({"content": case.raw_body, "headers": {"Content-Type": "application/json"}}
                               if case.raw_body is not None else {"json": case.payload} if case.payload is not None else {})
                    response = await client.request(case.method, url, **options)
                    received_status = response.status_code
                    try:
                        response_data = response.json()
                    except ValueError:
                        response_data = {"response_type": "non-JSON", "length": len(response.content)}
                    ok = received_status == case.expected_status and (
                        not case.expected_field or isinstance(response_data, dict) and case.expected_field in response_data)
                    error = None if ok else f"{case.name}: expected HTTP {case.expected_status}, received HTTP {received_status}; verify response contract"
                except httpx.HTTPError:
                    ok, error = False, f"{case.name}: HTTP request failed or timed out"
                evidence = redact({"source": "local-asgi-fixture" if self.demo_app else "http-service",
                    "method": case.method, "path": case.path, "request": case.payload,
                    "raw_body": case.raw_body, "expected_status": case.expected_status,
                    "received_status": received_status, "response": response_data})
                directory = self.settings.artifact_dir / task_id
                directory.mkdir(parents=True, exist_ok=True)
                filename = f"api-case-{index + 1}.json"
                (directory / filename).write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
                result = {"step": step, "ok": ok, "error": error, "retryable": False,
                          "duration_ms": int((time.monotonic() - start) * 1000), "evidence": evidence,
                          "artifacts": {"response": filename}}
                self.store.step(task_id, index, 0, result)
                self.store.event(task_id, "step_finished", {"node": "Assertion", "index": index, "attempt": 0, **result})
                if not ok:
                    failed.append(case.name)
        if failed:
            diagnosis = {"category": "api_contract_error", "summary": f"{len(failed)} API cases violated the contract",
                         "suggestion": "Verify authentication and input validation; compare expected/received status in HTTP evidence.",
                         "root_cause_confirmed": False}
            self.store.update(task_id, diagnosis=diagnosis)
            self.store.event(task_id, "diagnosis", diagnosis)
        status = "failed" if failed else "passed"
        self.store.update(task_id, status=status, approval=None)
        self.store.event(task_id, "completed", {"status": status, "message": "API run and persistent report completed"})
