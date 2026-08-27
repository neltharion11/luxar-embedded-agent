"""Durable runtime configuration for OpenAI-compatible model endpoints."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr


ModelProvider = Literal["deepseek", "openai", "local"]
VisionMode = Literal["inherit", "separate", "python"]
EmbeddingMode = Literal["local_hash", "api"]
EmbeddingProvider = Literal["openai", "local"]


_MODEL_CONTEXT_WINDOWS: dict[tuple[str, str], int] = {
    ("deepseek", "deepseek-v4-flash"): 1_048_576,
    ("deepseek", "deepseek-v4-pro"): 1_048_576,
    # DeepSeek 官方兼容别名当前分别映射到 V4 Flash 的非思考/思考模式。
    ("deepseek", "deepseek-chat"): 1_048_576,
    ("deepseek", "deepseek-reasoner"): 1_048_576,
    ("openai", "gpt-4.1"): 1_047_576,
    ("openai", "gpt-4.1-mini"): 1_047_576,
    ("openai", "gpt-4.1-nano"): 1_047_576,
}


def model_context_window(
    provider: str,
    model: str,
    configured: int | None = None,
) -> int:
    """返回模型输入窗口；自定义值优先，未知模型使用保守回退。"""

    if configured is not None:
        return configured
    key = (provider.casefold(), model.strip().casefold())
    known = _MODEL_CONTEXT_WINDOWS.get(key)
    if known is not None:
        return known
    return 32_768 if provider == "local" else 128_000


def _provider_defaults(provider: ModelProvider) -> tuple[str, str]:
    if provider == "openai":
        return "https://api.openai.com/v1", "gpt-4.1-mini"
    if provider == "local":
        return "http://127.0.0.1:8000/v1", ""
    return "https://api.deepseek.com", "deepseek-v4-flash"


def is_local_http_api(base_url: str) -> bool:
    """Return whether *base_url* points at a loopback HTTP model service."""

    try:
        parsed = urlsplit(base_url.strip())
    except ValueError:
        return False
    return parsed.scheme.lower() == "http" and (
        parsed.hostname or ""
    ).casefold() in {"localhost", "127.0.0.1", "::1"}


def _can_skip_api_key(provider: str, base_url: str) -> bool:
    return provider == "local" or is_local_http_api(base_url)


class ModelEndpoint(BaseModel):
    """One OpenAI-compatible endpoint, regardless of where it is hosted."""

    model_config = ConfigDict(extra="forbid")

    provider: ModelProvider = "deepseek"
    api_key: SecretStr | None = None
    base_url: str = ""
    model: str = ""
    repair_model: str = ""
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    context_window_tokens: int | None = Field(
        default=None,
        ge=4_096,
        le=2_000_000,
    )

    def resolved(self) -> "ModelEndpoint":
        default_url, default_model = _provider_defaults(self.provider)
        resolved_model = self.model.strip() or default_model
        return self.model_copy(update={
            "base_url": self.base_url.strip() or default_url,
            "model": resolved_model,
            # 保留字段以读取旧配置，但新运行时统一使用同一个对话模型。
            "repair_model": resolved_model,
            "context_window_tokens": model_context_window(
                self.provider,
                resolved_model,
                self.context_window_tokens,
            ),
        })

    @property
    def fast_model(self) -> str:
        return self.resolved().model

    @property
    def configured(self) -> bool:
        endpoint = self.resolved()
        has_key = bool(
            endpoint.api_key is not None
            and endpoint.api_key.get_secret_value().strip()
        )
        return bool(endpoint.base_url and endpoint.model) and (
            has_key or _can_skip_api_key(endpoint.provider, endpoint.base_url)
        )

    @property
    def uses_local_execution(self) -> bool:
        """Whether model work should avoid concurrent requests."""

        endpoint = self.resolved()
        return endpoint.provider == "local" or is_local_http_api(endpoint.base_url)

    def sdk_api_key(self) -> str:
        if self.api_key is not None and self.api_key.get_secret_value().strip():
            return self.api_key.get_secret_value().strip()
        if _can_skip_api_key(self.provider, self.resolved().base_url):
            return "local"
        raise ValueError(f"{self.provider} API Key 尚未配置")


class EmbeddingConfig(BaseModel):
    """Project-RAG embedding is independent from chat and vision models."""

    model_config = ConfigDict(extra="forbid")

    mode: EmbeddingMode = "local_hash"
    provider: EmbeddingProvider = "local"
    api_key: SecretStr | None = None
    base_url: str = "http://127.0.0.1:8082/v1"
    model: str = ""
    dimensions: int = Field(default=384, ge=32, le=4096)
    timeout_seconds: float = Field(default=30.0, gt=0, le=600)

    @property
    def configured(self) -> bool:
        if self.mode == "local_hash":
            return True
        has_key = bool(
            self.api_key is not None and self.api_key.get_secret_value().strip()
        )
        return bool(self.base_url.strip() and self.model.strip()) and (
            has_key or _can_skip_api_key(self.provider, self.base_url)
        )

    def sdk_api_key(self) -> str:
        if self.api_key is not None and self.api_key.get_secret_value().strip():
            return self.api_key.get_secret_value().strip()
        if _can_skip_api_key(self.provider, self.base_url):
            return "local"
        raise ValueError("Embedding API Key 尚未配置")


class RuntimeModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation: ModelEndpoint = Field(default_factory=ModelEndpoint)
    vision_mode: VisionMode = "python"
    vision: ModelEndpoint | None = None
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)

    def vision_endpoint(self) -> ModelEndpoint | None:
        if self.vision_mode == "python":
            return None
        endpoint = self.conversation if self.vision_mode == "inherit" else self.vision
        if endpoint is None or not endpoint.configured:
            return None
        return endpoint.resolved()

    def public_dict(self) -> dict[str, object]:
        def expose(endpoint: ModelEndpoint | None) -> dict[str, object] | None:
            if endpoint is None:
                return None
            resolved = endpoint.resolved()
            return {
                "provider": resolved.provider,
                "base_url": resolved.base_url,
                "model": resolved.model,
                "timeout_seconds": resolved.timeout_seconds,
                "context_window_tokens": resolved.context_window_tokens,
                "context_compaction_threshold": 0.95,
                "api_key_configured": bool(
                    resolved.api_key
                    and resolved.api_key.get_secret_value().strip()
                ),
            }

        return {
            "conversation": expose(self.conversation),
            "vision_mode": self.vision_mode,
            "vision": expose(self.vision),
            "pdf_fallback": "pymupdf",
            "embedding": {
                "mode": self.embedding.mode,
                "provider": self.embedding.provider,
                "base_url": self.embedding.base_url,
                "model": self.embedding.model,
                "dimensions": self.embedding.dimensions,
                "timeout_seconds": self.embedding.timeout_seconds,
                "api_key_configured": bool(
                    self.embedding.api_key
                    and self.embedding.api_key.get_secret_value().strip()
                ),
                "configured": self.embedding.configured,
            },
        }


class ModelConfigStore:
    """Save model settings locally and keep secrets out of read responses."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()

    def load(self) -> RuntimeModelConfig:
        if self.path.is_file():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return RuntimeModelConfig.model_validate(payload)
        return self._from_environment()

    def save(self, config: RuntimeModelConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        def persist_endpoint(endpoint: ModelEndpoint | None) -> dict[str, object] | None:
            if endpoint is None:
                return None
            return {
                "provider": endpoint.provider,
                "api_key": (
                    endpoint.api_key.get_secret_value()
                    if endpoint.api_key is not None
                    else None
                ),
                "base_url": endpoint.base_url,
                "model": endpoint.model,
                "repair_model": endpoint.repair_model,
                "timeout_seconds": endpoint.timeout_seconds,
                "context_window_tokens": endpoint.context_window_tokens,
            }
        payload = {
            "conversation": persist_endpoint(config.conversation),
            "vision_mode": config.vision_mode,
            "vision": persist_endpoint(config.vision),
            "embedding": {
                "mode": config.embedding.mode,
                "provider": config.embedding.provider,
                "api_key": (
                    config.embedding.api_key.get_secret_value()
                    if config.embedding.api_key is not None
                    else None
                ),
                "base_url": config.embedding.base_url,
                "model": config.embedding.model,
                "dimensions": config.embedding.dimensions,
                "timeout_seconds": config.embedding.timeout_seconds,
            },
        }
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _from_environment() -> RuntimeModelConfig:
        provider = os.environ.get("LUXAR_LLM_PROVIDER", "").strip().lower()
        if provider not in {"deepseek", "openai", "local"}:
            configured_url = os.environ.get("LUXAR_LLM_BASE_URL", "")
            provider = (
                "local"
                if is_local_http_api(configured_url)
                else "openai" if os.environ.get("OPENAI_API_KEY") else "deepseek"
            )
        typed_provider: ModelProvider = provider  # type: ignore[assignment]
        default_url, default_model = _provider_defaults(typed_provider)
        provider_key = (
            os.environ.get("OPENAI_API_KEY")
            if typed_provider == "openai"
            else os.environ.get("DEEPSEEK_API_KEY")
            if typed_provider == "deepseek"
            else None
        )
        conversation = ModelEndpoint(
            provider=typed_provider,
            api_key=os.environ.get("LUXAR_LLM_API_KEY") or provider_key,
            base_url=os.environ.get("LUXAR_LLM_BASE_URL", default_url),
            model=os.environ.get("LUXAR_LLM_MODEL", default_model),
            repair_model=os.environ.get("LUXAR_LLM_MODEL", default_model),
            timeout_seconds=float(os.environ.get("LUXAR_LLM_TIMEOUT_SECONDS", "60")),
            context_window_tokens=(
                int(os.environ["LUXAR_LLM_CONTEXT_WINDOW_TOKENS"])
                if os.environ.get("LUXAR_LLM_CONTEXT_WINDOW_TOKENS")
                else None
            ),
        )
        vision_key = os.environ.get("LUXAR_DOCUMENT_VISION_API_KEY")
        vision_model = os.environ.get("LUXAR_DOCUMENT_VISION_MODEL", "")
        raw_mode = os.environ.get("LUXAR_DOCUMENT_VISION_MODE", "").strip().lower()
        vision_mode: VisionMode = (
            raw_mode  # type: ignore[assignment]
            if raw_mode in {"inherit", "separate", "python"}
            else "separate"
            if vision_model and (
                vision_key
                or is_local_http_api(
                    os.environ.get(
                        "LUXAR_DOCUMENT_VISION_BASE_URL", "https://api.openai.com/v1"
                    )
                )
            )
            else "python"
        )
        vision = None
        if vision_key or vision_model:
            vision = ModelEndpoint(
                provider="openai",
                api_key=vision_key,
                base_url=os.environ.get(
                    "LUXAR_DOCUMENT_VISION_BASE_URL", "https://api.openai.com/v1"
                ),
                model=vision_model,
                repair_model=vision_model,
            )
        embedding_key = os.environ.get("LUXAR_EMBEDDING_API_KEY")
        embedding_base_url = os.environ.get(
            "LUXAR_EMBEDDING_BASE_URL", "https://api.openai.com/v1"
        )
        configured_embedding_model = os.environ.get(
            "LUXAR_EMBEDDING_MODEL", ""
        ).strip()
        embedding_model = configured_embedding_model or (
            "text-embedding-3-small" if embedding_key else ""
        )
        raw_embedding_mode = os.environ.get("LUXAR_EMBEDDING_MODE", "").strip()
        embedding_mode: EmbeddingMode = (
            raw_embedding_mode  # type: ignore[assignment]
            if raw_embedding_mode in {"local_hash", "api"}
            else "api"
            if embedding_key
            or (
                is_local_http_api(embedding_base_url)
                and configured_embedding_model
            )
            else "local_hash"
        )
        raw_embedding_provider = os.environ.get(
            "LUXAR_EMBEDDING_PROVIDER", "openai" if embedding_key else "local"
        ).strip()
        embedding_provider: EmbeddingProvider = (
            raw_embedding_provider  # type: ignore[assignment]
            if raw_embedding_provider in {"openai", "local"}
            else "local"
        )
        embedding = EmbeddingConfig(
            mode=embedding_mode,
            provider=embedding_provider,
            api_key=embedding_key,
            base_url=embedding_base_url,
            model=embedding_model,
            dimensions=int(os.environ.get(
                "LUXAR_EMBEDDING_DIMENSIONS",
                "1536" if embedding_mode == "api" else "384",
            )),
        )
        return RuntimeModelConfig(
            conversation=conversation,
            vision_mode=vision_mode,
            vision=vision,
            embedding=embedding,
        )
