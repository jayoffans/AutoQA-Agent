import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .schemas import TaskCreate
from .security import redact


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


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
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Store:
    def __init__(self, url: str):
        options = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, connect_args=options, pool_pre_ping=True)
        Base.metadata.create_all(self.engine)

    def create(self, request: TaskCreate) -> str:
        task_id = str(uuid4())
        with Session(self.engine) as s:
            s.add(TaskRow(id=task_id, status="queued", request=request.model_dump()))
            s.commit()
        self.event(task_id, "queued", {"message": "测试任务已加入队列"})
        return task_id

    def update(self, task_id: str, **changes):
        with Session(self.engine) as s:
            row = s.get(TaskRow, task_id)
            if row is None:
                raise KeyError(task_id)
            for key, value in changes.items():
                setattr(row, key, value)
            row.updated_at = now()
            s.commit()

    def event(self, task_id: str, kind: str, payload: dict):
        with Session(self.engine) as s:
            s.add(EventRow(task_id=task_id, kind=kind, payload=json.dumps(redact(payload), ensure_ascii=False)))
            s.commit()

    def step(self, task_id: str, index: int, attempt: int, result: dict):
        with Session(self.engine) as s:
            s.add(StepRow(task_id=task_id, index=index, attempt=attempt, result=redact(result)))
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
            result["steps"] = [dict(index=v.index, attempt=v.attempt, **v.result) for v in
                               s.scalars(select(StepRow).where(StepRow.task_id == task_id).order_by(StepRow.id))]
            return result if private else redact(result)

    def list(self, limit: int = 50):
        with Session(self.engine) as s:
            rows = s.scalars(select(TaskRow).order_by(TaskRow.created_at.desc()).limit(limit))
            return [{"id": r.id, "status": r.status, "goal": r.request["goal"],
                     "target_url": r.request["target_url"], "created_at": r.created_at.isoformat(),
                     "planner_mode": r.planner_mode} for r in rows]

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
