"""Additive relational models beside legacy tasks/steps/events; no destructive migration."""
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from .store_base import Base, now


class ProjectRow(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("id", "workspace_id", name="uq_projects_id_workspace"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), nullable=True, index=True)
    project_type: Mapped[str] = mapped_column(String(16), default="web", server_default="web")
    environment: Mapped[str] = mapped_column(String(80), default="staging", server_default="staging")
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(2000))
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TaskMetadataRow(Base):
    __tablename__ = "task_metadata"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(400))
    test_type: Mapped[str] = mapped_column(String(24), default="regression")


class PlanRow(Base):
    __tablename__ = "test_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), unique=True)
    content: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="generated")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CaseRow(Base):
    __tablename__ = "test_cases"
    __table_args__ = (UniqueConstraint("task_id", "step_index"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    step_index: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(400))
    description: Mapped[str] = mapped_column(Text)
    expected_result: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="waiting")


class RunRow(Base):
    __tablename__ = "test_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    coverage: Mapped[float | None] = mapped_column(Float, nullable=True)


class FindingRow(Base):
    __tablename__ = "findings"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("test_runs.id"), index=True)
    severity: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(400))
    description: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON)


class ReportRow(Base):
    __tablename__ = "reports"
    __table_args__ = (CheckConstraint(
        "(run_id IS NOT NULL AND api_run_id IS NULL) OR (run_id IS NULL AND api_run_id IS NOT NULL)",
        name="ck_report_source"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("test_runs.id"), unique=True, nullable=True)
    api_run_id: Mapped[str | None] = mapped_column(ForeignKey("api_test_runs.id"), unique=True, nullable=True)
    analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[dict] = mapped_column(JSON)
    html_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    html_content: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), "mysql"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WorkflowStepRow(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (UniqueConstraint("task_id", "step_name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    step_name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), default="Waiting")
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SchemaVersionRow(Base):
    __tablename__ = "schema_versions"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
