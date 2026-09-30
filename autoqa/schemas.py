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


class Approval(BaseModel):
    approved: bool


TERMINAL = {"passed", "failed", "cancelled", "interrupted"}
