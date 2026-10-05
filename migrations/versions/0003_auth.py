"""Add account lifecycle, workspace ownership and rotating refresh-token hashes."""
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0003_auth"
down_revision = "0002_product"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("nickname", sa.String(60), nullable=False, server_default=""))
        batch.add_column(sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("workspaces") as batch:
        batch.add_column(sa.Column("owner_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_workspaces_owner_id", ["owner_id"])
        batch.create_foreign_key("fk_workspaces_owner", "users", ["owner_id"], ["id"])
    with op.batch_alter_table("workspace_members") as batch:
        batch.add_column(sa.Column("id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("user_sessions") as batch:
        batch.add_column(sa.Column("refresh_token_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_unique_constraint("uq_user_sessions_refresh_token_hash", ["refresh_token_hash"])

    connection = op.get_bind()
    users = sa.table("users", sa.column("id"), sa.column("email"), sa.column("nickname"),
                     sa.column("created_at"), sa.column("updated_at"))
    for user in connection.execute(sa.select(users.c.id, users.c.email)).all():
        connection.execute(users.update().where(users.c.id == user.id).values(nickname=user.email.split("@")[0][:60]))
    connection.execute(users.update().values(updated_at=users.c.created_at))
    members = sa.table("workspace_members", sa.column("workspace_id"), sa.column("user_id"),
                       sa.column("role"), sa.column("id"), sa.column("created_at"))
    workspaces = sa.table("workspaces", sa.column("id"), sa.column("owner_id"),
                          sa.column("created_at"), sa.column("updated_at"))
    connection.execute(workspaces.update().values(updated_at=workspaces.c.created_at))
    for member in connection.execute(sa.select(members).order_by(members.c.user_id)).all():
        created = connection.scalar(sa.select(workspaces.c.created_at).where(workspaces.c.id == member.workspace_id))
        connection.execute(members.update().where(members.c.workspace_id == member.workspace_id,
            members.c.user_id == member.user_id).values(id=str(uuid4()), created_at=created))
        if member.role == "owner":
            connection.execute(workspaces.update().where(workspaces.c.id == member.workspace_id,
                workspaces.c.owner_id.is_(None)).values(owner_id=member.user_id))
    connection.execute(sa.text("UPDATE user_sessions SET created_at = CURRENT_TIMESTAMP"))

    with op.batch_alter_table("users") as batch:
        batch.alter_column("updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    with op.batch_alter_table("workspaces") as batch:
        batch.alter_column("updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    with op.batch_alter_table("workspace_members") as batch:
        batch.alter_column("id", existing_type=sa.String(36), nullable=False)
        batch.alter_column("created_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.create_unique_constraint("uq_workspace_members_id", ["id"])
    with op.batch_alter_table("user_sessions") as batch:
        batch.alter_column("created_at", existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade():
    raise RuntimeError("Account data is retained; restore a verified backup for rollback")
