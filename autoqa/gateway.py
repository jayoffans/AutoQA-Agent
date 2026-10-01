"""BYOK gateway: no environment credentials and no mock fallback in Production."""
import asyncio
from datetime import timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .crypto import mask_key
from .product_models import OnboardingRow, ProviderConfigRow, SessionRow, WorkspaceRow
from .providers import CompatibleProvider, DeepSeekProvider, OpenAIProvider, ProviderError
from .store_base import now
from .target_network import PinnedTransport, fetch_limited

CONNECTION_TIMEOUT = 15


class ProviderGateway:
    def __init__(self, engine, box, settings):
        self.engine, self.box, self.settings = engine, box, settings
        self.session_configs = {}  # Encrypted envelopes scoped to an authenticated session and workspace.
        self.transport_factory = lambda: PinnedTransport(settings.allow_private_targets)

    def scope(self, workspace):
        return "provider:" + workspace

    def row_config(self, row):
        return {"id": row.id, "provider": row.provider, "base_url": row.base_url, "model": row.model_name,
                "encrypted_api_key": row.encrypted_api_key, "key_mask": row.api_key_hint,
                "secret_scope": self.scope(row.workspace_id) + (":" + row.id if row.id != row.workspace_id else "")}

    def public_row(self, row):
        return {"id": row.id, "provider": row.provider, "model": row.model_name, "base_url": row.base_url,
                "masked_key": row.api_key_hint.replace("•", "*"), "is_active": row.is_active,
                "status": row.connection_status, "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat()}

    def list_configs(self, workspace, session_hash=None):
        with Session(self.engine) as session:
            values = [self.public_row(row) for row in session.scalars(select(ProviderConfigRow).where(
                ProviderConfigRow.workspace_id == workspace).order_by(
                    ProviderConfigRow.is_active.desc(), ProviderConfigRow.created_at.desc(), ProviderConfigRow.id))]
        if (session_hash, workspace) in self.session_configs:
            config = self.config(workspace, session_hash)
            for value in values:
                value["is_active"] = False
            values.insert(0, {"id": "session", "provider": config["provider"], "model": config["model"],
                "base_url": config["base_url"], "masked_key": config["key_mask"].replace("•", "*"),
                "status": config.get("connection_status", "untested"), "is_active": True, "save_mode": "session"})
        return values

    def owned_row(self, session, workspace, config_id):
        row = session.scalar(select(ProviderConfigRow).where(
            ProviderConfigRow.id == config_id, ProviderConfigRow.workspace_id == workspace))
        if row is None:
            raise ProviderError("模型配置不存在")
        return row

    def lock_workspace(self, session, workspace):
        # A write before reading serializes mutations on SQLite as well as MySQL.
        session.execute(update(WorkspaceRow).where(WorkspaceRow.id == workspace)
                        .values(updated_at=WorkspaceRow.updated_at))

    def set_active(self, session, workspace, row):
        session.execute(update(ProviderConfigRow).where(ProviderConfigRow.workspace_id == workspace,
            ProviderConfigRow.is_active.is_(True)).values(is_active=False, active_workspace_id=None))
        session.flush()
        row.is_active, row.active_workspace_id = True, workspace

    def mark_onboarding(self, session, workspace, user_id, configured):
        session.execute(update(OnboardingRow).where(OnboardingRow.workspace_id == workspace,
            *([OnboardingRow.user_id == user_id] if user_id else [])).values(
                model_configured=configured, updated_at=now()))

    def create(self, workspace, session_hash, data, user_id):
        if data.save_mode == "session":
            result = self.save(workspace, session_hash, data)
            with Session(self.engine) as session:
                self.mark_onboarding(session, workspace, user_id, True)
                session.commit()
            return {"id": "session", "provider": result["provider"], "model": result["model"],
                    "base_url": result["base_url"], "masked_key": result["key_mask"].replace("•", "*"),
                    "status": "untested", "is_active": True, "save_mode": "session"}
        config_id = str(uuid4())
        envelope = self.box.encrypt(data.api_key, self.scope(workspace) + ":" + config_id)
        with Session(self.engine) as session:
            self.lock_workspace(session, workspace)
            row = ProviderConfigRow(id=config_id, workspace_id=workspace, provider=data.provider,
                model_name=data.model, base_url=data.base_url, encrypted_api_key=envelope,
                api_key_hint=mask_key(data.api_key).replace("•", "*"), is_active=False)
            self.set_active(session, workspace, row)
            session.add(row)
            self.mark_onboarding(session, workspace, user_id, True)
            session.commit()
            result = self.public_row(row)
        self.session_configs.pop((session_hash, workspace), None)
        return result

    def activate(self, workspace, config_id):
        with Session(self.engine) as session:
            self.lock_workspace(session, workspace)
            row = self.owned_row(session, workspace, config_id)
            self.set_active(session, workspace, row)
            session.commit()
            return self.public_row(row)

    def remove(self, workspace, config_id):
        with Session(self.engine) as session:
            self.lock_workspace(session, workspace)
            row = self.owned_row(session, workspace, config_id)
            was_active = row.is_active
            session.delete(row)
            session.flush()
            remaining = session.scalar(select(ProviderConfigRow).where(
                ProviderConfigRow.workspace_id == workspace).order_by(
                    ProviderConfigRow.created_at.desc(), ProviderConfigRow.id).limit(1))
            if was_active and remaining:
                remaining.is_active, remaining.active_workspace_id = True, workspace
            if remaining is None:
                self.mark_onboarding(session, workspace, None, False)
            session.commit()

    def config(self, workspace, session_hash):
        transient = self.session_configs.get((session_hash, workspace))
        if transient:
            with Session(self.engine) as session:
                saved = session.get(SessionRow, session_hash)
                if not saved or saved.expires_at.replace(tzinfo=timezone.utc) <= now():
                    self.session_configs.pop((session_hash, workspace), None)
                    raise ProviderError("会话模型配置已过期")
            return dict(transient)
        with Session(self.engine) as session:
            row = session.scalar(select(ProviderConfigRow).where(
                ProviderConfigRow.workspace_id == workspace, ProviderConfigRow.is_active.is_(True)))
            if row:
                return self.row_config(row)
        raise ProviderError("尚未配置模型服务")

    def public_config(self, workspace, session_hash):
        try:
            config = self.config(workspace, session_hash)
        except ProviderError:
            return {"configured": False}
        return {"configured": True, "provider": config["provider"], "base_url": config["base_url"],
                "model": config["model"], "key_mask": config["key_mask"].replace("*", "•"),
                "save_mode": "session" if (session_hash, workspace) in self.session_configs else "workspace"}

    def save(self, workspace, session_hash, data):
        values = {"provider": data.provider, "base_url": data.base_url, "model": data.model,
                  "encrypted_api_key": self.box.encrypt(data.api_key, self.scope(workspace)),
                  "key_mask": mask_key(data.api_key)}
        if data.save_mode == "session":
            values["secret_scope"] = self.scope(workspace) + ":session:" + session_hash
            values["encrypted_api_key"] = self.box.encrypt(data.api_key, values["secret_scope"])
            self.session_configs[(session_hash, workspace)] = values
        else:
            with Session(self.engine) as session:
                self.lock_workspace(session, workspace)
                row = session.scalar(select(ProviderConfigRow).where(
                    ProviderConfigRow.workspace_id == workspace, ProviderConfigRow.is_active.is_(True)))
                if not row:
                    row = ProviderConfigRow(id=workspace, workspace_id=workspace, is_active=True,
                        active_workspace_id=workspace, **values)
                    session.add(row)
                else:
                    values["encrypted_api_key"] = self.box.encrypt(data.api_key, self.row_config(row)["secret_scope"])
                    for k, v in values.items():
                        setattr(row, k, v)
                    row.connection_status = "untested"
                session.commit()
            self.session_configs.pop((session_hash, workspace), None)
        return self.public_config(workspace, session_hash)

    def remove_all(self, workspace):
        with Session(self.engine) as session:
            self.lock_workspace(session, workspace)
            session.execute(delete(ProviderConfigRow).where(ProviderConfigRow.workspace_id == workspace))
            self.mark_onboarding(session, workspace, None, False)
            session.commit()

    async def test_connection(self, workspace, session_hash, config_id=None, override=None):
        if override:
            config = override
        elif config_id and config_id != "session":
            with Session(self.engine) as session:
                config = self.row_config(self.owned_row(session, workspace, config_id))
        else:
            config = self.config(workspace, session_hash)
        key = None
        headers = {}
        response = None
        status = "failed"
        try:
            key = self.box.decrypt(config["encrypted_api_key"], config.get("secret_scope", self.scope(workspace)))
            headers["Authorization"] = "Bearer " + key
            async with asyncio.timeout(CONNECTION_TIMEOUT), httpx.AsyncClient(
                    timeout=CONNECTION_TIMEOUT, trust_env=False, follow_redirects=False,
                    transport=self.transport_factory()) as client:
                response = await fetch_limited(client, "POST", config["base_url"].rstrip("/") + "/chat/completions",
                    headers=headers, max_bytes=65536, json={"model": config["model"], "max_tokens": 1,
                        "messages": [{"role": "user", "content": "Hi"}]})
                if response.is_error:
                    raise ProviderError("连接失败，请检查服务地址、密钥、模型和余额")
                value = response.json()["choices"][0]["message"]["content"]
                if not isinstance(value, str):
                    raise ValueError
            status = "connected"
            return {"status": "success", "model": config["model"]}
        except Exception as error:
            request = getattr(error, "request", None)
            if request is not None:
                request.headers.pop("Authorization", None)
            # Provider error bodies/exceptions may echo the key. Never return or log them.
            raise ProviderError("连接失败，请检查服务地址、密钥、模型和余额") from None
        finally:
            if response is not None:
                response.request.headers.pop("Authorization", None)
            headers.clear()
            key = None
            if config_id and config_id != "session":
                with Session(self.engine) as session:
                    # A deleted/changed configuration must not acquire a stale test result.
                    session.execute(update(ProviderConfigRow).where(ProviderConfigRow.id == config_id,
                        ProviderConfigRow.workspace_id == workspace,
                        ProviderConfigRow.encrypted_api_key == config["encrypted_api_key"]).values(
                            connection_status=status, updated_at=now()))
                    session.commit()
            elif config_id == "session":
                transient = self.session_configs.get((session_hash, workspace))
                if transient and transient["encrypted_api_key"] == config["encrypted_api_key"]:
                    transient["connection_status"] = status

    async def generate(self, workspace, session_hash, system, context, schema, override=None):
        config = override or self.config(workspace, session_hash)
        if config["provider"] not in {"openai", "deepseek", "compatible"}:
            raise ProviderError("Production does not permit MockProvider")
        key = self.box.decrypt(config["encrypted_api_key"], config.get("secret_scope", self.scope(workspace)))
        settings = SimpleNamespace(provider_key=key, model_name=config["model"], llm_base_url=config["base_url"])
        cls = {"openai": OpenAIProvider, "deepseek": DeepSeekProvider, "compatible": CompatibleProvider}[config["provider"]]
        # DeepSeek's public default is maintained while respecting a configured compatible endpoint.
        provider = None
        try:
            provider = cls(settings) if cls is DeepSeekProvider else cls(settings, self.transport_factory())
            if cls is DeepSeekProvider:
                provider.base, provider.transport = config["base_url"], self.transport_factory()
            result = await provider.generate(system, context, schema)
            def hide(value):
                if isinstance(value, str):
                    return value.replace(key, "[REDACTED]")
                if isinstance(value, dict):
                    return {hide(k): hide(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [hide(v) for v in value]
                return value
            return hide(result)
        finally:
            if provider:
                provider.key = ""
            settings.provider_key = ""
            key = None
