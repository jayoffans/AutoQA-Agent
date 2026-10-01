"""BYOK gateway: no environment credentials and no mock fallback in Production."""
from datetime import timezone
from types import SimpleNamespace

from sqlalchemy.orm import Session

from .crypto import mask_key
from .product_models import ProviderConfigRow, SessionRow
from .providers import CompatibleProvider, DeepSeekProvider, OpenAIProvider, ProviderError
from .store_base import now
from .target_network import PinnedTransport


class ProviderGateway:
    def __init__(self, engine, box, settings):
        self.engine, self.box, self.settings = engine, box, settings
        self.session_configs = {}  # Encrypted envelopes scoped to an authenticated session and workspace.
        self.transport_factory = lambda: PinnedTransport(settings.allow_private_targets)

    def scope(self, workspace):
        return "provider:" + workspace

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
            row = session.get(ProviderConfigRow, workspace)
            if row:
                return {k: getattr(row, k) for k in ("provider", "base_url", "model", "encrypted_api_key", "key_mask")}
        raise ProviderError("尚未配置模型服务")

    def public_config(self, workspace, session_hash):
        try:
            config = self.config(workspace, session_hash)
        except ProviderError:
            return {"configured": False}
        return {"configured": True, "provider": config["provider"], "base_url": config["base_url"],
                "model": config["model"], "key_mask": config["key_mask"],
                "save_mode": "session" if (session_hash, workspace) in self.session_configs else "workspace"}

    def save(self, workspace, session_hash, data):
        values = {"provider": data.provider, "base_url": data.base_url, "model": data.model,
                  "encrypted_api_key": self.box.encrypt(data.api_key, self.scope(workspace)),
                  "key_mask": mask_key(data.api_key)}
        if data.save_mode == "session":
            self.session_configs[(session_hash, workspace)] = values
        else:
            with Session(self.engine) as session:
                row = session.get(ProviderConfigRow, workspace)
                if not row:
                    session.add(ProviderConfigRow(workspace_id=workspace, **values))
                else:
                    for k, v in values.items():
                        setattr(row, k, v)
                session.commit()
            self.session_configs.pop((session_hash, workspace), None)
        return self.public_config(workspace, session_hash)

    async def generate(self, workspace, session_hash, system, context, schema, override=None):
        config = override or self.config(workspace, session_hash)
        if config["provider"] not in {"openai", "deepseek", "compatible"}:
            raise ProviderError("Production does not permit MockProvider")
        key = self.box.decrypt(config["encrypted_api_key"], self.scope(workspace))
        settings = SimpleNamespace(provider_key=key, model_name=config["model"], llm_base_url=config["base_url"])
        cls = {"openai": OpenAIProvider, "deepseek": DeepSeekProvider, "compatible": CompatibleProvider}[config["provider"]]
        # DeepSeek's public default is maintained while respecting a configured compatible endpoint.
        provider = cls(settings) if cls is DeepSeekProvider else cls(settings, self.transport_factory())
        if cls is DeepSeekProvider:
            provider.base, provider.transport = config["base_url"], self.transport_factory()
        try:
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
            provider.key = ""
            settings.provider_key = ""
            key = None
