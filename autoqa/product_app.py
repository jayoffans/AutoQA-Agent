"""Production HTTP surface. All business access passes a membership and ownership check."""
import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import timedelta, timezone
from pathlib import Path
from uuid import uuid4
from weakref import WeakValueDictionary

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .checkpoints import SQLCheckpointSaver
from .contracts import parse_contract
from .crypto import SecretBox, check_password, hash_password, scrub, token_hash
from .demo_guard import source_address
from .gateway import ProviderGateway
from .product_models import (
    ContractRow,
    EnvironmentRow,
    MemberRow,
    ProviderConfigRow,
    SessionRow,
    UserRow,
    WorkspaceRow,
)
from .product_runner import ProductRunner, validate_plan
from .product_schemas import (
    ContractImport,
    Credentials,
    EnvironmentConfig,
    MemberInput,
    ModelConfig,
    ProductProject,
    RealTask,
)
from .providers import ProviderError
from .reports import report_html, report_metrics
from .schemas import TERMINAL, Approval, TaskCreate
from .security import origin
from .store import Store, TaskRow
from .store_base import now
from .studio_models import CaseRow, FindingRow, ProjectRow, RunRow, TaskMetadataRow
from .target_network import PublicPolicy, fetch_limited, resolve_target

ROOT = Path(__file__).parent


