import json
from datetime import datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
    func,
    or_,
    select,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Mapped, Session, mapped_column

from .config import Settings
from .demo_guard import DemoOwnerRow, DemoQuotaRow, reserve
from .product_models import InstallationRow  # Register identity/configuration metadata before migrations.
from .reports import report_html, report_issues, report_metrics
from .schemas import TERMINAL, ProjectCreate, TaskCreate
from .security import redact
from .store_base import Base, now
from .studio_models import (
    CaseRow,
    FindingRow,
    PlanRow,
    ProjectRow,
    ReportRow,
    RunRow,
    SchemaVersionRow,
    TaskMetadataRow,
    WorkflowStepRow,
)


class TaskRow(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    request: Mapped[dict] = mapped_column(JSON)
    plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    diagnosis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    approval: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    planner_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class StepRow(Base):
    __tablename__ = "steps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict] = mapped_column(JSON)


class EventRow(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    kind: Mapped[str] = mapped_column(String(50))
    payload: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), "mysql"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Store:
    def __init__(self, url: str, artifact_dir: Path | None = None,
                 demo_url: str | None = None, *, mode: str | None = None):
        database = make_url(url)
        if database.drivername.startswith("sqlite") and database.database not in {None, "", ":memory:"}:
            Path(database.database).parent.mkdir(parents=True, exist_ok=True)
        options = {"check_same_thread": False} if url.startswith("sqlite") else {}
        if options:
            options["timeout"] = 30
        self.engine = create_engine(url, connect_args=options, pool_pre_ping=True)
        if options:
            @event.listens_for(self.engine, "connect")
            def sqlite_constraints(connection, record):
                connection.execute("PRAGMA foreign_keys=ON")
        elif database.get_backend_name() == "mysql" and mode == "production":
            @event.listens_for(self.engine, "connect")
            def mysql_utc(connection, record):
                with connection.cursor() as cursor:
                    cursor.execute("SET SESSION time_zone = '+00:00'")
        self.artifact_dir = artifact_dir
        from .migrations import upgrade
        upgrade(self.engine)
        with Session(self.engine) as session:
            marker = session.get(InstallationRow, 1)
            if marker and marker.mode == "production" and mode != "production":
                raise ValueError("Demo and Production must use separate databases; explicit production mode required")
        if mode:
            with Session(self.engine) as session:
                marker = session.get(InstallationRow, 1)
                if marker and marker.mode != mode:
                    raise ValueError("Demo and Production must use separate databases")
                if not marker:
                    if mode == "production" and session.scalar(select(TaskRow.id).limit(1)):
                        raise ValueError("Production requires a separate clean database; legacy tasks are retained")
                    if mode == "production" and session.scalar(select(ProjectRow.id).limit(1)):
                        raise ValueError("Production requires a separate clean database; legacy projects are retained")
                    session.add(InstallationRow(id=1, mode=mode))
                    session.commit()
        self.production = mode == "production"
        if self.production:
            return
        self.bootstrap(demo_url or Settings(_env_file=None).studio_url + "/demo/login.html")
        with Session(self.engine) as session:
            if not session.get(DemoQuotaRow, "global"):
                session.add(DemoQuotaRow(id="global", day="", count=0))
                session.commit()

    def create(self, request: TaskCreate, *, source=None, owner=None, settings=None) -> str:
        if self.production and not request.project_id:
            raise ValueError("Production tasks require an owned project")
        task_id = str(uuid4())
        with Session(self.engine) as s:
            if source is not None:
                reserve(s, source, settings)
            s.add(TaskRow(id=task_id, status="queued", request=request.model_dump()))
            s.flush()
            if source is not None:
                s.add(DemoOwnerRow(task_id=task_id, owner=owner))
            project = self.resolve_project(s, request.project_id, request.target_url)
            s.add(TaskMetadataRow(task_id=task_id, project_id=project.id,
                                  title=request.title or request.goal[:400], test_type=request.test_type))
            s.add(RunRow(id=task_id, task_id=task_id, status="queued"))
            for name in ["Requirement Analyzer", "Test Planner", "Human Review", "Execution Agent", "Report Generator"]:
                s.add(WorkflowStepRow(task_id=task_id, step_name=name, status="Waiting"))
            s.commit()
        self.event(task_id, "queued", {"message": "测试任务已加入队列"})
        return task_id

    def owns(self, task_id, owner):
        with Session(self.engine) as session:
            row = session.get(DemoOwnerRow, task_id)
            return bool(row and row.owner == owner)

    def update(self, task_id: str, **changes):
        with Session(self.engine) as s:
            row = s.get(TaskRow, task_id)
            if row is None:
                raise KeyError(task_id)
            for key, value in changes.items():
                setattr(row, key, value)
            row.updated_at = now()
            run = s.get(RunRow, task_id)
            if run and "status" in changes:
                run.status = row.status
                if row.status == "running" and not run.started_at:
                    run.started_at = now()
                if row.status in TERMINAL:
                    run.finished_at = now()
            if "plan" in changes and changes["plan"]:
                self.save_plan(s, task_id, changes["plan"])
            s.commit()
        if changes.get("status") in TERMINAL:
            self.finalize(task_id)

    def event(self, task_id: str, kind: str, payload: dict):
        with Session(self.engine) as s:
            s.add(EventRow(task_id=task_id, kind=kind, payload=json.dumps(redact(payload), ensure_ascii=False)))
            self.track_workflow(s, task_id, kind, redact(payload))
            s.commit()

    def step(self, task_id: str, index: int, attempt: int, result: dict):
        with Session(self.engine) as s:
            s.add(StepRow(task_id=task_id, index=index, attempt=attempt, result=redact(result)))
            case = s.scalar(select(CaseRow).where(CaseRow.task_id == task_id, CaseRow.step_index == index))
            if case:
                case.status = result.get("status") or ("passed" if result["ok"] else "failed")
            s.commit()

    def get(self, task_id: str, private: bool = False):
        with Session(self.engine) as s:
            row = s.get(TaskRow, task_id)
            if row is None:
                return None
            result = {"id": row.id, "status": row.status, "request": row.request,
                      "plan": row.plan, "diagnosis": row.diagnosis, "approval": row.approval,
                      "planner_mode": row.planner_mode, "created_at": row.created_at.isoformat(),
                      "updated_at": row.updated_at.isoformat()}
            if self.production:
                result["production"] = True
            result["steps"] = [dict(index=v.index, attempt=v.attempt, **v.result) for v in
                               s.scalars(select(StepRow).where(StepRow.task_id == task_id).order_by(StepRow.id))]
            meta = s.get(TaskMetadataRow, task_id)
            project = s.get(ProjectRow, meta.project_id) if meta else None
            result.update(project_id=meta.project_id if meta else None, title=meta.title if meta else row.request["goal"],
                          test_type=meta.test_type if meta else "regression", project_name=project.name if project else None)
            result["workflow_steps"] = [self.workflow_dict(v) for v in s.scalars(
                select(WorkflowStepRow).where(WorkflowStepRow.task_id == task_id).order_by(WorkflowStepRow.id))]
            result["run"] = self.run_dict(s.get(RunRow, task_id))
            result["findings"] = [self.finding_dict(v) for v in s.scalars(
                select(FindingRow).where(FindingRow.run_id == task_id).order_by(FindingRow.id))]
            stored_report = s.scalar(select(ReportRow).where(ReportRow.run_id == task_id))
            result["report"] = ({"id": stored_report.id, "summary": stored_report.summary,
                                 "html_path": stored_report.html_path,
                                 "created_at": stored_report.created_at.isoformat()} if stored_report else None)
            return result if private else redact(result)

    def list(self, limit: int = 50, *, owner=None, seed_id=None):
        with Session(self.engine) as s:
            query = (select(TaskRow, TaskMetadataRow, ProjectRow.name)
                     .outerjoin(TaskMetadataRow, TaskMetadataRow.task_id == TaskRow.id))
            query = query.outerjoin(ProjectRow, TaskMetadataRow.project_id == ProjectRow.id)
            if owner is not None:
                owned = select(DemoOwnerRow.task_id).where(DemoOwnerRow.owner == owner)
                query = query.where(or_(TaskRow.id == seed_id, TaskRow.id.in_(owned)))
                query = query.order_by((TaskRow.id == seed_id).desc())
            rows = s.execute(query.order_by(TaskRow.created_at.desc()).limit(limit))
            return [{"id": r.id, "status": r.status, "goal": r.request["goal"],
                     "target_url": r.request["target_url"], "created_at": r.created_at.isoformat(),
                     "planner_mode": r.planner_mode,
                     "project_id": meta.project_id if meta else None, "project_name": name,
                     "title": meta.title if meta else r.request["goal"],
                     "test_type": meta.test_type if meta else "regression"} for r, meta, name in rows]

    def events(self, task_id: str, after: int = 0):
        with Session(self.engine) as s:
            rows = s.scalars(select(EventRow).where(EventRow.task_id == task_id, EventRow.id > after)
                             .order_by(EventRow.id).limit(200))
            return [{"id": r.id, "kind": r.kind, "payload": json.loads(r.payload),
                     "at": r.created_at.isoformat()} for r in rows]

    def recover(self):
        with Session(self.engine) as s:
            rows = list(s.scalars(select(TaskRow).where(TaskRow.status.in_(["queued", "running"])) ))
            for row in rows:
                row.status = "interrupted"
                row.diagnosis = {"category": "service_restart", "summary": "服务重启中断了执行",
                                 "suggestion": "检查已有证据后创建新任务；不会自动重放已提交的操作。"}
            s.commit()
            interrupted = [row.id for row in rows]
        for task_id in interrupted:
            self.event(task_id, "error", {"summary": "Execution interrupted by service restart"})
            self.finalize(task_id)

    def bootstrap(self, demo_url):
        with Session(self.engine) as s:
            for name, path in [("Demo Commerce", demo_url), ("Login API Testing", demo_url.rsplit("/", 1)[0] + "/api/login")]:
                project_id = str(uuid5(NAMESPACE_URL, "autoqa-studio/" + name))
                project = s.get(ProjectRow, project_id)
                if not project:
                    s.add(ProjectRow(id=project_id, name=name, url=path, builtin=True,
                                     description="Controlled demo environment; executions are real."))
                else:
                    project.url = path  # Follow configured PORT/public origin across deployments.
            s.flush()
            legacy = select(TaskRow).outerjoin(TaskMetadataRow, TaskMetadataRow.task_id == TaskRow.id).where(
                TaskMetadataRow.task_id.is_(None))
            for row in s.scalars(legacy):
                project = self.resolve_project(s, None, row.request["target_url"])
                s.add(TaskMetadataRow(task_id=row.id, project_id=project.id,
                                      title=row.request["goal"][:400], test_type=row.request.get("test_type", "regression")))
                if not s.get(RunRow, row.id):
                    s.add(RunRow(id=row.id, task_id=row.id, status=row.status,
                                 started_at=row.created_at, finished_at=row.updated_at if row.status in TERMINAL else None))
                if row.plan:
                    self.save_plan(s, row.id, row.plan)
                for step in s.scalars(select(StepRow).where(StepRow.task_id == row.id)):
                    case = s.scalar(select(CaseRow).where(CaseRow.task_id == row.id, CaseRow.step_index == step.index))
                    if case:
                        case.status = "passed" if step.result["ok"] else "failed"
                for old_event in s.scalars(select(EventRow).where(EventRow.task_id == row.id).order_by(EventRow.id)):
                    self.track_workflow(s, row.id, old_event.kind, json.loads(old_event.payload), old_event.created_at)
            if not s.get(SchemaVersionRow, 2):
                s.add(SchemaVersionRow(version=2))
            s.commit()
        # Backfill immutable reports only once; legacy task tables and evidence remain intact.
        with Session(self.engine) as s:
            unreported = list(s.scalars(select(TaskRow.id).outerjoin(ReportRow, TaskRow.id == ReportRow.run_id)
                                       .where(TaskRow.status.in_(TERMINAL), ReportRow.id.is_(None))))
        for task_id in unreported:
            self.finalize(task_id)

    def resolve_project(self, session, project_id, url):
        if project_id:
            project = session.get(ProjectRow, project_id)
            if not project or project.archived:
                raise ValueError("Project not found or archived")
            return project
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        for project in session.scalars(select(ProjectRow).where(ProjectRow.builtin.is_(True))):
            if parsed.path.startswith("/demo/") and urlsplit(project.url).netloc == parsed.netloc:
                if ("/demo/api/" in parsed.path) == ("/demo/api/" in project.url):
                    return project
        from .security import origin
        project_id = str(uuid5(NAMESPACE_URL, "autoqa-target/" + origin(url)))
        project = session.get(ProjectRow, project_id)
        if not project:
            project = ProjectRow(id=project_id, name=parsed.hostname, url=url, description="Imported task target")
            session.add(project)
            session.flush()
        return project

    @staticmethod
    def project_dict(row, counts=None):
        return {"id": row.id, "name": row.name, "description": row.description, "url": row.url,
                "workspace_id": row.workspace_id, "project_type": row.project_type, "environment": row.environment,
                "builtin": row.builtin, "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(), **(counts or {})}

    def projects(self):
        with Session(self.engine) as s:
            counts = {}
            for project_id, status, count in s.execute(select(TaskMetadataRow.project_id, TaskRow.status, func.count())
                    .join(TaskRow, TaskMetadataRow.task_id == TaskRow.id)
                    .group_by(TaskMetadataRow.project_id, TaskRow.status)):
                stats = counts.setdefault(project_id, {"runs": 0, "passed": 0, "failed": 0})
                stats["runs"] += count
                if status in {"passed", "failed"}:
                    stats[status] += count
            return [self.project_dict(row, counts.get(row.id, {"runs": 0, "passed": 0, "failed": 0})) for row in
                    s.scalars(select(ProjectRow).where(ProjectRow.archived.is_(False)).order_by(ProjectRow.created_at))]

    def project(self, project_id):
        with Session(self.engine) as s:
            row = s.get(ProjectRow, project_id)
            return self.project_dict(row) if row and not row.archived else None

    def create_project(self, data: ProjectCreate):
        project_id = str(uuid4())
        with Session(self.engine) as s:
            s.add(ProjectRow(id=project_id, **data.model_dump()))
            s.commit()
        return self.project(project_id)

    def change_project(self, project_id, changes):
        with Session(self.engine) as s:
            row = s.get(ProjectRow, project_id)
            if not row or row.archived:
                raise KeyError(project_id)
            if row.builtin:
                raise ValueError("Built-in demo projects cannot be changed or deleted")
            for name, value in changes.items():
                if value is not None:
                    setattr(row, name, value)
            row.updated_at = now()
            s.commit()
        return self.project(project_id)

    def dashboard(self):
        with Session(self.engine) as s:
            counts = dict(s.execute(select(TaskRow.status, func.count()).group_by(TaskRow.status)).all())
            passed, failed = counts.get("passed", 0), counts.get("failed", 0)
            return {"projects": s.scalar(select(func.count()).select_from(ProjectRow).where(ProjectRow.archived.is_(False))),
                    "tasks": sum(counts.values()), "running": counts.get("queued", 0) + counts.get("running", 0),
                    "passed": passed, "failed": failed, "awaiting_approval": counts.get("awaiting_approval", 0),
                    "interrupted": counts.get("interrupted", 0),
                    "issues": s.scalar(select(func.count()).select_from(FindingRow)),
                    "success_rate": int(passed / (passed + failed) * 100 + 0.5) if passed + failed else None}

    def save_plan(self, session, task_id, plan):
        row = session.scalar(select(PlanRow).where(PlanRow.task_id == task_id))
        if row:
            row.content = redact(plan)
        else:
            session.add(PlanRow(id=task_id, task_id=task_id, content=redact(plan), status="generated"))
        session.flush()
        existing = {c.step_index: c for c in session.scalars(select(CaseRow).where(CaseRow.task_id == task_id))}
        for index, step in enumerate(plan.get("steps", [])):
            if not step["action"].startswith("assert_") or index in existing:
                continue
            session.add(CaseRow(id=str(uuid4()), task_id=task_id, step_index=index,
                name=step["description"], description=step["description"], expected_result=redact(step), status="waiting"))

    def track_workflow(self, session, task_id, kind, payload, at=None):
        at = at or now()

        def stage(name, state, incoming=None, outgoing=None):
            row = session.scalar(select(WorkflowStepRow).where(WorkflowStepRow.task_id == task_id,
                                                             WorkflowStepRow.step_name == name))
            if not row:
                row = WorkflowStepRow(task_id=task_id, step_name=name)
                session.add(row)
                session.flush()
            row.status = state
            if incoming is not None:
                row.input = redact(incoming)
            if outgoing is not None:
                row.output = redact(outgoing)
            if (state != "Waiting" or incoming is not None) and not row.started_at:
                row.started_at = at
            if state in {"Completed", "Failed"}:
                row.finished_at = at

        if kind == "planner_started":
            task = session.get(TaskRow, task_id)
            stage("Requirement Analyzer", "Running", task.request)
            stage("Test Planner", "Running", task.request)
        elif kind == "plan_created":
            stage("Requirement Analyzer", "Completed", outgoing={"status": "requirements parsed"})
            stage("Test Planner", "Completed", outgoing=payload)
        elif kind == "approval_required":
            stage("Human Review", "Waiting", payload)
            stage("Execution Agent", "Waiting")
        elif kind == "approval_answer":
            stage("Human Review", "Completed" if payload["approved"] else "Failed", outgoing=payload)
            plan = session.scalar(select(PlanRow).where(PlanRow.task_id == task_id))
            if plan:
                plan.status = "approved" if payload["approved"] else "rejected"
        elif kind == "step_started":
            review = session.scalar(select(WorkflowStepRow).where(WorkflowStepRow.task_id == task_id,
                                                                 WorkflowStepRow.step_name == "Human Review"))
            if review and review.status == "Waiting":
                stage("Human Review", "Completed", outgoing={"approval": "not_required_or_pre_authorized"})
                plan = session.scalar(select(PlanRow).where(PlanRow.task_id == task_id))
                if plan:
                    plan.status = "approved"
            stage("Execution Agent", "Running")
            stage(f'Case/Step {payload["index"] + 1} · attempt {payload.get("attempt", 0) + 1}', "Running", payload)
        elif kind == "step_finished":
            stage(f'Case/Step {payload["index"] + 1} · attempt {payload.get("attempt", 0) + 1}',
                  "Completed" if payload["ok"] else "Failed", outgoing=payload)
        elif kind in {"diagnosis", "error"}:
            stage("Failure Analyzer", "Completed" if kind == "diagnosis" else "Failed", outgoing=payload)
        elif kind in {"completed", "cancelled"}:
            task = session.get(TaskRow, task_id)
            stage("Execution Agent", "Completed" if task.status == "passed" else "Failed", outgoing=payload)

    @staticmethod
    def workflow_dict(row):
        return {"step_name": row.step_name, "status": row.status, "input": row.input, "output": row.output,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "finished_at": row.finished_at.isoformat() if row.finished_at else None}

    @staticmethod
    def run_dict(row):
        return ({"id": row.id, "status": row.status, "passed": row.passed, "failed": row.failed,
                 "coverage": row.coverage, "started_at": row.started_at.isoformat() if row.started_at else None,
                 "finished_at": row.finished_at.isoformat() if row.finished_at else None} if row else None)

    @staticmethod
    def finding_dict(row):
        return {"id": row.id, "severity": row.severity, "title": row.title, "description": row.description,
                "suggestion": row.suggestion, "evidence": row.evidence}

    def finalize(self, task_id):
        task = self.get(task_id)
        if not task or task["status"] not in TERMINAL:
            return
        metrics = report_metrics(task)
        content = report_html(task)
        with Session(self.engine) as s:
            run = s.get(RunRow, task_id)
            if self.production:
                for case in s.scalars(select(CaseRow).where(CaseRow.task_id == task_id, CaseRow.status == "waiting")):
                    case.status = "skipped"
            run.status = task["status"]
            run.finished_at = run.finished_at or now()
            run.passed, run.failed, run.coverage = metrics["passed"], metrics["failed"], metrics["coverage"]
            for issue in report_issues(task):
                if not s.get(FindingRow, issue["id"]):
                    evidence = {"diagnosis": task["diagnosis"], "steps": [v for v in task["steps"] if not v["ok"]]}
                    if self.production:
                        evidence = {"diagnosis": task["diagnosis"], "root_cause_confirmed": False,
                                    "steps": [v for v in task["steps"] if not v["ok"] and v.get("status") != "system_error"]}
                        first = evidence["steps"][0] if evidence["steps"] else {}
                        evidence.update(affected=first.get("step", {}).get("path", task["request"]["target_url"]),
                                        expected=first.get("expected", first.get("step")),
                                        actual=first.get("actual", first.get("error")), suggestion_label="AI 分析建议")
                    s.add(FindingRow(id=issue["id"], run_id=task_id, severity=issue["severity"],
                        title=issue["description"][:400], description=issue["description"],
                        suggestion=issue["suggestion"], evidence=redact(evidence)))
            report = s.scalar(select(ReportRow).where(ReportRow.run_id == task_id))
            if not report:
                path = None
                if self.artifact_dir:
                    directory = self.artifact_dir / task_id
                    directory.mkdir(parents=True, exist_ok=True)
                    temporary = directory / "report.tmp"
                    temporary.write_text(content, encoding="utf-8")
                    temporary.replace(directory / "report.html")
                    path = f"{task_id}/report.html"
                s.add(ReportRow(id=task_id, run_id=task_id, summary=metrics, html_path=path, html_content=content))
            self.track_report_stage(s, task_id, metrics)
            s.commit()

    def track_report_stage(self, session, task_id, metrics):
        task = session.get(TaskRow, task_id)
        for pending in session.scalars(select(WorkflowStepRow).where(WorkflowStepRow.task_id == task_id,
                                                                     WorkflowStepRow.status == "Running")):
            pending.status = "Completed" if task.status == "passed" else "Failed"
            pending.finished_at = now()
            if task.status != "passed":
                pending.output = {"error": (task.diagnosis or {}).get("summary", task.status)}
        row = session.scalar(select(WorkflowStepRow).where(WorkflowStepRow.task_id == task_id,
                                                         WorkflowStepRow.step_name == "Report Generator"))
        if not row:
            row = WorkflowStepRow(task_id=task_id, step_name="Report Generator")
            session.add(row)
        row.status, row.output = "Completed", metrics
        row.input = {"task_id": task_id}
        row.started_at = row.started_at or now()
        row.finished_at = now()

    def report_content(self, task_id):
        with Session(self.engine) as s:
            report = s.scalar(select(ReportRow).where(ReportRow.run_id == task_id))
            return report.html_content if report else None

    def reassign_task(self, task_id, project_id):
        with Session(self.engine) as s:
            row = s.get(TaskMetadataRow, task_id)
            if not row:
                raise KeyError(task_id)
            project = s.get(ProjectRow, project_id)
            if not project or project.archived:
                raise ValueError("Project not found or archived")
            row.project_id = project_id
            s.commit()
