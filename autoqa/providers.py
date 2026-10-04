"""Small JSON provider adapter; no executable model output or implicit mock fallback."""
import json
from typing import Protocol

import httpx

from .config import Settings


class ProviderError(ValueError):
    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.code = code


class LLMProvider(Protocol):
    async def generate(self, system: str, context: dict, schema: dict) -> dict: ...


class OpenAIProvider:
    def __init__(self, settings: Settings, transport=None):
        self.key, self.model = settings.provider_key, settings.model_name
        self.resolved_model = None
        self.base = settings.llm_base_url.rstrip("/")
        self.usage = None
        self.model_request_count = 0
        self.completed_model_requests = 0
        self.transport = transport

    async def generate(self, system: str, context: dict, schema: dict) -> dict:
        if not self.key or not self.model:
            raise ProviderError("LLM provider requires its API key and LLM_MODEL")
        payload = {"model": self.model, "temperature": 0, "max_tokens": 4096,
                   "messages": [{"role": "system", "content": system + " JSON schema: " + json.dumps(schema)},
                                {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
                   "response_format": {"type": "json_object"}}
        try:
            async with httpx.AsyncClient(timeout=45, trust_env=False, follow_redirects=False,
                                         **({"transport": self.transport} if self.transport else {})) as client:
                if self.model == "auto":
                    models = await client.get(self.base + "/models", headers={"Authorization": "Bearer " + self.key})
                    if models.is_error:
                        raise ProviderError(f"LLM model discovery returned HTTP {models.status_code}; set a valid LLM_MODEL")
                    available = {entry["id"] for entry in models.json()["data"]}
                    candidates = ["deepseek-chat", "deepseek-flash", "deepseek-v4-pro"]
                    self.model = next((name for name in candidates if name in available), "")
                    if not self.model:
                        raise ProviderError("No supported automatic model selection; set an explicit LLM_MODEL from your provider")
                    payload["model"] = self.model
                self.model_request_count += 1
                if self.transport:
                    from .target_network import fetch_limited
                    response = await fetch_limited(client, "POST", self.base + "/chat/completions",
                        headers={"Authorization": "Bearer " + self.key}, json=payload, max_bytes=1_000_000)
                else:
                    response = await client.post(self.base + "/chat/completions",
                                                 headers={"Authorization": "Bearer " + self.key}, json=payload)
                self.completed_model_requests += 1
                if response.is_error:
                    raise ProviderError(f"LLM provider returned HTTP {response.status_code}; check credentials/quota",
                                        code="provider_failure")
                data = response.json()
                self.resolved_model = data.get("model")
                self.usage = data.get("usage")
            content = data["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]
            value = json.loads(content)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except ProviderError:
            raise
        except httpx.TimeoutException:
            raise ProviderError("LLM provider timed out; verify endpoint/connectivity", code="provider_timeout") from None
        except httpx.HTTPError:
            raise ProviderError("LLM provider connection failed or timed out; verify endpoint/connectivity",
                                code="provider_failure") from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError("LLM provider returned invalid JSON; no test plan was executed", code="json_parse") from None


class DeepSeekProvider(OpenAIProvider):
    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.base = "https://api.deepseek.com"


class CompatibleProvider(OpenAIProvider):
    pass


class MockProvider:
    """Deterministic planning only. Executions still use the real browser/HTTP service."""
    async def generate(self, system: str, context: dict, schema: dict) -> dict:
        if schema.get("title") == "APIPlan":
            descriptions = ["Valid login", "Invalid password", "Missing username", "Missing password",
                            "Malformed payload", "Unauthorized request"]
            scenarios = ["valid_login", "invalid_password", "missing_username", "missing_password",
                         "malformed_payload", "unauthorized_request"]
            payloads = [{"username": "demo", "password": "autoqa123"},
                        {"username": "demo", "password": "incorrect"}, {"password": "autoqa123"},
                        {"username": "demo"}, None, None]
            return {"summary": "Login API contract validation (MockProvider plan)", "cases": [
                {"name": name, "scenario": scenario, "description": name,
                 "method": "GET" if index == 5 else "POST",
                 "path": "/demo/api/protected" if index == 5 else "/demo/api/login",
                 "payload": payloads[index], "raw_body": "{" if index == 4 else None,
                 "expected_status": [200, 401, 400, 400, 400, 401][index],
                 "expected_field": "access_token" if index == 0 else "error"}
                for index, (name, scenario) in enumerate(zip(descriptions, scenarios, strict=True))]}
        from .planner import demo_plan
        from .schemas import TaskCreate
        return demo_plan(TaskCreate(target_url=context["target_url"], goal=context["goal"])).model_dump()


def get_provider(settings: Settings) -> LLMProvider:
    if settings.mode == "production":
        raise ProviderError("Production providers must be resolved through the workspace BYOK gateway")
    return {"mock": MockProvider, "deepseek": DeepSeekProvider,
            "openai": OpenAIProvider, "compatible": OpenAIProvider}[settings.llm_provider](
                *([] if settings.llm_provider == "mock" else [settings]))
