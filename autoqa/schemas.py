from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Action = Literal[
    "navigate", "click", "fill", "select", "press", "wait", "assert_text",
    "assert_visible", "assert_url", "screenshot",
]
MUTATIONS = {"click", "fill", "select", "press"}
RETRYABLE = {"navigate", "wait", "assert_text", "assert_visible", "assert_url", "screenshot"}


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Action
    description: str = Field(min_length=1, max_length=400)
    selector: str | None = Field(default=None, max_length=500)
    value: str | None = Field(default=None, max_length=2000)
    approval_required: bool = False

    @model_validator(mode="after")
    def check_fields(self):
        if self.action in {"click", "fill", "select", "press", "assert_visible"} and not self.selector:
            raise ValueError(f"{self.action} requires selector")
        if self.action in {"navigate", "fill", "select", "press", "assert_text", "assert_url"} and not self.value:
            raise ValueError(f"{self.action} requires value")
        return self


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=1000)
    steps: list[Step] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def require_assertion(self):
        if not any(s.action.startswith("assert_") for s in self.steps):
            raise ValueError("A test plan must contain an explicit assertion")
        return self


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_url: str = Field(min_length=8, max_length=2000)
    goal: str = Field(min_length=3, max_length=4000)
    steps: list[Step] | None = Field(default=None, min_length=1, max_length=50)
    auto_approve_demo: bool = False
    max_steps: int = Field(default=25, ge=1, le=50)
    max_retries: int = Field(default=1, ge=0, le=3)
    step_timeout_ms: int = Field(default=8000, ge=500, le=30000)
    task_timeout_seconds: int = Field(default=180, ge=5, le=600)
    strict_runtime_errors: bool = True
    project_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    title: str | None = Field(default=None, min_length=1, max_length=400)
    test_type: Literal["regression", "smoke", "api"] = "regression"


class Approval(BaseModel):
    approved: bool


TERMINAL = {"passed", "failed", "cancelled", "interrupted"}


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    url: str = Field(min_length=8, max_length=2000)


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    url: str | None = Field(default=None, min_length=8, max_length=2000)


Scenario = Literal["valid_login", "invalid_password", "missing_username", "missing_password",
                   "malformed_payload", "unauthorized_request"]


class APICase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    scenario: Scenario
    description: str = Field(min_length=1, max_length=400)
    method: Literal["GET", "POST"] = "POST"
    path: Literal["/demo/api/login", "/demo/api/protected"] = "/demo/api/login"
    payload: dict | None = None
    raw_body: str | None = Field(default=None, max_length=2000)
    expected_status: int = Field(ge=100, le=599)
    expected_field: Literal["access_token", "error"] | None = None


class APIPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=1000)
    cases: list[APICase] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def contract(self):
        expected = {"valid_login": 200, "invalid_password": 401, "missing_username": 400,
                    "missing_password": 400, "malformed_payload": 400, "unauthorized_request": 401}
        if {case.scenario for case in self.cases} != set(expected):
            raise ValueError("Login plan must cover all six contract scenarios exactly once")
        for case in self.cases:
            if case.expected_status != expected[case.scenario]:
                raise ValueError("Expected status must follow the supplied login API contract")
            if case.scenario == "unauthorized_request":
                if case.method != "GET" or case.path != "/demo/api/protected":
                    raise ValueError("Unauthorized scenario must call the protected endpoint")
            elif case.method != "POST" or case.path != "/demo/api/login":
                raise ValueError("Login scenarios must POST to the login endpoint")
            body = case.payload or {}
            if case.scenario == "valid_login" and body != {"username": "demo", "password": "autoqa123"}:
                raise ValueError("Valid login must use supplied fixtures")
            if case.scenario == "invalid_password" and (body.get("username") != "demo" or
                                                       body.get("password") != "incorrect"):
                raise ValueError("Invalid password must use supplied negative fixture")
            if case.scenario == "missing_username" and (body.get("username") or not body.get("password")):
                raise ValueError("Missing username scenario must omit username only")
            if case.scenario == "missing_password" and (body.get("password") or not body.get("username")):
                raise ValueError("Missing password scenario must omit password only")
            if case.scenario == "malformed_payload":
                import json
                try:
                    json.loads(case.raw_body or "null")
                except ValueError:
                    pass
                else:
                    raise ValueError("Malformed payload must contain invalid JSON")
        return self
