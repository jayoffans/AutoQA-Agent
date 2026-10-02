"""Reuse report storage for mutually exclusive legacy and API run snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0010_api_reports"
down_revision = "0009_execution_assertions"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("reports") as batch:
        batch.alter_column("run_id", existing_type=sa.String(36), nullable=True)
        batch.add_column(sa.Column("api_run_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("analysis", sa.JSON(), nullable=True))
        batch.create_foreign_key("fk_report_api_run", "api_test_runs", ["api_run_id"], ["id"])
        batch.create_unique_constraint("uq_report_api_run", ["api_run_id"])
        batch.create_check_constraint(
            "ck_report_source",
            "(run_id IS NOT NULL AND api_run_id IS NULL) OR (run_id IS NULL AND api_run_id IS NOT NULL)",
        )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM reports WHERE api_run_id IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Restore a pre-upgrade backup before downgrading API reports")
    with op.batch_alter_table("reports") as batch:
        batch.drop_constraint("ck_report_source", type_="check")
        batch.drop_constraint("fk_report_api_run", type_="foreignkey")
        batch.drop_constraint("uq_report_api_run", type_="unique")
        batch.drop_column("analysis")
        batch.drop_column("api_run_id")
        batch.alter_column("run_id", existing_type=sa.String(36), nullable=False)
