"""Workspace-bound OpenAPI input documents and parsed operations."""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import LONGTEXT

revision = "0006_api_import"
down_revision = "0005_provider_center"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("projects") as batch:
        batch.create_unique_constraint("uq_projects_id_workspace", ["id", "workspace_id"])
    op.create_table("api_specs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("raw_content", sa.Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id", "workspace_id"], ["projects.id", "projects.workspace_id"],
                                name="fk_api_specs_project_workspace"),
        sa.UniqueConstraint("id", "project_id", "workspace_id", name="uq_api_specs_scope"),
    )
    op.create_table("api_endpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("spec_id", sa.String(36), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("summary", sa.String(1000), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("request_schema", sa.JSON(), nullable=False),
        sa.Column("response_schema", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id", "workspace_id"], ["projects.id", "projects.workspace_id"],
                                name="fk_api_endpoints_project_workspace"),
        sa.ForeignKeyConstraint(["spec_id", "project_id", "workspace_id"],
                                ["api_specs.id", "api_specs.project_id", "api_specs.workspace_id"],
                                name="fk_api_endpoints_spec_scope", ondelete="CASCADE"),
    )
    for table, columns in (("api_specs", ("workspace_id", "project_id")),
                           ("api_endpoints", ("workspace_id", "project_id", "spec_id"))):
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    op.drop_table("api_endpoints")
    op.drop_table("api_specs")
    with op.batch_alter_table("projects") as batch:
        batch.drop_constraint("uq_projects_id_workspace", type_="unique")
