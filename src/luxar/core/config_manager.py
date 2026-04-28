from __future__ import annotations

import os
from pathlib import Path

from ruamel.yaml import YAML
_yaml = YAML(typ="safe")
from pydantic import BaseModel, Field


class AgentSection(BaseModel):
    name: str = "Luxar"
    version: str = "0.1.0"
    workspace: str = "./workspace/projects"
    driver_library: str = "./workspace/driver_library"
    skill_library: str = "./workspace/skill_library"
    firmware_library: str = "./workspace/firmware_library"


class LLMSection(BaseModel):
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    temperature: float = 0.2
    max_tokens: int = 4096
    thinking_enabled: bool = False
    thinking_effort: str = "medium"
    thinking_budget_tokens: int = 2048
    timeout_sec: int = 60
    base_url: str = ""
    api_key_env: str = ""
    retry_attempts: int = 3
    retry_min_delay: int = 2
    retry_max_delay: int = 30


class ReviewLayers(BaseModel):
    static_analysis: bool = True
    custom_rules: bool = True
    semantic_review: bool = True


class ReviewSection(BaseModel):
    enabled: bool = True
    layers: ReviewLayers = Field(default_factory=ReviewLayers)
    max_fix_iterations: int = 3
    fail_on_warning: bool = False


class PlatformSection(BaseModel):
    default_platform: str = "stm32cubemx"
    default_runtime: str = "baremetal"


class BuildSection(BaseModel):
    toolchain_prefix: str = "arm-none-eabi-"
    cmake_generator: str = "Ninja"
    jobs: int = 4


class ToolchainsSection(BaseModel):
    root: str = "./workspace/toolchains"
    cmake: str = ""
    openocd: str = ""
    arm_gcc: str = ""
    ninja: str = ""
    programmer_cli: str = ""


class STM32Section(BaseModel):
    project_mode: str = "cubemx"
    firmware_package: str = ""
    use_cubemx: bool = True


class FlashSection(BaseModel):
    default_probe: str = "stlink"
    openocd_interface: str = "interface/stlink.cfg"
    openocd_target: str = "target/stm32f1x.cfg"


class MonitorSection(BaseModel):
    default_baudrate: int = 115200
    default_timeout: int = 10


class GitSection(BaseModel):
    auto_commit: bool = True
    agent_branch_prefix: str = "agent/"


class EvolutionSection(BaseModel):
    enabled: bool = True
    auto_update_protocol_skill: bool = True
    require_project_success: bool = True
    min_review_passes: int = 1


class AgentConfig(BaseModel):
    agent: AgentSection = Field(default_factory=AgentSection)
    llm: LLMSection = Field(default_factory=LLMSection)
    review: ReviewSection = Field(default_factory=ReviewSection)
    platform: PlatformSection = Field(default_factory=PlatformSection)
    build: BuildSection = Field(default_factory=BuildSection)
    toolchains: ToolchainsSection = Field(default_factory=ToolchainsSection)
    stm32: STM32Section = Field(default_factory=STM32Section)
    flash: FlashSection = Field(default_factory=FlashSection)
    monitor: MonitorSection = Field(default_factory=MonitorSection)
    git: GitSection = Field(default_factory=GitSection)
    evolution: EvolutionSection = Field(default_factory=EvolutionSection)
    api_keys: dict[str, str] = Field(default_factory=dict)


def _discover_project_root() -> Path | None:
    current = Path(__file__).resolve()
    for candidate in current.parents:
        if (candidate / "pyproject.toml").exists() and (candidate / "src" / "luxar").exists():
            return candidate
    return None


class ConfigManager:
    def __init__(self, config_path: str | Path | None = None):
        self._project_root_override: Path | None = None
        if config_path is not None:
            self.config_path = Path(config_path).expanduser()
            root_override = os.getenv("LUXAR_ROOT", "").strip()
            if root_override:
                self._project_root_override = Path(root_override).expanduser().resolve()
        else:
            env_config = os.getenv("LUXAR_CONFIG", "").strip()
            env_root = os.getenv("LUXAR_ROOT", "").strip()
            if env_config:
                self.config_path = Path(env_config).expanduser()
                if env_root:
                    self._project_root_override = Path(env_root).expanduser().resolve()
            else:
                root = Path(env_root).expanduser().resolve() if env_root else _discover_project_root()
                if root is None:
                    raise RuntimeError(
                        "Unable to determine the Luxar project root. "
                        "Run Luxar from a source checkout, or set LUXAR_ROOT to your project root "
                        "or LUXAR_CONFIG to your config/luxar.yaml path."
                    )
                self._project_root_override = root
                self.config_path = root / "config" / "luxar.yaml"

    def load(self) -> AgentConfig:
        if not self.config_path.exists():
            return AgentConfig()
        with self.config_path.open("r", encoding="utf-8") as handle:
            data = _yaml.load(handle) or {}
        return AgentConfig.model_validate(data)

    def ensure_default_config(self) -> AgentConfig:
        config = self.load()
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            with self.config_path.open("w", encoding="utf-8") as handle:
                _yaml.dump(
                    config.model_dump(mode="json"),
                    handle,
                )
        return config

    def project_root(self) -> Path:
        if self._project_root_override is not None:
            return self._project_root_override
        return self.config_path.resolve().parent.parent

    def resolve_path(self, configured_path: str | Path) -> Path:
        path = Path(configured_path)
        if path.is_absolute():
            return path.resolve()
        return (self.project_root() / path).resolve()

    def workspace_root(self) -> Path:
        config = self.ensure_default_config()
        return self.resolve_path(config.agent.workspace)

    def driver_library_root(self) -> Path:
        config = self.ensure_default_config()
        return self.resolve_path(config.agent.driver_library)

    def skill_library_root(self) -> Path:
        config = self.ensure_default_config()
        return self.resolve_path(config.agent.skill_library)

    def firmware_library_root(self) -> Path:
        config = self.ensure_default_config()
        return self.resolve_path(config.agent.firmware_library)

    def toolchain_root(self) -> Path:
        config = self.ensure_default_config()
        return self.resolve_path(config.toolchains.root)


