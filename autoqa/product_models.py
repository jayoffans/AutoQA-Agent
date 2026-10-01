"""Production identity and encrypted workspace configuration."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .store_base import Base, now


class UserRow(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WorkspaceRow(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MemberRow(Base):
    __tablename__ = "workspace_members"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))


class SessionRow(Base):
    __tablename__ = "user_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProviderConfigRow(Base):
    __tablename__ = "provider_configs"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    provider: Mapped[str] = mapped_column(String(24))
    base_url: Mapped[str] = mapped_column(String(2000))
    model: Mapped[str] = mapped_column(String(160))
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    key_mask: Mapped[str] = mapped_column(String(40))


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
