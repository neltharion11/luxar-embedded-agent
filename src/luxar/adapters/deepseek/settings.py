from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DeepSeekSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DEEPSEEK_",
        extra="ignore",
    )

    api_key: SecretStr
    base_url: str = "https://api.deepseek.com"
    fast_model: str = "deepseek-v4-flash"
    repair_model: str = "deepseek-v4-pro"
    timeout_seconds: float = Field(default=60.0, gt=0)