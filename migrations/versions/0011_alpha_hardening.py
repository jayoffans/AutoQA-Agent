"""Persist explicit Analyzer authorization without rewriting any historical facts."""

import sqlalchemy as sa
from alembic import op

revision = "0011_alpha_hardening"
down_revision = "0010_api_reports"
branch_labels = None
depends_on = None


def upgrade():
    # A unique index supports the scoped foreign key without recreating populated SQLite runs.
    op.create_index("uq_api_run_workspace", "api_test_runs", ["id", "workspace_id"], unique=True)
    op.create_table(
        "api_analysis_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("report_id", sa.String(36), sa.ForeignKey("reports.id"), nullable=False),
        sa.Column("selected_execution_ids", sa.JSON(), nullable=False),
        sa.Column("analyzed_execution_ids", sa.JSON(), nullable=False),
        sa.Column("refresh", sa.Boolean(), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("resolved_model", sa.String(160), nullable=True),
        sa.Column("provider_config_id", sa.String(36), nullable=True),
        sa.Column("provider_config_version", sa.String(64), nullable=False),
        sa.Column("action_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notice_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active_run_id", sa.String(36), nullable=True),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["api_test_runs.id", "api_test_runs.workspace_id"],
            name="fk_analysis_audit_run_scope",
        ),
        sa.UniqueConstraint("active_run_id", name="uq_analysis_audit_active_run"),
        sa.CheckConstraint(
            "status IN ('prepared','invoking','cached','succeeded','failed','unknown')",
            name="ck_analysis_audit_status",
        ),
        sa.CheckConstraint("request_count IS NULL OR request_count IN (0,1)", name="ck_analysis_audit_count"),
        sa.CheckConstraint(
            "active_run_id IS NULL OR (active_run_id = run_id AND status = 'invoking')",
            name="ck_analysis_audit_active",
        ),
        sa.CheckConstraint(
            "status != 'cached' OR (request_count IS NOT NULL AND request_count = 0)",
            name="ck_analysis_audit_cached",
        ),
        sa.CheckConstraint("status != 'unknown' OR request_count IS NULL", name="ck_analysis_audit_unknown"),
    )
    op.create_index("ix_api_analysis_audits_workspace_id", "api_analysis_audits", ["workspace_id"])
    op.create_index("ix_api_analysis_audits_run_id", "api_analysis_audits", ["run_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM api_analysis_audits LIMIT 1")).first():
        raise RuntimeError("Restore a pre-upgrade backup before downgrading Analyzer audit history")
    op.drop_table("api_analysis_audits")
    op.drop_index("uq_api_run_workspace", table_name="api_test_runs")
