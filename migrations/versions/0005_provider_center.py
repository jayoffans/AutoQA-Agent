"""Retain encrypted legacy records while allowing multiple workspace providers."""
import sqlalchemy as sa
from alembic import op

revision = "0005_provider_center"
down_revision = "0004_onboarding"
branch_labels = None
depends_on = None


def upgrade():
    # Copy ciphertext without decrypting it. Legacy ids equal workspace ids so the
    # existing provider:<workspace> associated-data envelope remains valid.
    op.create_table("provider_configs_next",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("model_name", sa.String(160), nullable=False),
        sa.Column("base_url", sa.String(2000), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("api_key_hint", sa.String(40), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active_workspace_id", sa.String(36), nullable=True),
        sa.Column("connection_status", sa.String(16), nullable=False, server_default="untested"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("active_workspace_id", name="uq_provider_active_workspace"),
        sa.CheckConstraint(
            "(is_active = 1 AND active_workspace_id IS NOT NULL AND active_workspace_id = workspace_id) "
            "OR (is_active = 0 AND active_workspace_id IS NULL)", name="ck_provider_active_workspace"))
    op.execute(sa.text("INSERT INTO provider_configs_next "
        "(id,workspace_id,provider,model_name,base_url,encrypted_api_key,api_key_hint,is_active,"
        "active_workspace_id,connection_status,created_at,updated_at) "
        "SELECT workspace_id,workspace_id,provider,model,base_url,encrypted_api_key,key_mask,1,"
        "workspace_id,'untested',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP FROM provider_configs"))
    op.drop_table("provider_configs")
    op.rename_table("provider_configs_next", "provider_configs")
    op.create_index("ix_provider_configs_workspace_id", "provider_configs", ["workspace_id"])


def downgrade():
    raise RuntimeError("Multiple encrypted configurations are retained; restore a verified backup for rollback")
