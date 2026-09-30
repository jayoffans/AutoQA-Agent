import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOQA_", env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./data/autoqa.db"
    data_dir: Path = Path("data")
    artifact_dir: Path = Path("artifacts")
    allowed_origins: str = "http://127.0.0.1:8765,http://localhost:8765"
    resource_origins: str = ""
    headless: bool = True
    concurrency: int = Field(default=2, ge=1, le=8)
    api_token: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    mcp_enabled: bool = False
    mcp_command: str = "npx.cmd" if os.name == "nt" else "npx"
    mcp_args: list[str] = ["-y", "chrome-devtools-mcp@1.10.1", "--no-usage-statistics"]

    @property
    def model_enabled(self) -> bool:
        return bool(self.llm_api_key and self.llm_model)
