"""Parse OpenAPI 3.x and Swagger 2 local references without fetching remote $refs."""
import json
import re

import yaml

from .product_schemas import Endpoint


def parse_contract(content):
    try:
        document = json.loads(content)
    except ValueError:
        # Limit alias expansion before loading; inputs are untrusted.
        if re.search(r"(?:^|\s)[&*][A-Za-z0-9_-]+", content):
            raise ValueError("YAML anchors/aliases are not supported")
        try:
            document = yaml.safe_load(content)
        except yaml.YAMLError:
            raise ValueError("Invalid YAML document") from None
    if not isinstance(document, dict) or not (str(document.get("openapi", "")).startswith("3.") or
                                               document.get("swagger") == "2.0"):
        raise ValueError("Provide an OpenAPI 3.x or Swagger 2.0 document")

    def resolve(value, depth=0, chain=()):
        if depth > 30:
            raise ValueError("Schema reference/depth exceeds the supported limit")
        if isinstance(value, list):
            return [resolve(v, depth + 1, chain) for v in value]
        if isinstance(value, dict):
            if "$ref" in value:
                ref = value["$ref"]
                if not isinstance(ref, str) or not ref.startswith("#/"):
                    raise ValueError("Remote references are disabled; upload a bundled specification")
                if ref in chain:
                    raise ValueError("Recursive schema references require a bounded bundled schema")
                target = document
                try:
                    for part in ref[2:].split("/"):
                        target = target[part.replace("~1", "/").replace("~0", "~")]
                except (KeyError, TypeError):
                    raise ValueError("Invalid local schema reference") from None
                return resolve(target, depth + 1, (*chain, ref))
            return {k: resolve(v, depth + 1, chain) for k, v in value.items()}
        return value

    endpoints = []
    for path, raw in document.get("paths", {}).items():
        item = resolve(raw)
        for method, operation in item.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                continue
            parameters = item.get("parameters", []) + operation.get("parameters", [])
            body = operation.get("requestBody", {})
            if document.get("swagger"):
                body = next(({"content": {"application/json": {"schema": p.get("schema", {})}}}
                             for p in parameters if p.get("in") == "body"), {})
                responses = {k: {**v, "content": {"application/json": {"schema": v.get("schema", {})}}}
                             for k, v in operation.get("responses", {}).items()}
            else:
                responses = operation.get("responses", {})
            endpoints.append(Endpoint(path=path, method=method.upper(), summary=operation.get("summary", ""),
                                      parameters=parameters, request_body=body, responses=responses).model_dump())
            if len(endpoints) > 200:
                raise ValueError("Select a specification with at most 200 endpoints")
    if not endpoints:
        raise ValueError("No supported API operations found")
    return endpoints
