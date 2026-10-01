import asyncio
import hmac
import json
import logging
import re
import secrets
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .checkpoints import SQLCheckpointSaver
from .config import Settings
from .demo_guard import DemoLimitError, identity, source_address
from .reports import report_html
from .runner import Runner
from .schemas import TERMINAL, Approval, ProjectCreate, ProjectPatch, TaskCreate
from .security import TargetPolicy, origin
from .seed import SEED_TASK_ID, seed_demo
from .store import Store
from .telemetry import Telemetry
from .workflow import Workflow

ROOT = Path(__file__).parent


def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    policy = TargetPolicy(settings)

    @asynccontextmanager
    async def lifespan(app):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.artifact_dir.mkdir(parents=True, exist_ok=True)
        store = Store(settings.database_url, settings.artifact_dir, settings.studio_url + "/demo/login.html")
        store.recover()
        app.state.store = store
        if settings.seed_demo:
            await seed_demo(store, settings, app)
        async with AsyncExitStack() as stack:
            if settings.database_url.startswith("sqlite"):
                saver = await stack.enter_async_context(
                    AsyncSqliteSaver.from_conn_string(str(settings.data_dir / "checkpoints.sqlite")))
                await saver.setup()
            else:
                saver = SQLCheckpointSaver(store.engine)
            workflow = Workflow(settings, store, policy, saver, Telemetry())
            app.state.store, app.state.workflow = store, workflow
            app.state.runner = Runner(workflow, store, settings.concurrency, demo_app=app if settings.demo_mode else None)
            try:
                yield
            finally:
                await app.state.runner.shutdown()
                store.engine.dispose()

    app = FastAPI(title="AutoQA Studio", version="1.0.0", lifespan=lifespan,
                  description="AI-powered Software Quality Engineering Platform", debug=False,
                  docs_url=None if settings.env == "production" else "/docs",
                  redoc_url=None if settings.env == "production" else "/redoc",
                  openapi_url=None if settings.env == "production" else "/openapi.json")

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse({"detail": "Invalid request; check the required fields and allowed Demo options."},
                            status_code=422)

    @app.middleware("http")
    async def public_boundary(request: Request, call_next):
        visitor = request.cookies.get("autoqa_visitor", "")
        if not re.fullmatch(r"[a-f0-9]{64}", visitor):
            visitor = secrets.token_hex(32)
        request.state.visitor = identity(visitor)
        try:
            if request.method in {"POST", "PATCH", "PUT"}:
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 16384:
                        return JSONResponse({"detail": "Request is too large."}, status_code=413)
                request._body = bytes(body)
            path = request.url.path
            if settings.demo_mode:
                if path.startswith("/api/projects") and request.method not in {"GET", "HEAD"}:
                    return JSONResponse({"detail": "Public Demo projects are read-only."}, status_code=403)
                match = re.match(r"^/api/tasks/([^/]+)", path)
                if match:
                    task_id = match[1]
                    readonly = request.method in {"GET", "HEAD"}
                    if not (readonly and task_id == SEED_TASK_ID) and not app.state.store.owns(
                            task_id, request.state.visitor):
                        return JSONResponse({"detail": "This Demo task is read-only or belongs to another visitor."},
                                            status_code=403)
                if request.method == "PATCH":
                    return JSONResponse({"detail": "Public Demo data is read-only."}, status_code=403)
            response = await call_next(request)
        except Exception:
            logging.getLogger(__name__).warning("Request failed", extra={"event": "request_failed"})
            response = JSONResponse({"detail": "Service temporarily unavailable. Please retry later."}, status_code=500)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if settings.demo_mode:
            response.set_cookie("autoqa_visitor", visitor, httponly=True, secure=settings.env == "production",
                                samesite="strict", max_age=86400)
        return response

    def auth(authorization: str | None = Header(default=None), token: str | None = Query(default=None)):
        if settings.api_token and not settings.demo_mode:
            provided = (authorization or "").removeprefix("Bearer ") or token or ""
            if not hmac.compare_digest(provided, settings.api_token):
                raise HTTPException(401, "Provide the configured API token")

    def get_task(task_id):
        try:
            UUID(task_id)
        except ValueError:
            raise HTTPException(404, "Task not found") from None
        task = app.state.store.get(task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        return task

    @app.get("/health")
    def health_status():
        return {"status": "ok"}

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "1.0.0", "model_enabled": settings.model_enabled,
                "mcp_enabled": settings.mcp_enabled,
                "database": "mysql" if settings.database_url.startswith("mysql") else "sqlite",
                "authentication_required": bool(settings.api_token) and not settings.demo_mode,
                "llm_provider": settings.llm_provider, "demo_mode": settings.demo_mode,
                "demo_daily_limit": settings.demo_daily_limit, "demo_ip_limit": settings.demo_ip_limit,
                "demo_llm_enabled": settings.demo_llm_enabled}

    @app.get("/api/dashboard", dependencies=[Depends(auth)])
    def dashboard():
        return app.state.store.dashboard()

    @app.get("/api/projects", dependencies=[Depends(auth)])
    def projects():
        rows = app.state.store.projects()
        return [r for r in rows if r["builtin"]] if settings.demo_mode else rows

    @app.post("/api/projects", status_code=201, dependencies=[Depends(auth)])
    def create_project(data: ProjectCreate):
        try:
            origin(data.url)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return app.state.store.create_project(data)

    @app.get("/api/projects/{project_id}", dependencies=[Depends(auth)])
    def project_detail(project_id: str):
        project = app.state.store.project(project_id)
        if not project or settings.demo_mode and not project["builtin"]:
            raise HTTPException(404, "Project not found")
        return project

    @app.patch("/api/projects/{project_id}", dependencies=[Depends(auth)])
    def update_project(project_id: str, data: ProjectPatch):
        try:
            if data.url:
                origin(data.url)
            return app.state.store.change_project(project_id, data.model_dump(exclude_unset=True))
        except KeyError:
            raise HTTPException(404, "Project not found") from None
        except ValueError as error:
            raise HTTPException(409 if "Built-in" in str(error) else 422, str(error)) from None

    @app.delete("/api/projects/{project_id}", status_code=204, dependencies=[Depends(auth)])
    def delete_project(project_id: str):
        try:
            # Archive instead of cascading away test history and evidence.
            app.state.store.change_project(project_id, {"archived": True})
        except KeyError:
            raise HTTPException(404, "Project not found") from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return Response(status_code=204)

    @app.get("/api/tasks", dependencies=[Depends(auth)])
    def tasks(request: Request):
        return app.state.store.list(owner=request.state.visitor if settings.demo_mode else None,
                                    seed_id=SEED_TASK_ID)

    @app.post("/api/tasks", status_code=202, dependencies=[Depends(auth)])
    async def create(request: TaskCreate, http_request: Request):
        try:
            if settings.demo_mode:
                from urllib.parse import urlsplit
                if request.test_type != "api" or not policy.is_demo(request.target_url) or (
                        urlsplit(request.target_url).path != "/demo/api/login") or request.steps:
                    raise ValueError("Public Demo supports the built-in Login API scenario only.")
                if request.project_id:
                    project = app.state.store.project(request.project_id)
                    if not project or not project["builtin"] or "/api/" not in project["url"]:
                        raise ValueError("Choose a built-in Login API Demo project.")
                request = request.model_copy(update={"target_url": settings.studio_url + "/demo/api/login",
                    "goal": "Validate all six authentication contract scenarios", "title": "Authentication Regression Test",
                    "max_steps": 6, "max_retries": 0, "step_timeout_ms": 5000, "task_timeout_seconds": 90})
            policy.validate(request.target_url)
            if request.project_id and not app.state.store.project(request.project_id):
                raise ValueError("Project not found or archived")
            if request.test_type == "api":
                from urllib.parse import urlsplit
                if not policy.is_demo(request.target_url) or urlsplit(request.target_url).path != "/demo/api/login":
                    raise ValueError("API demo currently supports only the Studio /demo/api/login endpoint")
                if request.steps:
                    raise ValueError("API plans are generated through the configured provider; omit browser steps")
            if request.steps:
                from .schemas import Plan
                Plan(summary=request.goal, steps=request.steps)
                if len(request.steps) > request.max_steps:
                    raise ValueError("Plan exceeds max_steps")
                for step in request.steps:
                    if step.action == "navigate":
                        policy.validate(step.value)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        counts = app.state.store.dashboard()
        if counts["running"] + counts["awaiting_approval"] >= 20:
            raise HTTPException(429, "Too many outstanding tasks; complete or cancel existing tasks")
        try:
            task_id = app.state.store.create(request, source=source_address(http_request, settings) if settings.demo_mode else None,
                owner=http_request.state.visitor, settings=settings)
        except DemoLimitError as error:
            raise HTTPException(429, str(error), headers={"Retry-After": "86400"}) from None
        app.state.runner.submit(task_id)
        return {"id": task_id, "status": "queued"}

    @app.get("/api/tasks/{task_id}", dependencies=[Depends(auth)])
    def detail(task_id: str):
        task = get_task(task_id)
        directory = settings.artifact_dir / task_id
        task["artifacts"] = sorted(p.name for p in directory.glob("*") if p.is_file()) if directory.exists() else []
        return task

    @app.patch("/api/tasks/{task_id}/project", dependencies=[Depends(auth)])
    def assign_project(task_id: str, data: dict):
        get_task(task_id)
        if set(data) != {"project_id"} or not isinstance(data["project_id"], str):
            raise HTTPException(422, "Provide project_id only")
        try:
            app.state.store.reassign_task(task_id, data["project_id"])
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return get_task(task_id)

    @app.get("/api/tasks/{task_id}/workflow", dependencies=[Depends(auth)])
    def workflow_history(task_id: str):
        return get_task(task_id)["workflow_steps"]

    @app.get("/api/tasks/{task_id}/findings", dependencies=[Depends(auth)])
    def findings(task_id: str):
        return get_task(task_id)["findings"]

    @app.post("/api/tasks/{task_id}/approve", status_code=202, dependencies=[Depends(auth)])
    async def approve(task_id: str, answer: Approval):
        task = get_task(task_id)
        if task["status"] != "awaiting_approval":
            raise HTTPException(409, "Task is not awaiting approval")
        job = app.state.runner.jobs.get(task_id)
        if job and not job.done():
            await asyncio.shield(job)
            task = get_task(task_id)
            if task["status"] != "awaiting_approval":
                raise HTTPException(409, "Task is no longer awaiting approval")
        if task["approval"].get("scope") == "step" and task_id not in app.state.workflow.sessions:
            raise HTTPException(409, "Live browser lost after restart. Review evidence and create a fresh task.")
        app.state.store.event(task_id, "approval_answer", {"approved": answer.approved})
        app.state.store.update(task_id, status="queued")
        app.state.runner.submit(task_id, resume=answer.approved)
        return {"id": task_id, "status": "queued"}

    @app.post("/api/tasks/{task_id}/cancel", dependencies=[Depends(auth)])
    async def cancel(task_id: str):
        task = get_task(task_id)
        if task["status"] in TERMINAL:
            raise HTTPException(409, "Task already completed")
        await app.state.runner.cancel(task_id)
        return {"id": task_id, "status": "cancelled"}

    @app.get("/api/tasks/{task_id}/events", dependencies=[Depends(auth)])
    async def events(task_id: str, request: Request, after: int = Query(0, ge=0),
                     last_event_id: str | None = Header(None)):
        get_task(task_id)
        cursor = max(after, int(last_event_id or "0")) if (last_event_id or "0").isdigit() else after

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                rows = app.state.store.events(task_id, cursor)
                for event in rows:
                    cursor = event["id"]
                    yield f"id: {cursor}\nevent: update\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                task = app.state.store.get(task_id)
                if task["status"] in TERMINAL and not rows:
                    break
                if not rows:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.7)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/tasks/{task_id}/artifacts/{name}", dependencies=[Depends(auth)])
    def artifact(task_id: str, name: str):
        get_task(task_id)
        if settings.demo_mode:
            raise HTTPException(403, "Use the public Report and its HTTP evidence to review this Demo.")
        allowed_extensions = {".png", ".json", ".html", ".zip"}
        directory = (settings.artifact_dir / task_id).resolve()
        target = (directory / name).resolve()
        if target.parent != directory or target.suffix not in allowed_extensions or not target.is_file():
            raise HTTPException(404, "Artifact not found")
        # DOM must be downloaded, never executed as a same-origin HTML document.
        return FileResponse(target, filename=name, media_type="application/octet-stream" if
                            target.suffix in {".html", ".zip", ".json"} else "image/png",
                            headers={"X-Content-Type-Options": "nosniff"})

    @app.get("/api/tasks/{task_id}/report", dependencies=[Depends(auth)])
    def report(task_id: str, format: str = Query("html", pattern="^(html|json)$")):
        task = get_task(task_id)
        if format == "json":
            return task
        return HTMLResponse(app.state.store.report_content(task_id) or report_html(task),
                            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

    @app.post("/demo/api/login")
    async def demo_login(request: Request):
        try:
            payload = await request.json()
        except (ValueError, UnicodeError):
            return JSONResponse({"error": "Malformed JSON"}, status_code=400)
        if not isinstance(payload, dict) or not payload.get("username") or not payload.get("password"):
            return JSONResponse({"error": "Username and password are required"}, status_code=400)
        valid = payload["username"] == "demo" and (payload["password"] == "autoqa123" or settings.demo_login_fault)
        if not valid:
            return JSONResponse({"error": "Invalid credentials"}, status_code=401)
        # Deliberate, documented fault switch; the tests assert against the correct API contract.
        return {"access_token": "demo-test-token", "token_type": "bearer"}

    @app.get("/demo/api/protected")
    def demo_protected(authorization: str | None = Header(default=None)):
        if authorization != "Bearer demo-test-token":
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return {"username": "demo"}

    @app.get("/demo/api/broken")
    def broken():
        return HTMLResponse('{"error":"Inventory service unavailable"}', status_code=500,
                            media_type="application/json")

    app.mount("/demo", StaticFiles(directory=ROOT / "demo", html=True), name="demo")
    app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="dashboard")
    return app


app = create_app()
