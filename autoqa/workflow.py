import asyncio
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .browser import BrowserSession
from .diagnosis import diagnose, runtime_errors
from .planner import Planner
from .schemas import MUTATIONS, RETRYABLE, Step, TaskCreate
from .security import redact


class State(TypedDict, total=False):
    task_id: str
    request: dict
    plan: dict
    cursor: int
    attempt: int
    result: dict
    evidence: dict
    status: str
    denied: bool


class Workflow:
    def __init__(self, settings, store, policy, checkpointer, telemetry):
        self.settings, self.store, self.policy, self.telemetry = settings, store, policy, telemetry
        self.planner = Planner(settings, policy, telemetry)
        self.sessions: dict[str, BrowserSession] = {}
        graph = StateGraph(State)
        graph.add_node("test_planner", self.plan)
        graph.add_node("plan_approval", self.plan_approval)
        graph.add_node("step_approval", self.step_approval)
        graph.add_node("browser_agent", self.execute)
        graph.add_node("verifier", self.verify)
        graph.add_node("failure_analyzer", self.analyze)
        graph.add_node("finish", self.finish)
        graph.add_edge(START, "test_planner")
        graph.add_edge("test_planner", "plan_approval")
        graph.add_conditional_edges("plan_approval", lambda s: "finish" if s.get("denied") else "step_approval")
        graph.add_conditional_edges("step_approval", lambda s: "finish" if s.get("denied") else "browser_agent")
        graph.add_edge("browser_agent", "verifier")
        graph.add_conditional_edges("verifier", self.route)
        graph.add_edge("failure_analyzer", "finish")
        graph.add_edge("finish", END)
        self.graph = graph.compile(checkpointer=checkpointer)

    async def session(self, state: State):
        task_id = state["task_id"]
        if task_id not in self.sessions:
            # A page cannot be reconstructed faithfully after a mid-flow process restart.
            # Never replay prior clicks/submissions automatically.
            if state.get("cursor", 0) > 0:
                raise ValueError("Browser session lost after progress; inspect evidence and create a fresh task")
            browser = BrowserSession(self.settings, self.policy, task_id, state["request"]["step_timeout_ms"])
            if self.settings.mode == "production":
                browser.observation_only = not bool(state.get("plan"))
            self.sessions[task_id] = browser
            await browser.start(state["request"]["target_url"])
        return self.sessions[task_id]

    async def plan(self, state: State):
        task_id = state["task_id"]
        self.store.event(task_id, "planner_started", {"node": "Test Planner", "message": "读取页面并生成测试计划"})
        browser = await self.session(state)
        with self.telemetry.span("test-planner", {"task_id": task_id}):
            request_data = state["request"]
            if self.settings.mode == "production":
                request_data = {k: v for k, v in request_data.items() if k in TaskCreate.model_fields}
            plan, mode = await self.planner.plan(TaskCreate(**request_data), await browser.snapshot())
        self.store.update(task_id, plan=plan.model_dump(), planner_mode=mode)
        self.store.event(task_id, "plan_created", {"plan": plan.model_dump(), "mode": mode})
        return {"plan": plan.model_dump(), "cursor": 0, "attempt": 0, "denied": False}

    async def plan_approval(self, state: State):
        demo_auto = (state["request"]["auto_approve_demo"] and self.policy.is_demo(state["request"]["target_url"])
                     and all(self.policy.is_demo(s["value"]) for s in state["plan"]["steps"]
                             if s["action"] == "navigate"))
        if self.settings.mode == "production" or (not demo_auto and any(
                s["action"] in MUTATIONS for s in state["plan"]["steps"])):
            approved = interrupt(redact({"scope": "plan", "message": "此计划包含输入或点击，请审批完整计划",
                                         "plan": state["plan"]}))
            if approved is not True:
                return {"denied": True, "status": "cancelled"}
        return {"denied": False, "plan": self.store.get(state["task_id"], private=True)["plan"]}

    async def step_approval(self, state: State):
        step = state["plan"]["steps"][state["cursor"]]
        if step.get("approval_required"):
            approved = interrupt(redact({"scope": "step", "index": state["cursor"], "step": step,
                                         "message": "该步骤需要人工确认后执行"}))
            if approved is not True:
                return {"denied": True, "status": "cancelled"}
        return {"denied": False}

    async def execute(self, state: State):
        step = Step(**state["plan"]["steps"][state["cursor"]])
        task_id = state["task_id"]
        browser = await self.session(state)
        self.store.event(task_id, "step_started", {"node": "Browser Agent", "index": state["cursor"],
                                                  "attempt": state["attempt"], "step": step.model_dump()})
        with self.telemetry.span("browser-agent", {"task_id": task_id, "index": state["cursor"],
                                                   "step": step.model_dump()}):
            result = await browser.execute(step)
            captured = await browser.capture(state["cursor"], state["attempt"])
        result["artifacts"] = captured["artifacts"]
        return {"result": result, "evidence": captured["evidence"]}

    async def verify(self, state: State):
        result = state["result"].copy()
        with self.telemetry.span("verifier", {"task_id": state["task_id"], "index": state["cursor"]}):
            if result["ok"] and state["request"]["strict_runtime_errors"] and runtime_errors(state["evidence"]):
                result.update(ok=False, error="Runtime verification found Console/Network/origin errors",
                              retryable=False)
                if self.settings.mode == "production":
                    result["status"] = "system_error" if any(
                        entry.get("error") for entry in state["evidence"].get("network", [])) else "failed"
            self.store.step(state["task_id"], state["cursor"], state["attempt"], result)
            self.store.event(state["task_id"], "step_finished", {"node": "Verifier", "index": state["cursor"],
                                                                  "attempt": state["attempt"], **result})
        if result["ok"]:
            return {"cursor": state["cursor"] + 1, "attempt": 0, "result": result}
        if result["retryable"] and state["attempt"] < state["request"]["max_retries"]:
            self.store.event(state["task_id"], "retry", {"index": state["cursor"],
                                                       "message": "重试可安全重复的只读步骤"})
            await asyncio.sleep(0.2 * (state["attempt"] + 1))
            return {"attempt": state["attempt"] + 1, "result": result}
        return {"result": result, "status": "system_error" if result.get("status") == "system_error" else "failed"}

    def route(self, state: State):
        if state.get("status") in {"failed", "system_error"}:
            return "failure_analyzer"
        if state["cursor"] >= len(state["plan"]["steps"]):
            return "finish"
        if not state["result"]["ok"] and state["result"]["step"]["action"] in RETRYABLE:
            return "browser_agent"
        return "step_approval"

    async def analyze(self, state: State):
        with self.telemetry.span("failure-analyzer", {"task_id": state["task_id"]}):
            diagnosis = diagnose(state["result"], state["evidence"])
            browser = self.sessions.get(state["task_id"])
            if browser:
                diagnosis["mcp"] = await browser.mcp_capture()
            self.store.update(state["task_id"], diagnosis=diagnosis)
            self.store.event(state["task_id"], "diagnosis", {"node": "Failure Analyzer", **diagnosis})
        return {"status": state.get("status", "failed")}

    async def finish(self, state: State):
        status = state.get("status", "passed")
        await self.close_session(state["task_id"])
        self.store.update(state["task_id"], status=status, approval=None)
        self.store.event(state["task_id"], "completed", {"status": status, "message": "测试流程已结束"})
        return {"status": status}

    async def close_session(self, task_id: str):
        browser = self.sessions.pop(task_id, None)
        if browser:
            await browser.close()
