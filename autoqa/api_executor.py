"""Deterministic HTTP execution of human-reviewed requests; no model or code evaluation."""
import asyncio
import json
import re
import time
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .crypto import scrub
from .product_models import TestExecution
from .security import origin, safe_url
from .target_network import PinnedTransport, fetch_limited, resolve_target

CASE_TIMEOUT = 10
MAX_RESPONSE_BYTES = 256_000
MAX_REQUEST_BYTES = 64_000
MAX_RUN_CASES = 240


class RequestData(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path_params: dict[str, Any] = Field(default_factory=dict, max_length=40)
    query: dict[str, Any] = Field(default_factory=dict, max_length=40)
    headers: dict[str, str] = Field(default_factory=dict, max_length=40)
    json_body: Any = None


class CaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_data: RequestData
    expected_status: int = Field(ge=100, le=599)


def default_value(schema, document, depth=0):
    if depth > 12:
        raise ValueError("Schema 递归或嵌套过深，请人工填写参数")
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            raise ValueError("仅支持本地 Schema 引用")
        resolved = document
        for part in ref[2:].split("/"):
            resolved = resolved[part.replace("~1", "/").replace("~0", "~")]
        return default_value(resolved, document, depth + 1)
    for key in ("default", "example", "const"):
        if key in schema:
            return schema[key]
    if schema.get("enum"):
        return schema["enum"][0]
    kind = schema.get("type", "object" if "properties" in schema else "string")
    if kind == "string":
        length = max(0, schema.get("minLength", min(1, schema.get("maxLength", 1))))
        if length > 1000:
            raise ValueError("字符串默认值超出限制")
        return "test" if length <= 4 and schema.get("maxLength", 4) >= 4 else "x" * length
    if kind == "integer":
        return max(schema.get("minimum", 1), 1) if schema.get("maximum", 1) >= 1 else schema["maximum"]
    if kind == "boolean":
        return True
    if kind == "array":
        count = max(1, schema.get("minItems", 1)) if schema.get("maxItems", 1) else 0
        if count > 40:
            raise ValueError("数组默认值超出限制")
        return [default_value(schema.get("items", {}), document, depth + 1) for _ in range(count)]
    if kind == "object":
        return {key: default_value(value, document, depth + 1) for key, value in schema.get("properties", {}).items()}
    raise ValueError("此 Schema 类型需要人工填写参数")


def default_config(endpoint, document):
    data = RequestData()
    for parameter in endpoint.parameters:
        location = parameter.get("in")
        target = {"path": data.path_params, "query": data.query, "header": data.headers}.get(location)
        if target is None:
            if location == "body":
                continue  # Swagger body is normalized in request_schema.
            raise ValueError("暂不支持 Cookie 或表单参数")
        value = default_value(parameter.get("schema", parameter), document)
        target[parameter["name"]] = str(value).lower() if isinstance(value, bool) and location == "header" else (
            str(value) if location == "header" else value)
    content = endpoint.request_schema.get("content", {})
    if content:
        media = next((key for key in content if key == "application/json" or key.endswith("+json")), None)
        if media is None:
            raise ValueError("暂仅支持 JSON 请求体")
        data.json_body = default_value(content[media].get("schema", {}), document)
        data.headers.setdefault("Content-Type", media)
    statuses = sorted(int(key) for key in endpoint.response_schema if key.isdigit() and 200 <= int(key) < 300)
    if not statuses:
        raise ValueError("请人工填写预期 HTTP 状态码")
    return CaseConfig(request_data=data, expected_status=statuses[0])


def validate_base_url(url):
    origin(url)
    parts = urlsplit(url)
    if parts.query or parts.fragment or any(c in url for c in "\\\r\n\t"):
        raise ValueError("Base URL 不得包含查询、片段或控制字符")
    resolve_target(url, allow_private=False)


def request_url(base_url, endpoint, data):
    origin(base_url)
    path = endpoint.path
    if not path.startswith("/") or path.startswith("//") or any(c in path for c in "\\?#") or any(
            ord(c) < 32 or ord(c) == 127 for c in path):
        raise ValueError("接口路径不合法")
    names = set(re.findall(r"\{([^{}]+)\}", path))
    if set(data.path_params) != names:
        raise ValueError("请填写全部路径参数")
    for name in names:
        value = data.path_params[name]
        if not isinstance(value, (str, int, bool)):
            raise ValueError("路径参数必须是标量")
        value = str(value)
        if not value or value in {".", ".."} or any(c in value for c in "/\\%?#") or any(
                ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("路径参数包含不安全字符")
        path = path.replace("{" + name + "}", quote(value, safe=""))
    decoded = unquote(path)
    if "{" in path or "}" in path or "%" in decoded or "\\" in decoded or any(
            ord(c) < 32 or ord(c) == 127 for c in decoded) or any(
            segment in {".", ".."} for segment in decoded.split("/")):
        raise ValueError("禁止路径穿越")
    url = base_url.rstrip("/") + path
    if origin(url) != origin(base_url) or len(url) > 4000:
        raise ValueError("请求必须留在已审核的 Base URL")
    return url


def validate_config(endpoint, config, base_url):
    if endpoint.method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
        raise ValueError("不支持该 HTTP 方法")
    data = config.request_data
    if len(json.dumps(config.model_dump(), allow_nan=False).encode()) > MAX_REQUEST_BYTES:
        raise ValueError("请求参数超出限制")
    for key, value in data.headers.items():
        if (not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) or any(ord(c) < 32 or ord(c) == 127 for c in value)
                or key.lower() in {"host", "content-length", "transfer-encoding", "connection", "upgrade", "te", "trailer"}
                or key.lower().startswith("proxy-")):
            raise ValueError("禁止覆盖路由或传输 Header")
    for value in data.query.values():
        values = value if isinstance(value, list) else [value]
        if any(not isinstance(v, (str, int, bool, float)) for v in values):
            raise ValueError("Query 参数必须是标量或数组")
    return request_url(base_url, endpoint, data)


def request_secrets(data):
    result = []
    def visit(value, sensitive=False):
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, sensitive or bool(re.search(r"password|secret|token|authorization|cookie|api.?key", key, re.I)))
        elif isinstance(value, list):
            for child in value:
                visit(child, sensitive)
        elif sensitive and isinstance(value, str) and value:
            result.append(value)
            if value.lower().startswith("bearer "):
                result.append(value[7:])
    visit(data)
    return result


