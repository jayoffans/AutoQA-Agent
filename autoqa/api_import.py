"""Bounded, offline OpenAPI imports. Prance validates; SchemaPath resolves local objects.

Schema-level references stay intact beside the original document, including recursive
schemas. Importing never fetches a URL, executes an operation, or calls a provider.
"""
import json
from contextlib import contextmanager

import yaml
from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from jsonschema_path import SchemaPath
from prance import BaseParser
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .product_models import ApiEndpointRow, ApiSpecRow
from .studio_models import ProjectRow

MAX_UPLOAD_BYTES = 1_000_000
MAX_ENDPOINTS = 1000
METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace", "query"}
INVALID_SPEC = "无法解析接口规范；请上传有效的 OpenAPI 3.x 或 Swagger 2.0 文档"
router = APIRouter(prefix="/api/projects/{project_id}")


def unique_mapping(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("文档包含重复字段")
        result[key] = value
    return result


class UniqueSafeLoader(yaml.SafeLoader):
    """Keep safe YAML construction while rejecting ambiguous duplicate fields."""

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        return unique_mapping((self.construct_object(key, deep=deep), self.construct_object(value, deep=deep))
                              for key, value in node.value)


def check_document(document):
    # A schema resource id could change the base URI of an otherwise local $ref.
    # Reject it before invoking any library resolver/validator.
    pending, count = [(document, 0)], 0
    while pending:
        value, depth = pending.pop()
        count += 1
        if depth > 64 or count > 50_000:
            raise ValueError("文档结构过于复杂")
        if isinstance(value, dict):
            if "$ref" in value and (not isinstance(value["$ref"], str) or not value["$ref"].startswith("#/")):
                raise ValueError("仅支持文档内的 JSON Pointer 引用，请先打包外部引用")
            if "$id" in value or "$dynamicRef" in value:
                raise ValueError("请移除 schema 资源 ID 和动态引用后重试")
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)


def parse_spec(raw_content, format):
    try:
        if format == "json":
            document = json.loads(raw_content, object_pairs_hook=unique_mapping)
        else:
            if any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken))
                   for token in yaml.scan(raw_content)):
                raise ValueError("不支持 YAML 锚点和别名，请上传展开后的文档")
            document = yaml.load(raw_content, Loader=UniqueSafeLoader)
        if not isinstance(document, dict):
            raise ValueError(INVALID_SPEC)
        check_document(document)
        # JSON round-trip rejects unsafe/non-JSON YAML values and normalizes numeric
        # Swagger response keys. Prance does the actual OpenAPI parsing/validation.
        parser = BaseParser(spec_string=json.dumps(document, allow_nan=False),
                            backend="openapi-spec-validator", strict=True)
        document = parser.specification
        root = SchemaPath.from_dict(document, handlers={})

        def contents(node):
            with node.open() as value:
                return value

        name, version = document["info"]["title"], document["info"]["version"]
        if not name.strip() or len(name) > 255 or len(version) > 100:
            raise ValueError("规范名称或版本超出长度限制")
        swagger = document.get("swagger") == "2.0"
        endpoints, total_bytes = [], 0
        for path in document.get("paths", {}):
            if path.startswith("x-"):
                continue
            item_node = root / "paths" / path
            item = contents(item_node)
            for method in METHODS:
                if method not in item:
                    continue
                operation_node = item_node / method
                operation = contents(operation_node)
                # Operation parameters override matching path parameters per OAS.
                parameters = {}
                for node, value in ((item_node, item), (operation_node, operation)):
                    for index in range(len(value.get("parameters", []))):
                        parameter = contents(node / "parameters" / index)
                        parameters[(parameter["name"], parameter["in"])] = parameter
                values = list(parameters.values())
                body = contents(operation_node / "requestBody") if "requestBody" in operation else {}
                responses = {str(status): contents(operation_node / "responses" / status)
                             for status in operation.get("responses", {}) if not str(status).startswith("x-")}
                if swagger:
                    consumes = operation.get("consumes", document.get("consumes", ["application/json"]))
                    produces = operation.get("produces", document.get("produces", ["application/json"]))
                    body_parameter = next((p for p in values if p["in"] == "body"), None)
                    if body_parameter:
                        body = {"required": body_parameter.get("required", False),
                                "description": body_parameter.get("description", ""),
                                "content": {mime: {"schema": body_parameter["schema"]} for mime in consumes}}
                    form = [p for p in values if p["in"] == "formData"]
                    if form:
                        schema = {"type": "object", "properties": {p["name"]: {
                            k: v for k, v in p.items() if k not in {"name", "in", "required"}} for p in form},
                            "required": [p["name"] for p in form if p.get("required")]}
                        body = {"content": {mime: {"schema": schema} for mime in consumes}}
                    responses = {status: {**response, **({"content": {
                        mime: {"schema": response["schema"]} for mime in produces}} if "schema" in response else {})}
                        for status, response in responses.items()}
                endpoint = {"method": method.upper(), "path": path, "summary": operation.get("summary", ""),
                            "parameters": values, "request_schema": body, "response_schema": responses}
                if len(path) > 1000 or len(endpoint["summary"]) > 1000:
                    raise ValueError("接口路径或摘要超出长度限制")
                total_bytes += len(json.dumps(endpoint, ensure_ascii=False).encode("utf-8"))
                endpoints.append(endpoint)
                if len(endpoints) > MAX_ENDPOINTS or total_bytes > 4_000_000:
                    raise ValueError("解析结果过大，单份文档最多支持 1000 个接口和 4 MB 结构数据")
        if not endpoints:
            raise ValueError("文档中没有可导入的接口")
        return {"name": name, "version": version, "format": format, "raw_content": raw_content}, endpoints
    except ValueError as error:
        # Only our bounded messages may be shown; library errors can echo uploaded data.
        if str(error) in {INVALID_SPEC, "文档包含重复字段", "文档结构过于复杂",
                         "仅支持文档内的 JSON Pointer 引用，请先打包外部引用",
                         "请移除 schema 资源 ID 和动态引用后重试", "不支持 YAML 锚点和别名，请上传展开后的文档",
                         "规范名称或版本超出长度限制", "接口路径或摘要超出长度限制",
                         "解析结果过大，单份文档最多支持 1000 个接口和 4 MB 结构数据", "文档中没有可导入的接口"}:
            raise
        raise ValueError(INVALID_SPEC) from None
    except Exception:
        raise ValueError(INVALID_SPEC) from None


