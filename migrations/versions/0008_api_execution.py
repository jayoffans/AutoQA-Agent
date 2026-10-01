"""Add human review snapshots and API executions without changing legacy Demo runs."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import LONGTEXT

revision = "0008_api_execution"
down_revision = "0007_test_planner"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("ai_test_plans") as batch:
        batch.add_column(sa.Column("approved_base_url", sa.String(2000), nullable=True))
        batch.add_column(sa.Column("approved_by", sa.String(36), nullable=True))
        batch.add_column(sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key("fk_ai_plan_reviewer", "users", ["approved_by"], ["id"])
        batch.create_unique_constraint("uq_ai_plan_project_workspace", ["id", "project_id", "workspace_id"])
    with op.batch_alter_table("ai_test_cases") as batch:
        batch.add_column(sa.Column("execution_config", sa.JSON(), nullable=True))
        batch.create_unique_constraint("uq_ai_case_plan_workspace", ["id", "plan_id", "workspace_id"])
    op.create_table("api_test_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("plan_id", sa.String(36), nullable=False),
        sa.Column("active_plan_id", sa.String(36), unique=True, nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("total_cases", sa.Integer(), nullable=False),
        sa.Column("passed_cases", sa.Integer(), nullable=False),
        sa.Column("failed_cases", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.ForeignKeyConstraint(["plan_id", "project_id", "workspace_id"],
            ["ai_test_plans.id", "ai_test_plans.project_id", "ai_test_plans.workspace_id"], name="fk_api_run_plan_scope"),
        sa.UniqueConstraint("id", "plan_id", "workspace_id", name="uq_api_run_scope"),
        sa.CheckConstraint("status IN ('running','passed','failed','interrupted')", name="ck_api_run_status"),
        sa.CheckConstraint("total_cases >= 0 AND passed_cases >= 0 AND failed_cases >= 0 "
                           "AND passed_cases + failed_cases <= total_cases", name="ck_api_run_counts"),
    )
    op.create_table("api_test_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("plan_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("url", sa.String(4000), nullable=False),
        sa.Column("request_data", sa.JSON(), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text().with_variant(LONGTEXT(), "mysql"), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["run_id", "plan_id", "workspace_id"],
            ["api_test_runs.id", "api_test_runs.plan_id", "api_test_runs.workspace_id"], name="fk_api_execution_run_scope"),
        sa.ForeignKeyConstraint(["case_id", "plan_id", "workspace_id"],
            ["ai_test_cases.id", "ai_test_cases.plan_id", "ai_test_cases.workspace_id"], name="fk_api_execution_case_scope"),
        sa.UniqueConstraint("run_id", "case_id", name="uq_api_execution_case"),
        sa.CheckConstraint("duration_ms >= 0", name="ck_api_execution_duration"),
    )
    for table, columns in (("api_test_runs", ("workspace_id", "project_id", "plan_id")),
                           ("api_test_executions", ("workspace_id", "run_id", "case_id"))):
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    op.drop_table("api_test_executions")
    op.drop_table("api_test_runs")
    with op.batch_alter_table("ai_test_cases") as batch:
        batch.drop_constraint("uq_ai_case_plan_workspace", type_="unique")
        batch.drop_column("execution_config")
    with op.batch_alter_table("ai_test_plans") as batch:
        batch.drop_constraint("uq_ai_plan_project_workspace", type_="unique")
        batch.drop_constraint("fk_ai_plan_reviewer", type_="foreignkey")
        for column in ("approved_at", "approved_by", "approved_base_url"):
            batch.drop_column(column)
