"""Add opt-in account onboarding; existing accounts deliberately have no row."""
import sqlalchemy as sa
from alembic import op

revision = "0004_onboarding"
down_revision = "0003_auth"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("user_onboarding",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("step", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("model_configured", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("project_created", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("first_test_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("onboarding_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_user_onboarding_workspace_id", "user_onboarding", ["workspace_id"])


def downgrade():
    raise RuntimeError("Onboarding progress is retained; restore a verified backup for rollback")