@contextmanager
def project_session(request, project_id):
    with Session(request.app.state.store.engine) as session:
        project = session.scalar(select(ProjectRow).where(ProjectRow.id == project_id,
            ProjectRow.workspace_id == request.state.workspace_id, ProjectRow.archived.is_(False)))
        if not project:
            raise HTTPException(404, "项目不存在")
        if project.project_type != "api":
            raise HTTPException(422, "请在 API 测试项目中导入接口文档")
        yield session


def scope(row, request, project_id):
    return (row.workspace_id == request.state.workspace_id, row.project_id == project_id)


def spec_info(row, endpoint_count):
    return {key: getattr(row, key) for key in ("id", "workspace_id", "project_id", "name", "version", "format",
                                              "created_at", "updated_at")} | {"endpoint_count": endpoint_count}


@router.post("/api-specs/upload", status_code=201)
async def upload_spec(project_id: str, request: Request, file: UploadFile = File(...)):
    try:
        with project_session(request, project_id):
            pass
        filename = file.filename or ""
        if "\\" in file.headers.get("content-disposition", "") or not filename or len(filename) > 255 or any(
                char in filename for char in ("/", "\\", ":")) or any(
                ord(char) < 32 or ord(char) == 127 for char in filename):
            raise HTTPException(422, "文件名不合法")
        extension = filename.rsplit(".", 1)[-1].lower()
        if extension not in {"json", "yaml", "yml"}:
            raise HTTPException(415, "仅支持 .json、.yaml 和 .yml 文件")
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "接口文档最大为 1 MB")
        try:
            raw = content.decode("utf-8-sig")
            spec, endpoints = await run_in_threadpool(parse_spec, raw, "json" if extension == "json" else "yaml")
        except UnicodeError:
            raise HTTPException(422, INVALID_SPEC) from None
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        with project_session(request, project_id) as session:
            row = ApiSpecRow(workspace_id=request.state.workspace_id, project_id=project_id, **spec)
            session.add(row)
            session.flush()
            session.add_all(ApiEndpointRow(workspace_id=row.workspace_id, project_id=project_id, spec_id=row.id,
                                           **endpoint) for endpoint in endpoints)
            session.commit()
            return {"spec": spec_info(row, len(endpoints)), "endpoint_count": len(endpoints)}
    finally:
        await file.close()


@router.get("/api-specs")
def list_specs(project_id: str, request: Request):
    with project_session(request, project_id) as session:
        counts = dict(session.execute(select(ApiEndpointRow.spec_id, func.count()).where(
            *scope(ApiEndpointRow, request, project_id)).group_by(ApiEndpointRow.spec_id)).all())
        return [spec_info(row, counts.get(row.id, 0)) for row in session.scalars(select(ApiSpecRow).where(
            *scope(ApiSpecRow, request, project_id)).order_by(ApiSpecRow.created_at, ApiSpecRow.id))]


@router.get("/api-endpoints")
def list_endpoints(project_id: str, request: Request):
    with project_session(request, project_id) as session:
        return [{key: getattr(row, key) for key in ("id", "workspace_id", "project_id", "spec_id", "method", "path",
                "summary", "parameters", "request_schema", "response_schema", "created_at")}
                for row in session.scalars(select(ApiEndpointRow).where(*scope(ApiEndpointRow, request, project_id))
                    .order_by(ApiEndpointRow.path, ApiEndpointRow.method, ApiEndpointRow.id))]


@router.delete("/api-specs/{spec_id}", status_code=204)
def delete_spec(project_id: str, spec_id: str, request: Request):
    with project_session(request, project_id) as session:
        row = session.scalar(select(ApiSpecRow).where(ApiSpecRow.id == spec_id, *scope(ApiSpecRow, request, project_id)))
        if not row:
            raise HTTPException(404, "接口文档不存在")
        session.execute(delete(ApiEndpointRow).where(ApiEndpointRow.spec_id == spec_id,
                                                     *scope(ApiEndpointRow, request, project_id)))
        session.delete(row)
        session.commit()
    return Response(status_code=204)
