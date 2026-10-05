import os
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOQA_", env_file=".env", extra="ignore",
                                     populate_by_name=True)
    database_url: str = Field(default="sqlite:///./data/autoqa.db", repr=False,
                              validation_alias=AliasChoices("DATABASE_URL", "AUTOQA_DATABASE_URL"))
    data_dir: Path = Path("data")
    artifact_dir: Path = Path("artifacts")
    allowed_origins: str = ""
    resource_origins: str = ""
    trusted_proxy_cidrs: str = ""
    headless: bool = True
    concurrency: int = Field(default=2, ge=1, le=8)
    api_token: str = Field(default="", repr=False)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = Field(default="", repr=False)
    llm_model: str = ""
    llm_provider: Literal["compatible", "openai", "deepseek", "mock"] = Field(
        default="compatible", validation_alias=AliasChoices("LLM_PROVIDER", "AUTOQA_LLM_PROVIDER"))
    provider_model: str = Field(default="", validation_alias="LLM_MODEL")
    openai_api_key: str = Field(default="", repr=False, validation_alias="OPENAI_API_KEY")
    deepseek_api_key: str = Field(default="", repr=False, validation_alias="DEEPSEEK_API_KEY")
    port: int = Field(default=8765, ge=1, le=65535, validation_alias="PORT")
    host: str = Field(default="0.0.0.0", validation_alias="HOST")
    demo_mode: bool | None = Field(default=None, validation_alias="DEMO_MODE")
    demo_daily_limit: int = Field(default=20, ge=0, le=1000, validation_alias="DEMO_DAILY_LIMIT")
    demo_ip_limit: int = Field(default=3, ge=0, le=100, validation_alias="DEMO_IP_LIMIT")
    demo_llm_enabled: bool = Field(default=False, validation_alias="DEMO_LLM_ENABLED")
    seed_demo: bool | None = None
    env: Literal["development", "production", "test"] = "development"
    mode: Literal["demo", "production"] | None = None
    secret_key: str = Field(default="", repr=False)
    allow_private_targets: bool = False
    runtime_secrets: dict[str, str] = Field(default_factory=dict, repr=False, exclude=True)
    runtime_headers: dict[str, str] = Field(default_factory=dict, repr=False, exclude=True)
    public_base_url: str = ""
    demo_login_fault: bool = True
    mysql_host: str = Field(default="", validation_alias="MYSQLHOST")
    mysql_port: int = Field(default=3306, validation_alias="MYSQLPORT")
    mysql_user: str = Field(default="", validation_alias="MYSQLUSER")
    mysql_password: str = Field(default="", repr=False, validation_alias="MYSQLPASSWORD")
    mysql_database: str = Field(default="", validation_alias="MYSQLDATABASE")
    mcp_enabled: bool = False
    mcp_command: str = "npx.cmd" if os.name == "nt" else "npx"
    mcp_args: list[str] = ["-y", "chrome-devtools-mcp@1.10.1", "--no-usage-statistics"]

    @property
    def model_enabled(self) -> bool:
        if self.mode == "production":
            return False  # Production resolves credentials exclusively through the workspace gateway.
        return self.llm_provider == "mock" or bool(self.provider_key and self.model_name)

    @property
    def provider_key(self) -> str:
        if self.mode == "production":
            return ""
        return {"openai": self.openai_api_key, "deepseek": self.deepseek_api_key}.get(
            self.llm_provider, self.llm_api_key)

    @property
    def model_name(self) -> str:
        return self.provider_model or self.llm_model

    @property
    def studio_url(self) -> str:
        return self.public_base_url.rstrip("/") or f"http://127.0.0.1:{self.port}"

    @model_validator(mode="after")
    def production_config(self):
        if self.mode == "production" and self.env == "production" and (
                "database_url" not in self.model_fields_set or not self.database_url):
            raise ValueError("Set an explicit DATABASE_URL for production")
        from ipaddress import ip_network
        for cidr in self.trusted_proxy_cidrs.split(","):
            if cidr.strip():
                ip_network(cidr.strip(), strict=False)
        if not self.database_url:
            if self.mysql_host:
                from sqlalchemy import URL
                self.database_url = URL.create("mysql+pymysql", username=self.mysql_user,
                    password=self.mysql_password, host=self.mysql_host, port=self.mysql_port,
                    database=self.mysql_database, query={"charset": "utf8mb4"}).render_as_string(hide_password=False)
            else:
                self.database_url = "sqlite:///./data/autoqa.db"
        if not self.public_base_url and os.getenv("RAILWAY_PUBLIC_DOMAIN"):
            self.public_base_url = "https://" + os.environ["RAILWAY_PUBLIC_DOMAIN"]
        if self.database_url.startswith("mysql://"):
            self.database_url = "mysql+pymysql://" + self.database_url[len("mysql://"):]
        if self.demo_mode is None:
            self.demo_mode = self.env == "production"
        if self.mode is not None:
            self.demo_mode = self.mode == "demo"
        if self.mode == "production":
            import base64
            try:
                if len(base64.b64decode(self.secret_key, altchars=b"-_", validate=True)) != 32:
                    raise ValueError
            except Exception:
                raise ValueError("AUTOQA_SECRET_KEY must be a URL-safe base64 encoded random 32-byte key") from None
            self.seed_demo = False
            self.mcp_enabled = False
        if self.seed_demo is None:
            self.seed_demo = bool(self.demo_mode)
        if self.env == "production":
            self.mcp_enabled = False
        if self.env == "production" and self.mode != "production" and not self.demo_mode and not self.api_token:
            raise ValueError("AUTOQA_API_TOKEN is required in production")
        if self.demo_mode:
            self.mcp_enabled = False
            if not self.demo_llm_enabled:
                self.llm_provider = "mock"
        from sqlalchemy.engine import make_url
        url = make_url(self.database_url)
        if url.drivername in {"mysql", "mysql+mysqlconnector", "mysql+mysqldb"}:
            url = url.set(drivername="mysql+pymysql")
        if url.drivername == "mysql+pymysql":
            url = url.update_query_dict({"charset": "utf8mb4"})
        elif not url.drivername.startswith("sqlite"):
            raise ValueError("Use a SQLite or MySQL DATABASE_URL")
        self.database_url = url.render_as_string(hide_password=False)
        return self