def create_product_app(settings):
    box = SecretBox(settings.secret_key)

    @asynccontextmanager
    async def lifespan(app):
        settings.artifact_dir.mkdir(parents=True, exist_ok=True)
        store = Store(settings.database_url, settings.artifact_dir, mode="production")
        store.recover()
        app.state.store = store
        app.state.gateway = ProviderGateway(store.engine, box, settings)
        app.state.runner = ProductRunner(settings, store, app.state.gateway, SQLCheckpointSaver(store.engine), box)
        try:
            yield
        finally:
            await app.state.runner.shutdown()
            app.state.gateway.session_configs.clear()
            store.engine.dispose()

    app = FastAPI(title="AutoQA Studio", version="2.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    attempts = defaultdict(deque)
    task_locks = WeakValueDictionary()

    async def review_lock(task_id: str):
        lock = task_locks.get(task_id)
        if lock is None:
            lock = asyncio.Lock()
            task_locks[task_id] = lock
        async with lock:
            yield

    def plan_hash(plan):
        return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def csrf(raw):
        return hmac.new(settings.secret_key.encode(), raw.encode(), hashlib.sha256).hexdigest()

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse({"detail": "请求格式不正确，请检查必填字段"}, status_code=422)

    @app.middleware("http")
    async def boundary(request, call_next):
        try:
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                sent_origin = request.headers.get("origin")
                if sent_origin and origin(sent_origin) not in {origin(str(request.url)), origin(settings.studio_url)}:
                    return JSONResponse({"detail": "跨站请求被拒绝"}, status_code=403)
                if request.headers.get("sec-fetch-site") == "cross-site":
                    return JSONResponse({"detail": "跨站请求被拒绝"}, status_code=403)
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 1_200_000:
                        return JSONResponse({"detail": "请求过大"}, status_code=413)
                request._body = bytes(body)
            if request.url.path.startswith("/api/") and request.url.path not in {
                    "/api/health", "/api/auth/register", "/api/auth/login"}:
                raw = request.cookies.get("autoqa_session", "")
                if not re.fullmatch(r"[a-f0-9]{64}", raw):
                    return JSONResponse({"detail": "请先登录"}, status_code=401)
                hashed = token_hash(raw)
                with Session(app.state.store.engine) as session:
                    saved = session.get(SessionRow, hashed)
                    if not saved or saved.expires_at.replace(tzinfo=timezone.utc) <= now():
                        # Expired sessions lose all volatile credentials as well.
                        for key in list(app.state.gateway.session_configs):
                            if key[0] == hashed:
                                app.state.gateway.session_configs.pop(key, None)
                        return JSONResponse({"detail": "登录已过期"}, status_code=401)
                    user = session.get(UserRow, saved.user_id)
                    members = list(session.scalars(select(MemberRow).where(MemberRow.user_id == user.id)))
                    workspace = request.headers.get("x-workspace-id") or (members[0].workspace_id if members else None)
                    member = next((m for m in members if m.workspace_id == workspace), None)
                    if not member:
                        return JSONResponse({"detail": "工作区不存在"}, status_code=404)
                    request.state.user_id, request.state.workspace_id = user.id, workspace
                    request.state.email, request.state.role = user.email, member.role
                    request.state.session_hash, request.state.csrf = hashed, csrf(raw)
                if request.method not in {"GET", "HEAD", "OPTIONS"} and not hmac.compare_digest(
                        request.headers.get("x-csrf-token", ""), csrf(raw)):
                    return JSONResponse({"detail": "请刷新页面后重试"}, status_code=403)
            response = await call_next(request)
        except Exception:
            # Never interpolate request bodies, headers, URLs, credentials or exception messages.
            response = JSONResponse({"detail": "服务暂时不可用，请稍后重试"}, status_code=500)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                                    "style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; "
                                    "connect-src 'self'; frame-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        return response

    def rate_limit(request):
        key = source_address(request, settings)
        queue = attempts[key]
        instant = time.monotonic()
        while queue and queue[0] < instant - 60:
            queue.popleft()
        if len(queue) >= 10:
            raise HTTPException(429, "尝试过于频繁，请稍后重试")
        queue.append(instant)

    def owner(request: Request):
        if request.state.role != "owner":
            raise HTTPException(403, "仅工作区所有者可修改此配置")

    def project(project_id, request):
        with Session(app.state.store.engine) as session:
            row = session.scalar(select(ProjectRow).where(ProjectRow.id == project_id,
                ProjectRow.workspace_id == request.state.workspace_id, ProjectRow.archived.is_(False)))
            if not row:
                raise HTTPException(404, "项目不存在")
            return app.state.store.project_dict(row)

    def task(task_id, request):
        with Session(app.state.store.engine) as session:
            owned = session.scalar(select(TaskRow.id).join(TaskMetadataRow, TaskMetadataRow.task_id == TaskRow.id)
                .join(ProjectRow, ProjectRow.id == TaskMetadataRow.project_id).where(
                    TaskRow.id == task_id, ProjectRow.workspace_id == request.state.workspace_id))
            if not owned:
                raise HTTPException(404, "任务不存在")
        return app.state.store.get(task_id, private=True)

    def public_task(row):
        result = scrub(row, [])
        # Validated plans contain only placeholders for secrets and remain editable.
        result["plan"] = row["plan"]
        result["metrics"] = report_metrics(row)
        result["plan_hash"] = plan_hash(row["plan"])
        result["plan_status"] = "approved" if row["status"] in TERMINAL else "generated"
        return result

    def target(url):
        try:
            resolve_target(url, settings.allow_private_targets)
            if urlsplit_query(url):
                raise ValueError("Use headers/environment variables instead of URL credentials or query strings")
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.get("/health")
    def health_status():
        return {"status": "ok"}

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "2.0.0", "mode": "production", "demo_mode": False,
                "authentication_required": True, "model_enabled": False, "byok_required": True}

    def sign_in(response, user_id):
        raw = secrets.token_hex(32)
        with Session(app.state.store.engine) as session:
            session.add(SessionRow(token_hash=token_hash(raw), user_id=user_id, expires_at=now() + timedelta(hours=12)))
            session.commit()
        response.set_cookie("autoqa_session", raw, httponly=True,
            secure=settings.env == "production" or settings.studio_url.startswith("https://"), samesite="strict", max_age=43200)
        return {"csrf_token": csrf(raw)}

    @app.post("/api/auth/register", status_code=201)
    def register(data: Credentials, response: Response, request: Request):
        rate_limit(request)
        user_id, workspace_id = str(uuid4()), str(uuid4())
        try:
            with Session(app.state.store.engine) as session:
                session.add(UserRow(id=user_id, email=data.email, password_hash=hash_password(data.password)))
                session.add(WorkspaceRow(id=workspace_id, name="个人工作区"))
                session.flush()
                session.add(MemberRow(workspace_id=workspace_id, user_id=user_id, role="owner"))
                session.commit()
        except IntegrityError:
            raise HTTPException(409, "无法注册此账号，请尝试登录") from None
        return sign_in(response, user_id)

    @app.post("/api/auth/login")
    def login(data: Credentials, response: Response, request: Request):
        rate_limit(request)
        with Session(app.state.store.engine) as session:
            user = session.scalar(select(UserRow).where(UserRow.email == data.email))
            hashed = user.password_hash if user else hash_password("dummy-password")
            valid = check_password(data.password, hashed)
            if not user or not valid:
                raise HTTPException(401, "邮箱或密码不正确")
            return sign_in(response, user.id)

    @app.get("/api/auth/me")
    def me(request: Request):
        with Session(app.state.store.engine) as session:
            workspaces = [{"id": w.id, "name": w.name, "role": m.role} for w, m in session.execute(
                select(WorkspaceRow, MemberRow).join(MemberRow).where(MemberRow.user_id == request.state.user_id))]
        return {"user_id": request.state.user_id, "email": request.state.email, "workspaces": workspaces,
                "workspace_id": request.state.workspace_id, "csrf_token": request.state.csrf}

    @app.post("/api/auth/logout", status_code=204)
    def logout(request: Request, response: Response):
        with Session(app.state.store.engine) as session:
            session.delete(session.get(SessionRow, request.state.session_hash))
            session.commit()
        for key in list(app.state.gateway.session_configs):
            if key[0] == request.state.session_hash:
                del app.state.gateway.session_configs[key]
        response.delete_cookie("autoqa_session")

    @app.post("/api/workspace/members", dependencies=[Depends(owner)], status_code=201)
    def add_member(data: MemberInput, request: Request):
        with Session(app.state.store.engine) as session:
            user = session.scalar(select(UserRow).where(UserRow.email == data.email.strip().lower()))
            if not user:
                raise HTTPException(404, "用户需先注册")
            existing = session.get(MemberRow, (request.state.workspace_id, user.id))
            if existing:
                raise HTTPException(409, "用户已加入工作区")
            session.add(MemberRow(workspace_id=request.state.workspace_id, user_id=user.id, role=data.role))
            session.commit()
        return {"role": data.role}

    @app.get("/api/provider")
    def provider(request: Request):
        return app.state.gateway.public_config(request.state.workspace_id, request.state.session_hash)

    def model_target(data):
        target(data.base_url)
        if not settings.allow_private_targets and not data.base_url.startswith("https://"):
            raise HTTPException(422, "模型服务必须使用 HTTPS")

    @app.put("/api/provider", dependencies=[Depends(owner)])
    def save_provider(data: ModelConfig, request: Request):
        model_target(data)
        return app.state.gateway.save(request.state.workspace_id, request.state.session_hash, data)

    @app.post("/api/provider/test", dependencies=[Depends(owner)])
    async def test_provider(data: ModelConfig, request: Request):
        model_target(data)
        config = {"provider": data.provider, "model": data.model, "base_url": data.base_url,
                  "encrypted_api_key": box.encrypt(data.api_key, app.state.gateway.scope(request.state.workspace_id))}
        try:
            await app.state.gateway.generate(request.state.workspace_id, request.state.session_hash,
                "Connection test. Return {\"ok\": true} as JSON.", {}, {"type": "object"}, override=config)
        except (ProviderError, ValueError):
            raise HTTPException(502, "连接失败，请检查服务地址、密钥、模型和余额") from None
        return {"ok": True}

    @app.delete("/api/provider", dependencies=[Depends(owner)], status_code=204)
    def delete_provider(request: Request, save_mode: str = Query("workspace", pattern="^(workspace|session)$")):
        app.state.gateway.session_configs.pop((request.state.session_hash, request.state.workspace_id), None)
        if save_mode == "workspace":
            with Session(app.state.store.engine) as session:
                row = session.get(ProviderConfigRow, request.state.workspace_id)
                if row:
                    session.delete(row)
                    session.commit()
        return Response(status_code=204)

    @app.get("/api/projects")
    def projects(request: Request):
        with Session(app.state.store.engine) as session:
            return [app.state.store.project_dict(row) for row in session.scalars(select(ProjectRow).where(
                ProjectRow.workspace_id == request.state.workspace_id, ProjectRow.archived.is_(False)))]

    @app.post("/api/projects", status_code=201)
    def create_project(data: ProductProject, request: Request):
        target(data.url)
        project_id = str(uuid4())
        with Session(app.state.store.engine) as session:
            session.add(ProjectRow(id=project_id, workspace_id=request.state.workspace_id, **data.model_dump()))
            session.commit()
        return project(project_id, request)

    @app.get("/api/projects/{project_id}")
    def project_detail(project_id: str, request: Request):
        return project(project_id, request)

    @app.patch("/api/projects/{project_id}")
    def change_project(project_id: str, data: ProductProject, request: Request):
        project(project_id, request)
        target(data.url)
        return app.state.store.change_project(project_id, data.model_dump())

    @app.delete("/api/projects/{project_id}", status_code=204)
    def archive(project_id: str, request: Request):
        project(project_id, request)
        app.state.store.change_project(project_id, {"archived": True})
        return Response(status_code=204)

    @app.get("/api/projects/{project_id}/environment")
    def environment(project_id: str, request: Request):
        item = project(project_id, request)
        with Session(app.state.store.engine) as session:
            row = session.get(EnvironmentRow, project_id)
            if not row:
                return {"base_url": item["url"], "headers": {}, "variables": {}, "secret_names": [], "has_account": False}
            stored = box.decrypt(row.encrypted_secrets, "environment:" + project_id)
            return {"base_url": row.base_url, "headers": row.headers, "variables": row.variables,
                    "secret_names": list(stored), "has_account": bool(stored.get("PASSWORD"))}

    @app.put("/api/projects/{project_id}/environment")
    def save_environment(project_id: str, data: EnvironmentConfig, request: Request):
        project(project_id, request)
        target(data.base_url)
        with Session(app.state.store.engine) as session:
            row = session.get(EnvironmentRow, project_id)
            secret_values = box.decrypt(row.encrypted_secrets, "environment:" + project_id) if row else {}
            secret_values.update(data.secrets)
            variables, headers = dict(data.variables), dict(data.headers)
            for k in list(variables):
                if re.search(r"password|token|secret|authorization|api.?key", k, re.I):
                    secret_values[k] = variables.pop(k)
            for k in list(headers):
                if k.lower() in {"host", "proxy-authorization", "content-length", "transfer-encoding"}:
                    raise HTTPException(422, "不允许设置此请求 Header")
                if re.search(r"password|token|secret|authorization|cookie|api.?key", k, re.I) and not re.search(r"\{\{", headers[k]):
                    name = "HEADER_" + re.sub(r"[^A-Za-z0-9_]", "_", k.upper())
                    secret_values[name], headers[k] = headers[k], "{{" + name + "}}"
            if data.username:
                secret_values["USERNAME"] = data.username
            if data.password:
                secret_values["PASSWORD"] = data.password
            if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", k) for k in (*variables, *secret_values)):
                raise HTTPException(422, "变量名仅支持字母、数字和下划线")
            values = {"base_url": data.base_url, "headers": headers, "variables": variables,
                      "encrypted_secrets": box.encrypt(secret_values, "environment:" + project_id)}
            if row:
                for k, v in values.items():
                    setattr(row, k, v)
            else:
                session.add(EnvironmentRow(project_id=project_id, **values))
            session.commit()
        return environment(project_id, request)

    @app.delete("/api/projects/{project_id}/environment", status_code=204)
    def delete_environment(project_id: str, request: Request):
        project(project_id, request)
        with Session(app.state.store.engine) as session:
            row = session.get(EnvironmentRow, project_id)
            if row:
                session.delete(row)
                session.commit()
        return Response(status_code=204)

    @app.get("/api/projects/{project_id}/endpoints")
    def endpoints(project_id: str, request: Request):
        project(project_id, request)
        with Session(app.state.store.engine) as session:
            row = session.get(ContractRow, project_id)
            return row.endpoints if row else []

    @app.post("/api/projects/{project_id}/openapi")
    async def import_contract(project_id: str, data: ContractImport, request: Request):
        project(project_id, request)
        if sum(v is not None for v in (data.url, data.content, data.endpoints)) != 1:
            raise HTTPException(422, "请选择 URL、文件内容或手动接口中的一种")
        try:
            if data.url:
                target(data.url)
                async with httpx.AsyncClient(transport=app.state.gateway.transport_factory(), trust_env=False,
                                             follow_redirects=False, timeout=15) as client:
                    response = await fetch_limited(client, "GET", data.url, max_bytes=1_000_000)
                    response.raise_for_status()
                    content = response.text
            else:
                content = data.content
            parsed = [v.model_dump() for v in data.endpoints] if data.endpoints is not None else parse_contract(content)
            if not parsed:
                raise ValueError("至少添加一个接口")
        except (ValueError, httpx.HTTPError, KeyError, TypeError, RecursionError):
            raise HTTPException(422, "无法解析接口规范；请检查格式，远程/循环引用需先打包") from None
        with Session(app.state.store.engine) as session:
            row = session.get(ContractRow, project_id)
            if row:
                row.endpoints = parsed
            else:
                session.add(ContractRow(project_id=project_id, endpoints=parsed))
            session.commit()
        return parsed

    def owned_task_ids(workspace):
        return select(TaskMetadataRow.task_id).join(ProjectRow).where(ProjectRow.workspace_id == workspace)

    @app.get("/api/dashboard")
    def dashboard(request: Request):
        ids = owned_task_ids(request.state.workspace_id)
        with Session(app.state.store.engine) as session:
            counts = dict(session.execute(select(TaskRow.status, func.count()).where(TaskRow.id.in_(ids))
                                          .group_by(TaskRow.status)).all())
            cases = session.execute(select(func.coalesce(func.sum(RunRow.passed), 0), func.coalesce(func.sum(RunRow.failed), 0))
                                    .where(RunRow.task_id.in_(ids))).one()
            # Calendar day in Asia/Shanghai; stored timestamps are UTC.
            start = now().astimezone(timezone(timedelta(hours=8))).replace(hour=0, minute=0, second=0, microsecond=0)
            return {"projects": session.scalar(select(func.count()).select_from(ProjectRow).where(
                        ProjectRow.workspace_id == request.state.workspace_id, ProjectRow.archived.is_(False))),
                    "tasks": sum(counts.values()), "executions": session.scalar(select(func.count()).select_from(RunRow)
                        .where(RunRow.task_id.in_(ids), RunRow.started_at.is_not(None))),
                    "today_runs": session.scalar(select(func.count()).select_from(RunRow).where(
                        RunRow.task_id.in_(ids), RunRow.started_at >= start.astimezone(timezone.utc).replace(tzinfo=None))),
                    "passed": cases[0], "failed": cases[1], "system_errors": counts.get("system_error", 0),
                    "running": counts.get("running", 0) + counts.get("queued", 0),
                    "awaiting_approval": counts.get("awaiting_approval", 0),
                    "issues": session.scalar(select(func.count()).select_from(FindingRow).where(FindingRow.run_id.in_(ids)))}

    @app.get("/api/tasks")
    def tasks(request: Request):
        with Session(app.state.store.engine) as session:
            ids = list(session.scalars(select(TaskRow.id).where(TaskRow.id.in_(owned_task_ids(request.state.workspace_id)))
                                      .order_by(TaskRow.created_at.desc()).limit(200)))
        return [public_task(app.state.store.get(v, private=True)) for v in ids]

    @app.get("/api/projects/{project_id}/runs")
    def runs(project_id: str, request: Request):
        project(project_id, request)
        return [t for t in tasks(request) if t["project_id"] == project_id]

    @app.post("/api/tasks", status_code=202)
    async def create_task(data: RealTask, request: Request):
        item = project(data.project_id, request)
        try:
            app.state.gateway.config(request.state.workspace_id, request.state.session_hash)
        except ProviderError:
            raise HTTPException(409, "尚未配置模型服务") from None
        with Session(app.state.store.engine) as session:
            environment = session.get(EnvironmentRow, data.project_id)
            variables = dict(environment.variables) if environment else {}
            saved = box.decrypt(environment.encrypted_secrets, "environment:" + data.project_id) if environment else {}
            base = data.target_url or (environment.base_url if environment else item["url"])
        target(base)
        if origin(base) != origin(environment.base_url if environment else item["url"]):
            raise HTTPException(422, "测试目标必须属于当前项目环境")
        variables.update(saved)
        variables.update(data.session_secrets)
        if len(data.session_secrets) and any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", k) for k in data.session_secrets):
            raise HTTPException(422, "无效的临时变量名")
        contracts = endpoints(data.project_id, request) if item["project_type"] == "api" else []
        selected = [e for e in contracts if e["method"] + " " + e["path"] in data.endpoints]
        if item["project_type"] == "api" and (not selected or len(selected) != len(set(data.endpoints))):
            raise HTTPException(422, "请从项目接口列表选择测试目标")
        counts = dashboard(request)
        if counts["running"] + counts["awaiting_approval"] >= 20:
            raise HTTPException(429, "请先完成或取消待执行任务")
        # Persist variable names and placeholders only; never persist run-only secrets.
        goal = data.goal
        for k, v in sorted(variables.items(), key=lambda item: len(item[1]), reverse=True):
            if v:
                goal = goal.replace(v, "{{" + k + "}}")
        task_id = app.state.store.create(TaskCreate(project_id=data.project_id, target_url=base, goal=goal,
            title=scrub(data.title, variables.values()) if data.title else None,
            test_type="api" if item["project_type"] == "api" else "regression", max_retries=0))
        with Session(app.state.store.engine) as session:
            row = session.get(TaskRow, task_id)
            row.request = {**row.request, "endpoint_contracts": selected, "variable_names": list(variables)}
            session.commit()
        if data.session_secrets:
            app.state.runner.transient[task_id] = box.encrypt(data.session_secrets, "run:" + task_id)
        app.state.runner.submit(task_id, request.state.workspace_id, request.state.session_hash)
        return {"id": task_id, "status": "queued"}

    @app.get("/api/tasks/{task_id}")
    def detail(task_id: str, request: Request):
        return public_task(task(task_id, request))

    @app.patch("/api/tasks/{task_id}/plan", dependencies=[Depends(review_lock)])
    async def edit_plan(task_id: str, data: dict, request: Request):
        row = task(task_id, request)
        if row["status"] != "awaiting_approval" or (row.get("approval") or {}).get("scope") != "plan":
            raise HTTPException(409, "仅可修改待审核的完整计划")
        try:
            plan = validate_plan(data, row, PublicPolicy(settings, row["request"]["target_url"]))
        except (ValueError, ValidationError):
            raise HTTPException(422, "计划无效，请检查断言、接口范围和敏感变量占位符") from None
        with Session(app.state.store.engine) as session:
            # Rebuild generated case rows, so deleted/added cases cannot leave stale counts.
            for old in session.scalars(select(CaseRow).where(CaseRow.task_id == task_id)):
                session.delete(old)
            session.commit()
        app.state.store.update(task_id, plan=plan)
        return detail(task_id, request)

    @app.post("/api/tasks/{task_id}/regenerate", status_code=202)
    async def regenerate(task_id: str, request: Request):
        old = task(task_id, request)
        if old["status"] != "awaiting_approval":
            raise HTTPException(409, "仅可重新生成待审核计划")
        transient = app.state.runner.transient.get(task_id)
        session_secrets = box.decrypt(transient, "run:" + task_id) if transient else {}
        await app.state.runner.cancel(task_id)
        # A new task preserves the old review history and avoids replaying graph checkpoints.
        return await create_task(RealTask(project_id=old["project_id"], goal=old["request"]["goal"],
            title=old["title"], target_url=old["request"]["target_url"],
            endpoints=[e["method"] + " " + e["path"] for e in old["request"].get("endpoint_contracts", [])],
            session_secrets=session_secrets), request)

    @app.post("/api/tasks/{task_id}/approve", status_code=202, dependencies=[Depends(review_lock)])
    async def approve(task_id: str, data: Approval, request: Request):
        row = task(task_id, request)
        if row["status"] != "awaiting_approval":
            raise HTTPException(409, "任务当前不需要审核")
        job = app.state.runner.jobs.get(task_id)
        if job and not job.done():
            await asyncio.shield(job)
            row = task(task_id, request)
        if data.plan_hash and data.plan_hash != plan_hash(row["plan"]):
            raise HTTPException(409, "计划已修改，请刷新后重新审核")
        if data.approved:
            validate_plan(row["plan"], row, PublicPolicy(settings, row["request"]["target_url"]))
        app.state.store.event(task_id, "approval_answer", {"approved": data.approved})
        app.state.store.update(task_id, status="queued")
        app.state.runner.submit(task_id, request.state.workspace_id, request.state.session_hash, resume=data.approved)
        return {"id": task_id, "status": "queued"}

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel(task_id: str, request: Request):
        row = task(task_id, request)
        if row["status"] in TERMINAL:
            raise HTTPException(409, "任务已结束")
        await app.state.runner.cancel(task_id)
        return {"status": "cancelled"}

    @app.patch("/api/tasks/{task_id}/project")
    def assign(task_id: str, data: dict, request: Request):
        row = task(task_id, request)
        if set(data) != {"project_id"}:
            raise HTTPException(422, "只允许提交 project_id")
        project(data["project_id"], request)
        if row["project_id"] != data["project_id"]:
            raise HTTPException(409, "历史任务保留原项目环境，请在目标项目创建新任务")
        app.state.store.reassign_task(task_id, data["project_id"])
        return detail(task_id, request)

    @app.get("/api/tasks/{task_id}/workflow")
    def workflow(task_id: str, request: Request):
        return public_task(task(task_id, request))["workflow_steps"]

    @app.get("/api/tasks/{task_id}/findings")
    def findings(task_id: str, request: Request):
        row = task(task_id, request)
        return [{**f, "suggestion_label": "AI 分析建议", "root_cause_confirmed": False} for f in row["findings"]]

    @app.get("/api/tasks/{task_id}/report")
    def report(task_id: str, request: Request, format: str = Query("html", pattern="^(html|json)$")):
        row = task(task_id, request)
        if format == "json":
            return public_task(row)
        return HTMLResponse(app.state.store.report_content(task_id) or report_html(row),
            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"})

    @app.get("/api/tasks/{task_id}/events")
    async def events(task_id: str, request: Request, after: int = Query(0, ge=0)):
        task(task_id, request)
        async def stream():
            cursor = after
            while not await request.is_disconnected():
                with Session(app.state.store.engine) as session:
                    saved = session.get(SessionRow, request.state.session_hash)
                    member = session.get(MemberRow, (request.state.workspace_id, request.state.user_id))
                    if not saved or not member or saved.expires_at.replace(tzinfo=timezone.utc) <= now():
                        break
                rows = app.state.store.events(task_id, cursor)
                for item in rows:
                    cursor = item["id"]
                    yield f"id: {cursor}\nevent: update\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                if not rows and app.state.store.get(task_id)["status"] in TERMINAL:
                    break
                if not rows:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.7)
        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/tasks/{task_id}/artifacts/{name}")
    def artifact(task_id: str, name: str, request: Request):
        task(task_id, request)
        directory = (settings.artifact_dir / task_id).resolve()
        path = (directory / name).resolve()
        if path.parent != directory or path.suffix not in {".png", ".json", ".html"} or not path.is_file():
            raise HTTPException(404, "证据不存在")
        return FileResponse(path, filename=name, media_type="application/octet-stream")

    @app.get("/")
    def index():
        return FileResponse(ROOT / "static" / "production.html")

    @app.get("/index.html")
    def index_alias():
        return index()

    # Only known product assets are reachable: legacy Demo HTML/JS are not served in Production.
    @app.get("/{asset}")
    def assets(asset: str):
        if asset not in {"production.js", "production.css", "i18n.js", "style.css"}:
            raise HTTPException(404, "资源不存在")
        return FileResponse(ROOT / "static" / asset)

    app.mount("/locales", StaticFiles(directory=ROOT / "static" / "locales"), name="locales")
    return app


def urlsplit_query(url):
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    return parsed.query or parsed.fragment
