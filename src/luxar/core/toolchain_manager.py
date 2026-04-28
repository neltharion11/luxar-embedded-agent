from __future__ import annotations

import shutil
from pathlib import Path

from luxar.core.config_manager import AgentConfig


class ToolchainManager:
    def __init__(self, config: AgentConfig, project_root: str | Path):
        self.config = config
        self.project_root = Path(project_root).resolve()
        self.toolchains_root = (self.project_root / config.toolchains.root).resolve()

    def resolve_cmake(self) -> str | None:
        return self._resolve_binary(
            explicit=self.config.toolchains.cmake,
            bundled_relatives=["cmake/bin/cmake.exe", "cmake/bin/cmake"],
            fallback_name="cmake",
        )

    def resolve_openocd(self) -> str | None:
        return self._resolve_binary(
            explicit=self.config.toolchains.openocd,
            bundled_relatives=["openocd/bin/openocd.exe", "openocd/bin/openocd"],
            fallback_name="openocd",
        )

    def resolve_ninja(self) -> str | None:
        return self._resolve_binary(
            explicit=self.config.toolchains.ninja,
            bundled_relatives=[
                "ninja/bin/ninja.exe",
                "ninja/bin/ninja",
                "ninja/ninja.exe",
                "ninja/ninja",
            ],
            fallback_name="ninja",
        )

    def resolve_programmer_cli(self) -> str | None:
        return self._resolve_binary(
            explicit=self.config.toolchains.programmer_cli,
            bundled_relatives=[
                "programmer/bin/STM32_Programmer_CLI.exe",
                "programmer/bin/STM32_Programmer_CLI",
                "programmer/STM32_Programmer_CLI.exe",
                "programmer/STM32_Programmer_CLI",
            ],
            fallback_name="STM32_Programmer_CLI",
        )

    def resolve_arm_gcc(self) -> str | None:
        gcc_name = f"{self.config.build.toolchain_prefix}gcc"
        return self._resolve_binary(
            explicit=self.config.toolchains.arm_gcc,
            bundled_relatives=[
                f"gcc-arm/bin/{gcc_name}.exe",
                f"gcc-arm/bin/{gcc_name}",
            ],
            fallback_name=gcc_name,
        )

    def resolve_arm_gcc_bin_dir(self) -> str | None:
        gcc = self.resolve_arm_gcc()
        if not gcc:
            return None
        return str(Path(gcc).resolve().parent)

    def status(self) -> dict[str, str]:
        return {
            "toolchains_root": str(self.toolchains_root),
            "cmake": self.resolve_cmake() or "",
            "openocd": self.resolve_openocd() or "",
            "arm_gcc": self.resolve_arm_gcc() or "",
            "ninja": self.resolve_ninja() or "",
            "programmer_cli": self.resolve_programmer_cli() or "",
        }

    def _resolve_binary(
        self,
        explicit: str,
        bundled_relatives: list[str],
        fallback_name: str,
    ) -> str | None:
        if explicit:
            explicit_path = Path(explicit)
            if explicit_path.exists():
                return str(explicit_path.resolve())

        for relative in bundled_relatives:
            candidate = self.toolchains_root / relative
            if candidate.exists():
                return str(candidate.resolve())

        return shutil.which(fallback_name)


