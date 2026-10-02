"""Bounded, deterministic JSON assertions. This module never invokes a model."""
import json
import math
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

JSON_TYPES = {"string", "number", "integer", "boolean", "object", "array", "null"}


class ResponseComplexityError(ValueError):
    """Valid JSON exceeded the assertion engine's processing boundary."""


class AssertionBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["json"]
    path: str = Field(min_length=1, max_length=256)

    @field_validator("path")
    @classmethod
    def simple_path(cls, value):
        parts = value.split(".")
        if len(parts) > 32 or any(not re.fullmatch(r"[\w-]+", p) for p in parts):
            raise ValueError("Use a dotted field path with optional numeric array indices")
        return value


class ExistsAssertion(AssertionBase):
    operator: Literal["exists"]


class EqualsAssertion(AssertionBase):
    operator: Literal["equals"]
    expected: JsonValue

    @field_validator("expected")
    @classmethod
    def bounded_json(cls, value):
        json.dumps(value, allow_nan=False, ensure_ascii=False).encode("utf-8")
        def depth(item, level=0):
            if level > 32:
                raise ValueError("Expected JSON is too deeply nested")
            children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
            for child in children:
                depth(child, level + 1)
        depth(value)
        return value


class TypeAssertion(AssertionBase):
    operator: Literal["type"]
    expected: Literal["string", "number", "integer", "boolean", "object", "array", "null"]


JsonAssertion = Annotated[ExistsAssertion | EqualsAssertion | TypeAssertion, Field(discriminator="operator")]


def json_type(value):
    if value is None:
        return "null"
    return {bool: "boolean", int: "integer", float: "number", str: "string", dict: "object", list: "array"}[type(value)]


def json_equal(left, right):
    a, b = json_type(left), json_type(right)
    if a in {"integer", "number"} and b in {"integer", "number"}:
        return left == right
    if a != b:
        return False
    if a == "object":
        return left.keys() == right.keys() and all(json_equal(left[k], right[k]) for k in left)
    if a == "array":
        return len(left) == len(right) and all(json_equal(x, y) for x, y in zip(left, right))
    return left == right


def parse_json(body):
    def finite(value):
        raise ValueError("Non-finite JSON number")
    value = json.loads(body, parse_constant=finite)
    def validate(item, level=0):
        if level > 64:
            raise ResponseComplexityError("Response JSON exceeds supported depth")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Non-finite JSON number")
        if isinstance(item, str):
            item.encode("utf-8")
        if isinstance(item, dict):
            for key in item:
                key.encode("utf-8")
        children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
        for child in children:
            validate(child, level + 1)
    validate(value)
    return value


def lookup(value, path):
    for segment in path.split("."):
        if isinstance(value, dict) and segment in value:
            value = value[segment]
        elif isinstance(value, list) and segment.isascii() and segment.isdecimal() and int(segment) < len(value):
            value = value[int(segment)]
        else:
            return False, None
    return True, value


def assertion_results(expected_status, assertions, status=None, body=None):
    """All assertions produce evidence, including when HTTP never completed."""
    completed = status is not None
    good_status = completed and status == expected_status and not 300 <= status < 400
    results = [{"type": "http_status", "expected": expected_status, "actual": status,
                "status": "passed" if good_status else "failed" if completed else "not_evaluated",
                "reason_code": None if good_status else "redirect_blocked" if completed and 300 <= status < 400
                else "status_mismatch" if completed else "http_incomplete"}]
    valid, parsed = False, None
    if completed and assertions:
        try:
            parsed, valid = parse_json(body), True
        except ResponseComplexityError:
            raise
        except (ValueError, TypeError, RecursionError):
            pass
    for assertion in assertions:
        entry = assertion.model_dump()
        found, actual = lookup(parsed, assertion.path) if valid else (False, None)
        passed = valid and found and (assertion.operator == "exists" or (
            json_equal(actual, assertion.expected) if assertion.operator == "equals" else
            json_type(actual) == assertion.expected or assertion.expected == "number" and json_type(actual) == "integer"))
        entry.update(actual=actual, actual_exists=found,
                     status="passed" if passed else "failed" if completed else "not_evaluated",
                     reason_code=None if passed else "http_incomplete" if not completed else
                     "invalid_json" if not valid else "path_missing" if not found else "value_mismatch")
        results.append(entry)
    return results
