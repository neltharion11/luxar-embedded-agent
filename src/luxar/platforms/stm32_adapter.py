from __future__ import annotations

import shutil
import subprocess
import os
from pathlib import Path

from luxar.core.platform_adapter import PlatformAdapter
from luxar.core.toolchain_manager import ToolchainManager
from luxar.models.schemas import BuildResult, FlashResult, MonitorResult


class STM32CubeMXAdapter(PlatformAdapter):
    def __init__(
        self,
        toolchain_manager: ToolchainManager | None = None,
        openocd_interface: str = "interface/stlink.cfg",
        openocd_target: str = "target/stm32f1x.cfg",
    ):
        self.toolchain_manager = toolchain_manager
        self.openocd_interface = openocd_interface
        self.openocd_target = openocd_target

    def check_project_config(self, project_path: str) -> dict:
        project = Path(project_path)
        ioc_files = list(project.glob("*.ioc"))
        core_dir = project / "Core"
        drivers_dir = project / "Drivers"
        app_dir = project / "App"
        firmware_marker = project / "FIRMWARE_PACKAGE.txt"
        family_marker = project / "STM32_FAMILY.txt"
        cmsis_dir = project / "Drivers" / "CMSIS"
        hal_dirs = list((project / "Drivers").glob("STM32*HAL_Driver"))
        return {
            "valid": bool(ioc_files) or firmware_marker.exists(),
            "project_path": str(project),
            "ioc_files": [str(path) for path in ioc_files],
            "has_core_dir": core_dir.exists(),
            "has_drivers_dir": drivers_dir.exists(),
            "has_app_dir": app_dir.exists(),
            "has_firmware_marker": firmware_marker.exists(),
            "has_family_marker": family_marker.exists(),
            "has_cmsis_dir": cmsis_dir.exists(),
            "hal_driver_dirs": [str(path) for path in hal_dirs],
        }

    def build(self, project_path: str, clean: bool = False) -> BuildResult:
        project = Path(project_path)
        cmake_lists = project / "CMakeLists.txt"
        if not cmake_lists.exists():
            return BuildResult(
                success=False,
                command=[],
                return_code=-1,
                stderr="CMakeLists.txt not found. Run `agent assemble` first or provide a CubeMX-generated CMake project.",
                errors=["missing_cmakelists"],
            )

        cmake_bin = (
            self.toolchain_manager.resolve_cmake()
            if self.toolchain_manager is not None
            else shutil.which("cmake")
        )
        if cmake_bin is None:
            return BuildResult(
                success=False,
                command=[],
                return_code=-1,
                stderr="`cmake` is not available in bundled toolchains or PATH.",
                errors=["cmake_not_found"],
            )

        build_dir = project / "build"
        if clean and build_dir.exists():
            shutil.rmtree(build_dir)
        build_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        if self.toolchain_manager is not None:
            arm_gcc_bin = self.toolchain_manager.resolve_arm_gcc_bin_dir()
            if arm_gcc_bin:
                env["PATH"] = arm_gcc_bin + os.pathsep + env.get("PATH", "")

        configure_cmd = [cmake_bin, "-S", str(project), "-B", str(build_dir)]
        toolchain_file = project / "cmake" / "toolchain-arm-none-eabi.cmake"
        if toolchain_file.exists():
            configure_cmd.append(f"-DCMAKE_TOOLCHAIN_FILE={toolchain_file}")
            configure_cmd.append("-DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY")
        generator = None
        make_program = None
        if self.toolchain_manager is not None:
            generator = self.toolchain_manager.config.build.cmake_generator
            make_program = self.toolchain_manager.resolve_ninja() if generator == "Ninja" else None
        if generator:
            configure_cmd.extend(["-G", generator])
        if make_program:
            configure_cmd.append(f"-DCMAKE_MAKE_PROGRAM={make_program}")
        configure = subprocess.run(
            configure_cmd,
            capture_output=True,
            text=True,
            cwd=project,
            env=env,
        )
        if configure.returncode != 0:
            return BuildResult(
                success=False,
                command=configure_cmd,
                return_code=configure.returncode,
                stdout=configure.stdout,
                stderr=configure.stderr,
                errors=["cmake_configure_failed"],
            )

        build_cmd = [cmake_bin, "--build", str(build_dir)]
        build = subprocess.run(
            build_cmd,
            capture_output=True,
            text=True,
            cwd=project,
            env=env,
        )
        warnings = [
            line for line in (build.stdout + "\n" + build.stderr).splitlines()
            if "warning" in line.lower()
        ]
        errors = [
            line for line in build.stderr.splitlines()
            if "error" in line.lower()
        ]
        return BuildResult(
            success=(build.returncode == 0),
            command=build_cmd,
            return_code=build.returncode,
            stdout=build.stdout,
            stderr=build.stderr,
            warnings=warnings,
            errors=errors,
        )

    def flash(self, project_path: str, probe: str | None = None) -> FlashResult:
        project = Path(project_path)
        openocd_bin = (
            self.toolchain_manager.resolve_openocd()
            if self.toolchain_manager is not None
            else shutil.which("openocd")
        )
        programmer_cli = (
            self.toolchain_manager.resolve_programmer_cli()
            if self.toolchain_manager is not None
            else None
        )
        if openocd_bin is None and programmer_cli is None:
            return FlashResult(
                success=False,
                command=[],
                return_code=-1,
                stderr="Neither `openocd` nor `STM32_Programmer_CLI` is available in bundled toolchains or PATH.",
            )

        candidates = self._find_flash_artifacts(project)
        if not candidates:
            return FlashResult(
                success=False,
                command=[],
                return_code=-1,
                stderr="No build artifact found under build/. Run `luxar build` first.",
            )

        artifact = candidates[0]
        if programmer_cli is not None:
            flash_artifact = artifact
            temp_artifact: Path | None = None
            if artifact.suffix.lower() not in {".elf", ".bin", ".hex", ".srec", ".s19"}:
                temp_artifact = project / "build" / f"{artifact.name}.elf"
                shutil.copy2(artifact, temp_artifact)
                flash_artifact = temp_artifact
            connect_arg = self._build_programmer_connect_arg(probe)
            command = [programmer_cli, "-c", connect_arg, "-w", str(flash_artifact), "-v", "-rst"]
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    cwd=project,
                )
                return FlashResult(
                    success=(result.returncode == 0),
                    command=command,
                    return_code=result.returncode,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    artifact_path=str(flash_artifact),
                )
            finally:
                if temp_artifact is not None:
                    temp_artifact.unlink(missing_ok=True)
        else:
            command = [openocd_bin, "-f", self.openocd_interface, "-f", self.openocd_target]
            return FlashResult(
                success=False,
                command=command,
                return_code=-1,
                stderr="OpenOCD found, but programming command is not implemented yet.",
                artifact_path=str(artifact),
            )

    def monitor(self, project_path: str, **kwargs) -> MonitorResult:
        port = str(kwargs.get("port", ""))
        timeout = float(kwargs.get("timeout", 2))
        lines_to_read = int(kwargs.get("lines", 10))

        if not port:
            return MonitorResult(
                success=False,
                port="",
                error="Serial port is required.",
            )

        try:
            import serial
        except ImportError:
            return MonitorResult(
                success=False,
                port=port,
                error="pyserial is not installed.",
                port_released=True,
            )

        ser = None
        try:
            collected: list[str] = []
            ser = serial.Serial(
                port=port,
                baudrate=int(kwargs.get("baudrate", 115200)),
                timeout=timeout,
            )
            for _ in range(lines_to_read):
                raw = ser.readline()
                if not raw:
                    continue
                collected.append(raw.decode(errors="replace").rstrip())
            return MonitorResult(
                success=bool(collected),
                port=port,
                lines=collected,
                error="" if collected else "No serial data captured within timeout.",
                port_released=True,
            )
        except PermissionError:
            return MonitorResult(
                success=False,
                port=port,
                error=(
                    f"Serial port `{port}` is busy. Close any serial terminal, IDE monitor, "
                    "or prior debug session and retry. The agent releases the port when monitoring ends."
                ),
                port_released=True,
            )
        except Exception as exc:  # pragma: no cover - hardware dependent
            message = str(exc)
            lowered = message.lower()
            if "permissionerror" in lowered or "access is denied" in lowered or "拒绝访问" in message:
                message = (
                    f"Serial port `{port}` is busy. Close any serial terminal, IDE monitor, "
                    "or prior debug session and retry. The agent releases the port when monitoring ends."
                )
            return MonitorResult(
                success=False,
                port=port,
                error=message,
                port_released=True,
            )
        finally:
            if ser is not None and getattr(ser, "is_open", False):
                ser.close()

    def _find_flash_artifacts(self, project: Path) -> list[Path]:
        build_dir = project / "build"
        if not build_dir.exists():
            return []

        candidates = [
            path for path in (list(build_dir.glob("**/*.elf")) + list(build_dir.glob("**/*.bin")))
            if "CMakeFiles" not in path.parts and ".cmake" not in path.parts
        ]
        if candidates:
            return sorted(candidates)

        fallback: list[Path] = []
        ignored_names = {
            ".ninja_deps",
            ".ninja_log",
            "build.ninja",
            "cmake_install.cmake",
            "CMakeCache.txt",
        }
        for path in build_dir.iterdir():
            if not path.is_file():
                continue
            if path.name in ignored_names:
                continue
            if path.suffix:
                continue
            fallback.append(path)
        return sorted(fallback)

    def _build_programmer_connect_arg(self, probe: str | None) -> str:
        if not probe or probe.lower() == "stlink":
            return "port=SWD"
        normalized = probe.strip()
        if normalized.startswith("sn=") or normalized.startswith("index="):
            return f"port=SWD {normalized}"
        if normalized.isdigit():
            return f"port=SWD index={normalized}"
        return f"port=SWD sn={normalized}"


