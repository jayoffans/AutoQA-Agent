"""Fixed Planner diagnostics: trusted IDs, counts and provider numeric metadata only."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Category = Literal["positive", "negative", "boundary", "security"]
ErrorCode = Literal[
    "json_parse", "schema_validation", "unknown_endpoint", "duplicate_case",
    "missing_category_coverage", "endpoint_case_limit", "context_too_large",
    "provider_timeout", "provider_failure", "persistence_failure", "planner_timeout",
    "planner_failure", "cancelled",
]
ERROR_CODES = set(ErrorCode.__args__)
USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens",
                "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
Id = Annotated[str, StringConstraints(min_length=1, max_length=36, pattern=r"^[A-Za-z0-9-]+$")]
Count = Annotated[int, Field(strict=True, ge=0)]


def numeric_usage(value):
    if not isinstance(value, dict):
        return None
    result = {key: value[key] for key in USAGE_FIELDS
              if type(value.get(key)) is int and value[key] >= 0}
    return result or None


class EndpointCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    endpoint_id: Id
    case_count: Count
    category_counts: dict[Category, Count]
    missing_categories: list[Category]


class BatchDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    batch_index: int = Field(ge=1, le=10)
    endpoint_ids: list[Id] = Field(min_length=1, max_length=2)
    candidate_case_count: Count | None = None
    endpoints: list[EndpointCounts] = Field(default_factory=list, max_length=2)
    model_request_count: Count | None = 0
    completed_model_requests: Count | None = 0
    usage: dict[Literal["prompt_tokens", "completion_tokens", "total_tokens",
                        "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"], Count] | None = None


class PlannerDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    plan_id: Id | None = None
    batch_count: int = Field(ge=0, le=10)
    max_model_requests: int = Field(ge=0, le=10)
    case_limit: Literal[12] = 12
    persisted_case_count: Count = 0
    error_code: ErrorCode | None = None
    batches: list[BatchDiagnostics] = Field(default_factory=list, max_length=10)

    def public(self):
        result = self.model_dump()
        counts = [batch.model_request_count for batch in self.batches]
        completed = [batch.completed_model_requests for batch in self.batches]
        result["model_request_count"] = None if None in counts else sum(counts)
        result["completed_model_requests"] = None if None in completed else sum(completed)
        called = [batch for batch in self.batches if batch.model_request_count != 0]
        known = {key: sum(batch.usage[key] for batch in called if batch.usage and key in batch.usage)
                 for key in USAGE_FIELDS if any(batch.usage and key in batch.usage for batch in called)}
        result["known_usage"] = known or None
        result["usage_status"] = ("unknown" if not known else "known" if all(
            batch.usage and all(key in batch.usage for key in USAGE_FIELDS[:3]) for batch in called
        ) else "partial")
        return result
