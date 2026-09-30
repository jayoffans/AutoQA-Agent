import asyncio
import hmac
import json
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .config import Settings
from .reports import report_html
from .runner import Runner
from .schemas import TERMINAL, Approval, TaskCreate
from .security import TargetPolicy
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
        store = Store(settings.database_url)
        store.recover()
        async with AsyncSqliteSaver.from_conn_string(str(settings.data_dir / "checkpoints.sqlite")) as saver:
            await saver.setup()
            workflow = Workflow(settings, store, policy, saver, Telemetry())
            app.state.store, app.state.workflow = store, workflow
            app.state.runner = Runner(workflow, store, settings.concurrency)
            try:
                yield
            finally:
                await app.state.runner.shutdown()
                store.engine.dispose()

    app = FastAPI(title="AutoQA Agent", version="1.0.0", lifespan=lifespan,
                  description="自然语言 Web 测试、人工审批、可复核的故障证据与持久化检查点")

    def auth(authorization: str | None = Header(default=None), token: str | None = Query(default=None)):
        if settings.api_token:
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

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "1.0.0", "model_enabled": settings.model_enabled,
                "mcp_enabled": settings.mcp_enabled,
                "database": "mysql" if settings.database_url.startswith("mysql") else "sqlite",
                "authentication_required": bool(settings.api_token)}

    @app.get("/api/tasks", dependencies=[Depends(auth)])
    def tasks():
        return app.state.store.list()

    @app.post("/api/tasks", status_code=202, dependencies=[Depends(auth)])
    async def create(request: TaskCreate):
        try:
            policy.validate(request.target_url)
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
        outstanding = [t for t in app.state.store.list(200) if t["status"] not in TERMINAL]
        if len(outstanding) >= 20:
            raise HTTPException(429, "Too many outstanding tasks; complete or cancel existing tasks")
        task_id = app.state.store.create(request)
        app.state.runner.submit(task_id)
        return {"id": task_id, "status": "queued"}

    @app.get("/api/tasks/{task_id}", dependencies=[Depends(auth)])
    def detail(task_id: str):
        task = get_task(task_id)
        directory = settings.artifact_dir / task_id
        task["artifacts"] = sorted(p.name for p in directory.glob("*") if p.is_file()) if directory.exists() else []
        return task

    @app.post("/api/tasks/{task_id}/approve", status_code=202, dependencies=[Depends(auth)])
    async def approve(task_id: str, answer: Approval):
        task = get_task(task_id)
        if task["status"] != "awaiting_approval":
            raise HTTPException(409, "Task is not awaiting approval")
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
        return HTMLResponse(report_html(task), headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

    @app.get("/demo/api/broken")
    def broken():
        return HTMLResponse('{"error":"Inventory service unavailable"}', status_code=500,
                            media_type="application/json")

    app.mount("/demo", StaticFiles(directory=ROOT / "demo", html=True), name="demo")
    app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="dashboard")
    return app


app = create_app()
