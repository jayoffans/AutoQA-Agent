"""Production identity and encrypted workspace configuration."""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, synonym

from .store_base import Base, now


class UserRow(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    nickname: Mapped[str] = mapped_column(String(60), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class WorkspaceRow(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    # Nullable only to retain historical orphan workspaces; registrations always assign an owner.
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class MemberRow(Base):
    __tablename__ = "workspace_members"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))
    # Retain the Phase 5 composite key and uniqueness contract.
    id: Mapped[str] = mapped_column(String(36), default=lambda: str(uuid4()), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SessionRow(Base):
    __tablename__ = "user_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OnboardingRow(Base):
    __tablename__ = "user_onboarding"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    step: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    model_configured: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    project_created: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    first_test_completed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class ProviderConfigRow(Base):
    __tablename__ = "provider_configs"
    __table_args__ = (CheckConstraint(
        "(is_active = 1 AND active_workspace_id IS NOT NULL AND active_workspace_id = workspace_id) "
        "OR (is_active = 0 AND active_workspace_id IS NULL)", name="ck_provider_active_workspace"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    provider: Mapped[str] = mapped_column(String(24))
    base_url: Mapped[str] = mapped_column(String(2000))
    model_name: Mapped[str] = mapped_column(String(160))
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    api_key_hint: Mapped[str] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # NULL for inactive rows; portable uniqueness on both MySQL and SQLite.
    active_workspace_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    connection_status: Mapped[str] = mapped_column(String(16), default="untested", server_default="untested")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    model = synonym("model_name")
    key_mask = synonym("api_key_hint")


class EnvironmentRow(Base):
    __tablename__ = "environment_configs"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    base_url: Mapped[str] = mapped_column(String(2000))
    headers: Mapped[dict] = mapped_column(JSON, default=dict)
    variables: Mapped[dict] = mapped_column(JSON, default=dict)
    encrypted_secrets: Mapped[str] = mapped_column(Text)


class ContractRow(Base):
    __tablename__ = "api_contracts"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    endpoints: Mapped[list] = mapped_column(JSON)


class InstallationRow(Base):
    __tablename__ = "installation_mode"
    id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(24))
