"""Execution evidence redaction; comparison always happens before these helpers."""
import json
import re

from .crypto import scrub

SENSITIVE = re.compile(r"password|secret|token|authorization|cookie|api.?key", re.I)
VALUE_LIMIT = 4096


def secret_value(value):
    # Short/common fixture values must not replace substrings throughout unrelated evidence.
    return (isinstance(value, str) and len(value) >= 12 and not value.isdecimal()
            and len(set(value)) >= 4 and value.lower() not in {
                "password", "passwordpassword", "changeme", "redacted", "[redacted]", "undefined",
                "testtest", "testpassword", "not-a-secret", "abcdefghijkl"})


def sensitive_values(data):
    result = []
    def visit(value, sensitive=False):
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, sensitive or bool(SENSITIVE.search(key)))
        elif isinstance(value, list):
            for child in value:
                visit(child, sensitive)
        elif sensitive and secret_value(value):
            result.append(value)
            if value.lower().startswith("bearer ") and secret_value(value[7:]):
                result.append(value[7:])
    visit(data)
    return result


def config_secrets(config):
    hidden = sensitive_values(config.request_data.model_dump())
    for assertion in config.assertions:
        if assertion.operator == "equals" and SENSITIVE.search(assertion.path):
            value = assertion.expected
            hidden.extend(sensitive_values({"secret": value}))
    return hidden


def redact_assertions(results, hidden):
    clean = []
    for original in results:
        entry = dict(original)
        sensitive = bool(SENSITIVE.search(entry.get("path", "")))
        for key in ("expected", "actual"):
            if key not in entry:
                continue
            value = "[REDACTED]" if sensitive and (key == "actual" or entry.get("operator") == "equals") else scrub(entry[key], hidden)
            rendered = json.dumps(value, ensure_ascii=False)
            if len(rendered.encode()) > VALUE_LIMIT:
                value = rendered.encode()[:VALUE_LIMIT].decode("utf-8", errors="ignore")
                entry[key + "_truncated"] = True
            entry[key] = value
        # Paths are configuration metadata, never run value-based substitution over them.
        clean.append(entry)
    return clean


def public_config(config):
    value = config.model_dump()
    hidden = config_secrets(config)
    value["request_data"] = scrub(value["request_data"], hidden)
    value["assertions"] = redact_assertions(value["assertions"], hidden)
    return value


def safe_text(text, hidden):
    # Malformed JSON can still contain credential fragments; hide such bodies conservatively.
    keys = re.finditer(r'''(?<![\w-])["']?([\w-]+)["']?\s*[:=]''', text)
    if any(SENSITIVE.search(match[1]) for match in keys):
        return "[REDACTED BODY]"
    return scrub(text, hidden)
