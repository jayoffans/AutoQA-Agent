"""Add explicit API outcomes and assertion evidence without rewriting historical executions."""
import sqlalchemy as sa
from alembic import op

revision = "0009_execution_assertions"
down_revision = "0008_api_execution"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("api_test_executions") as batch:
        batch.add_column(sa.Column("status", sa.String(16), nullable=True))
        batch.add_column(sa.Column("result_data", sa.JSON(), nullable=True))
        batch.create_check_constraint("ck_api_execution_status",
                                      "status IS NULL OR status IN ('passed','failed','execution_error')")
        batch.create_check_constraint("ck_api_execution_passed",
            "status IS NULL OR (status = 'passed' AND passed = 1) OR "
            "(status IN ('failed','execution_error') AND passed = 0)")
    with op.batch_alter_table("api_test_runs") as batch:
        batch.drop_constraint("ck_api_run_status", type_="check")
        batch.create_check_constraint("ck_api_run_status",
                                      "status IN ('running','passed','failed','execution_error','interrupted')")


def downgrade():
    connection = op.get_bind()
    if (connection.execute(sa.text("SELECT 1 FROM api_test_runs WHERE status = 'execution_error' LIMIT 1")).first()
            or connection.execute(sa.text("SELECT 1 FROM api_test_executions WHERE status IS NOT NULL LIMIT 1")).first()):
        raise RuntimeError("Restore a pre-upgrade backup before downgrading executions with new evidence")
    with op.batch_alter_table("api_test_runs") as batch:
        batch.drop_constraint("ck_api_run_status", type_="check")
        batch.create_check_constraint("ck_api_run_status", "status IN ('running','passed','failed','interrupted')")
    with op.batch_alter_table("api_test_executions") as batch:
        batch.drop_constraint("ck_api_execution_passed", type_="check")
        batch.drop_constraint("ck_api_execution_status", type_="check")
        batch.drop_column("result_data")
        batch.drop_column("status")
