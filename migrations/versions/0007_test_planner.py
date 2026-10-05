"""Add planning-only product tables; legacy Demo tables remain intact."""
import sqlalchemy as sa
from alembic import op

revision = "0007_test_planner"
down_revision = "0006_api_import"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("api_endpoints") as batch:
        batch.create_unique_constraint("uq_api_endpoints_workspace", ["id", "workspace_id"])
    op.create_table("ai_test_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("api_spec_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["api_spec_id", "project_id", "workspace_id"],
            ["api_specs.id", "api_specs.project_id", "api_specs.workspace_id"], name="fk_ai_plan_spec_scope"),
        sa.UniqueConstraint("id", "workspace_id", name="uq_ai_plan_workspace"),
        sa.CheckConstraint("status IN ('draft','generating','ready','failed','approved')", name="ck_ai_plan_status"),
    )
    op.create_table("ai_test_cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("plan_id", sa.String(36), nullable=False),
        sa.Column("endpoint_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("priority", sa.String(2), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("expected_result", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_id", "workspace_id"], ["ai_test_plans.id", "ai_test_plans.workspace_id"],
                                name="fk_ai_case_plan_workspace", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["endpoint_id", "workspace_id"], ["api_endpoints.id", "api_endpoints.workspace_id"],
                                name="fk_ai_case_endpoint_workspace"),
        sa.CheckConstraint("category IN ('positive','negative','boundary','security')", name="ck_ai_case_category"),
        sa.CheckConstraint("priority IN ('P0','P1','P2','P3')", name="ck_ai_case_priority"),
    )
    for table, columns in (("ai_test_plans", ("workspace_id", "project_id", "api_spec_id")),
                           ("ai_test_cases", ("workspace_id", "plan_id", "endpoint_id"))):
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    op.drop_table("ai_test_cases")
    op.drop_table("ai_test_plans")
    with op.batch_alter_table("api_endpoints") as batch:
        batch.drop_constraint("uq_api_endpoints_workspace", type_="unique")