class ApiExecutor:
    def __init__(self):
        self.transport_factory = lambda: PinnedTransport(allow_private=False)

    async def execute(self, case, endpoint, base_url, config, run_id, secrets_to_hide=()):
        started = time.monotonic()
        data = config.request_data.model_dump()
        hidden = [*secrets_to_hide, *request_secrets(data)]
        result = TestExecution(workspace_id=case.workspace_id, plan_id=case.plan_id, run_id=run_id,
            case_id=case.id, method=endpoint.method, url=safe_url(base_url), request_data=scrub(data, hidden),
            response_status=None, response_body=None, passed=False, error_message=None, duration_ms=0)
        try:
            url = validate_config(endpoint, config, base_url)
            result.url = scrub(safe_url(url), hidden)
            # Revalidate every request, then pin again at connection time. Never honor the private-target setting.
            async with asyncio.timeout(CASE_TIMEOUT):
                await asyncio.to_thread(validate_base_url, base_url)
                await asyncio.to_thread(resolve_target, url, False)
                async with httpx.AsyncClient(transport=self.transport_factory(), timeout=CASE_TIMEOUT,
                        follow_redirects=False, trust_env=False) as client:
                    response = await fetch_limited(client, endpoint.method, url, max_bytes=MAX_RESPONSE_BYTES,
                        params=config.request_data.query, headers=config.request_data.headers,
                        **({"json": config.request_data.json_body} if config.request_data.json_body is not None else {}))
            result.response_status = response.status_code
            try:
                result.response_body = json.dumps(scrub(response.json(), hidden), ensure_ascii=False)
            except ValueError:
                result.response_body = scrub(response.text, hidden)
            result.passed = response.status_code == config.expected_status and not 300 <= response.status_code < 400
            if not result.passed:
                result.error_message = ("禁止跟随重定向" if 300 <= response.status_code < 400 else
                                        f"预期 HTTP {config.expected_status}，收到 HTTP {response.status_code}")
        except (TimeoutError, httpx.TimeoutException):
            result.error_message = "HTTP 请求超时"
        except httpx.HTTPError:
            result.error_message = "HTTP 请求失败"
        except (ValueError, KeyError, TypeError):
            result.error_message = "请求被安全策略拒绝或响应超出限制"
        result.duration_ms = max(0, int((time.monotonic() - started) * 1000))
        return result
