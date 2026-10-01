from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schemas import Step


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(Input):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=10, max_length=128, repr=False)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        value = value.strip().lower()
        if "@" not in value or " " in value:
            raise ValueError("Invalid email")
        return value


class ModelConfig(Input):
    provider: Literal["deepseek", "openai", "compatible"]
    base_url: str = Field(min_length=8, max_length=2000)
    api_key: str = Field(min_length=8, max_length=512, repr=False)
    model: str = Field(min_length=1, max_length=160)
    save_mode: Literal["session", "workspace"] = "workspace"


class ProductProject(Input):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    url: str = Field(min_length=8, max_length=2000)
    project_type: Literal["web", "api"] = "web"
    environment: str = Field(default="staging", min_length=1, max_length=80)


class EnvironmentConfig(Input):
    base_url: str = Field(min_length=8, max_length=2000)
    headers: dict[str, str] = Field(default_factory=dict, max_length=40)
    variables: dict[str, str] = Field(default_factory=dict, max_length=80)
    secrets: dict[str, str] = Field(default_factory=dict, max_length=40, repr=False)
    username: str = Field(default="", max_length=254, repr=False)
    password: str = Field(default="", max_length=512, repr=False)


class Endpoint(Input):
    path: str = Field(pattern=r"^/([^/?#][^?#]*)?$", max_length=1000)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    summary: str = Field(default="", max_length=1000)
    parameters: list[dict] = Field(default_factory=list, max_length=100)
    request_body: dict = Field(default_factory=dict)
    responses: dict = Field(default_factory=dict)


class ContractImport(Input):
    url: str | None = Field(default=None, max_length=2000)
    content: str | None = Field(default=None, max_length=1_000_000)
    endpoints: list[Endpoint] | None = Field(default=None, max_length=200)


class RealCase(Input):
    case_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    category: Literal["normal", "boundary", "invalid", "auth", "status", "schema"] = "normal"
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    path: str = Field(pattern=r"^/([^/?#][^?#]*)?$", max_length=1000)
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=80)
    headers: dict[str, str] = Field(default_factory=dict, max_length=40)
    payload: dict | list | None = None
    expected_status: int = Field(ge=100, le=599)
    response_schema: dict | None = None
    expected: str = Field(min_length=1, max_length=1000)


class RealPlan(Input):
    summary: str = Field(min_length=1, max_length=1000)
    scope: str = Field(min_length=1, max_length=2000)
    preconditions: list[str] = Field(default_factory=list, max_length=40)
    cases: list[RealCase] = Field(min_length=1, max_length=50)


class WebPlan(Input):
    summary: str = Field(min_length=1, max_length=1000)
    scope: str = Field(min_length=1, max_length=2000)
    preconditions: list[str] = Field(default_factory=list, max_length=40)
    steps: list[Step] = Field(min_length=1, max_length=50)


class RealTask(Input):
    project_id: str
    target_url: str | None = Field(default=None, max_length=2000)
    goal: str = Field(min_length=3, max_length=4000)
    title: str | None = Field(default=None, max_length=400)
    endpoints: list[str] = Field(default_factory=list, max_length=200)
    session_secrets: dict[str, str] = Field(default_factory=dict, max_length=40, repr=False)


class MemberInput(Input):
    email: str = Field(max_length=254)
    role: Literal["owner", "member"] = "member"
