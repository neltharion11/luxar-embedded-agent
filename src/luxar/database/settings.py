"""Environment-backed PostgreSQL settings without leaking credentials."""

from __future__ import annotations

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """LUXAR_DATABASE_* configuration shared by CLI and Web runtimes."""

    model_config = SettingsConfigDict(
        env_prefix="LUXAR_DATABASE_",
        extra="ignore",
    )

    url: SecretStr | None = None
    min_pool_size: int = Field(default=1, ge=1, le=20)
    max_pool_size: int = Field(default=5, ge=1, le=50)
    timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    auto_migrate: bool = True
    require_vector: bool = True

    @model_validator(mode="after")
    def validate_pool_bounds(self) -> "DatabaseSettings":
        if self.min_pool_size > self.max_pool_size:
            raise ValueError("数据库最小连接数不能超过最大连接数")
        return self

    @property
    def configured(self) -> bool:
        return self.url is not None and bool(
            self.url.get_secret_value().strip()
        )

    def connection_string(self) -> str:
        if not self.configured or self.url is None:
            raise ValueError("未配置 LUXAR_DATABASE_URL")
        return self.url.get_secret_value()
