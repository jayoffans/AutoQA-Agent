import re
from urllib.parse import urlsplit, urlunsplit

from .config import Settings


def origin(url: str) -> str:
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("Only HTTP(S) URLs without embedded credentials are supported")
    port = p.port
    host = f"[{p.hostname}]" if ":" in p.hostname else p.hostname
    suffix = f":{port}" if port and port != {"http": 80, "https": 443}[p.scheme] else ""
    return f"{p.scheme}://{host}{suffix}"


class TargetPolicy:
    def __init__(self, settings: Settings):
        self.allowed = {origin(v.strip()) for v in settings.allowed_origins.split(",") if v.strip()}
        self.resources = self.allowed | {
            origin(v.strip()) for v in settings.resource_origins.split(",") if v.strip()
        }
        self.studio_origins = {origin(settings.studio_url), f"http://127.0.0.1:{settings.port}",
                               f"http://localhost:{settings.port}"}
        self.allowed |= self.studio_origins
        self.resources |= self.studio_origins

    def validate(self, url: str, resource: bool = False):
        allowed = self.resources if resource else self.allowed
        if origin(url) not in allowed:
            raise ValueError("Target origin is not allowed. Add it to AUTOQA_ALLOWED_ORIGINS first.")
        p = urlsplit(url)
        if origin(url) in self.studio_origins:
            if not p.path.startswith("/demo/"):
                raise ValueError("The built-in AutoQA origin only permits /demo/ targets")

    def is_demo(self, url: str) -> bool:
        p = urlsplit(url)
        return origin(url) in self.studio_origins and p.path.startswith("/demo/")


def safe_url(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def redact(value, key: str = ""):
    if re.search(r"password|secret|token|authorization|cookie|api.?key", key, re.I):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if k == "value" and value.get("action") == "fill"
                    else redact(v, k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)(bearer\s+)[\w.\-]+", r"\1[REDACTED]", value)
        value = re.sub(r"(?i)((?:token|password|secret|api_key)=)[^&\s]+", r"\1[REDACTED]", value)
        return re.sub(r"(\w+(?:\+\w+)?://[^\s/:]+:)[^@\s]+(@)", r"\1[REDACTED]\2", value)
    return value
