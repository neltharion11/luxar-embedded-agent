"""DeepSeek 配置：从环境变量读取 API 地址、模型、超时和脱敏密钥。"""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DeepSeekSettings(BaseSettings):
    # BaseSettings 会用 DEEPSEEK_ 前缀和字段名自动寻找对应环境变量。
    model_config = SettingsConfigDict(
        env_prefix="DEEPSEEK_",
        extra="ignore",
    )

    # SecretStr 的 repr 只显示星号；传给 SDK 时才显式取出真实值。
    api_key: SecretStr
    base_url: str = "https://api.deepseek.com"
    fast_model: str = "deepseek-v4-flash"
    repair_model: str = "deepseek-v4-pro"
    # gt=0 表示超时必须严格大于 0，防止无效配置进入 HTTP 客户端。
    timeout_seconds: float = Field(default=60.0, gt=0)
    thinking_enabled: bool = False
    thinking_effort: str = "high"
