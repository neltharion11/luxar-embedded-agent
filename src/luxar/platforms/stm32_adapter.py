from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
import threading
import time

from luxar.core.toolchain_manager import ToolchainManager
from luxar.models.schemas import BuildResult, FlashResult, MonitorResult, ProbeResult
from luxar.core.platform_adapter import PlatformAdapter

NINJA_FATAL_RE = re.compile(r"ninja:\s*fatal:")


class BackgroundSerialMonitor:
    """Thread-safe background serial reader for flash-then-monitor flow."""

    def __init__(self, port: str, baudrate: int = 115200):
        self.port = port
        self.baudrate = baudrate
        self._lines: list[str] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._ser = None

    def start(self) -> None:
        import serial
        self._ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            timeout=0.1,
        )
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self._ser is not None and self._ser.is_open:
                    raw = self._ser.readline()
                    if raw:
                        line = raw.decode(errors="replace").rstrip()
                        with self._lock:
                            self._lines.append(line)
            except Exception:
                time.sleep(0.05)

    def stop(self, extra_wait: float = 1.5) -> list[str]:
        """Signal stop, wait for extra data, close port, return all collected lines."""
        self._stop_event.set()
        deadline = time.time() + extra_wait
        while time.time() < deadline:
            try:
                if self._ser is not None and self._ser.is_open:
                    raw = self._ser.readline()
                    if raw:
                        line = raw.decode(errors="replace").rstrip()
                        with self._lock:
                            self._lines.append(line)
            except Exception:
                break
            time.sleep(0.05)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._ser is not None and self._ser.is_open:
            try:
                self._ser.close()
            except Exception:
                pass
        with self._lock:
            return list(self._lines)

    def get_lines_so_far(self) -> list[str]:
        with self._lock:
            return list(self._lines)


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

        # ── Makefile fallback: if Makefile exists (no CMakeLists.txt), run make ──
        makefile = project / "Makefile"
        cmake_lists = project / "CMakeLists.txt"
        # PlatformIO: try if platformio.ini exists
        pio_ini = project / 'platformio.ini'
        if pio_ini.exists():
            pio_result = self._build_with_platformio(project, clean)
            if pio_result is not None:
                return pio_result

        if not cmake_lists.exists() and makefile.exists():
            return self._build_with_make(project, makefile, clean)

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

        # For CubeMX/CMakePresets projects, use cmake --preset
        preset_name = self._detect_cmake_preset(project)
        if preset_name is not None:
            env = os.environ.copy()
            if self.toolchain_manager is not None:
                arm_gcc_bin = self.toolchain_manager.resolve_arm_gcc_bin_dir()
                if arm_gcc_bin:
                    env["PATH"] = arm_gcc_bin + os.pathsep + env.get("PATH", "")
                ninja_bin = self.toolchain_manager.resolve_ninja()
                if ninja_bin:
                    env["PATH"] = str(Path(ninja_bin).parent) + os.pathsep + env.get("PATH", "")
            return self._build_with_preset(project, preset_name, clean, cmake_bin, env)

        build_dir = project / "build" / "Debug"
        if clean and build_dir.exists():
            shutil.rmtree(build_dir)
        build_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        gcc_path = None
        gxx_path = None
        asm_path = None
        if self.toolchain_manager is not None:
            arm_gcc_bin = self.toolchain_manager.resolve_arm_gcc_bin_dir()
            if arm_gcc_bin:
                env["PATH"] = arm_gcc_bin + os.pathsep + env.get("PATH", "")
            gcc_path = self.toolchain_manager.resolve_arm_gcc()
            gxx_path = self.toolchain_manager.resolve_arm_gxx()
            asm_path = self.toolchain_manager.resolve_arm_as()

        configure_cmd = [cmake_bin, "-S", str(project), "-B", str(build_dir)]
        toolchain_file = self._ensure_cmake_toolchain_file(
            project=project,
            build_dir=build_dir,
            gcc_path=gcc_path,
            gxx_path=gxx_path,
            asm_path=asm_path,
        )
        if toolchain_file is not None:
            self._reset_cmake_cache(build_dir)
            configure_cmd.append(f"-DCMAKE_TOOLCHAIN_FILE={toolchain_file}")
            configure_cmd.append("-DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY")
        configure_cmd.append("-DCMAKE_EXPORT_COMPILE_COMMANDS=ON")
        if gcc_path:
            configure_cmd.append(f"-DCMAKE_C_COMPILER={gcc_path}")
        if gxx_path:
            configure_cmd.append(f"-DCMAKE_CXX_COMPILER={gxx_path}")
        if asm_path:
            configure_cmd.append(f"-DCMAKE_ASM_COMPILER={asm_path}")
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
            text=True, encoding="utf-8", errors="replace",
            cwd=project,
            env=env,
        )
        if configure.returncode != 0:
            # Ninja compatibility fallback: retry by skipping the compiler
            # test (CMAKE_C_COMPILER_WORKS=1) which avoids Ninja I/O issues
            # during CMake's TryCompile on some Windows environments.
            if generator == "Ninja":
                fallback_cmd = list(configure_cmd)
                fallback_cmd.append("-DCMAKE_C_COMPILER_WORKS=1")
                configure = subprocess.run(
                    fallback_cmd,
                    capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
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
            text=True, encoding="utf-8", errors="replace",
            cwd=project,
            env=env,
        )
        if build.returncode != 0 and NINJA_FATAL_RE.search(build.stderr):
            # Ninja I/O failure on this platform; fall back to direct
            # compilation using compile_commands.json
            return self._build_fallback_direct(
                project=project,
                build_dir=build_dir,
                cmake_bin=cmake_bin,
                env=env,
            )
        warnings = [
            line for line in ((build.stdout or "") + "\n" + (build.stderr or "")).splitlines()
            if "warning" in line.lower()
        ]
        combined_build_output = (build.stdout or "") + "\n" + (build.stderr or "")
        errors = [
            line for line in combined_build_output.splitlines()
            if "error" in line.lower() or "fatal:" in line.lower() or "failed:" in line.lower()
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

    def _ensure_cmake_toolchain_file(
        self,
        project: Path,
        build_dir: Path,
        gcc_path: str | None,
        gxx_path: str | None,
        asm_path: str | None,
    ) -> Path | None:
        project_toolchain = project / "cmake" / "toolchain-arm-none-eabi.cmake"
        if project_toolchain.exists():
            return project_toolchain

        if not gcc_path:
            return None

        target_flags = self._resolve_target_flags(project)
        generated_toolchain = build_dir / "luxar-toolchain-arm-none-eabi.cmake"
        lines = [
            "set(CMAKE_SYSTEM_NAME Generic)",
            "set(CMAKE_SYSTEM_PROCESSOR arm)",
            f'set(CMAKE_C_COMPILER "{self._cmake_path(gcc_path)}")',
            f'set(CMAKE_ASM_COMPILER "{self._cmake_path(asm_path or gcc_path)}")',
            "set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)",
            'set(CMAKE_EXECUTABLE_SUFFIX ".elf")',
        ]
        if gxx_path:
            lines.append(f'set(CMAKE_CXX_COMPILER "{self._cmake_path(gxx_path)}")')
        if target_flags:
            lines.extend([
                f'set(LUXAR_TARGET_FLAGS "{target_flags}")',
                'set(CMAKE_C_FLAGS_INIT "${LUXAR_TARGET_FLAGS} -ffunction-sections -fdata-sections")',
                'set(CMAKE_CXX_FLAGS_INIT "${LUXAR_TARGET_FLAGS} -ffunction-sections -fdata-sections")',
                'set(CMAKE_ASM_FLAGS_INIT "${LUXAR_TARGET_FLAGS} -x assembler-with-cpp")',
                'set(CMAKE_EXE_LINKER_FLAGS_INIT "${LUXAR_TARGET_FLAGS} -Wl,--gc-sections")',
            ])
        generated_toolchain.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return generated_toolchain

    def _reset_cmake_cache(self, build_dir: Path) -> None:
        cache_file = build_dir / "CMakeCache.txt"
        if cache_file.exists():
            cache_file.unlink()
        cmake_files = build_dir / "CMakeFiles"
        if cmake_files.exists():
            shutil.rmtree(cmake_files)

    def _cmake_path(self, path: str) -> str:
        return Path(path).resolve().as_posix()

    def _resolve_target_flags(self, project: Path) -> str:
        cpu = self._resolve_target_cpu(project)
        if not cpu:
            return ""
        return f"-mcpu={cpu} -mthumb"

    def _resolve_target_cpu(self, project: Path) -> str:
        candidates = [
            self._read_project_mcu(project),
            self._read_ioc_mcu(project),
            self._read_cmake_mcu_hint(project),
        ]
        for candidate in candidates:
            cpu = self._map_stm32_to_cpu(candidate)
            if cpu:
                return cpu
        return ""

    def _read_project_mcu(self, project: Path) -> str:
        meta_file = project / ".agent_project.json"
        if not meta_file.exists():
            return ""
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            return ""
        return str(data.get("mcu", "") or "")

    def _read_ioc_mcu(self, project: Path) -> str:
        ioc_files = sorted(project.glob("*.ioc"))
        pattern = re.compile(r"^\s*ProjectManager\.DeviceId\s*=\s*(.+?)\s*$", re.MULTILINE)
        for ioc_file in ioc_files:
            try:
                text = ioc_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            match = pattern.search(text)
            if match:
                return match.group(1).strip()
        return ""

    def _read_cmake_mcu_hint(self, project: Path) -> str:
        cmake_file = project / "cmake" / "stm32cubemx" / "CMakeLists.txt"
        if not cmake_file.exists():
            return ""
        try:
            text = cmake_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
        match = re.search(r"STM32([A-Z0-9]+)", text)
        if not match:
            return ""
        return f"STM32{match.group(1)}"

    def _map_stm32_to_cpu(self, mcu_hint: str) -> str:
        normalized = mcu_hint.strip().upper().replace("-", "")
        if not normalized.startswith("STM32"):
            return ""
        family_match = re.match(r"STM32([A-Z0-9]+)", normalized)
        if not family_match:
            return ""
        family = family_match.group(1)
        family_to_cpu = {
            "F0": "cortex-m0",
            "F1": "cortex-m3",
            "F2": "cortex-m3",
            "F3": "cortex-m4",
            "F4": "cortex-m4",
            "F7": "cortex-m7",
            "G0": "cortex-m0plus",
            "G4": "cortex-m4",
            "H5": "cortex-m33",
            "H7": "cortex-m7",
            "L0": "cortex-m0plus",
            "L1": "cortex-m3",
            "L4": "cortex-m4",
            "L5": "cortex-m33",
            "U0": "cortex-m0plus",
            "U5": "cortex-m33",
            "WB": "cortex-m4",
            "WL": "cortex-m4",
            "C0": "cortex-m0plus",
            "MP1": "cortex-m4",
            "N6": "cortex-m55",
        }
        for prefix, cpu in family_to_cpu.items():
            if family.startswith(prefix):
                return cpu
        return ""

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
        # 1) Try probe-rs first (modern, cross-platform, no external deps)
        chip = self._detect_chip_from_build(project)
        probe_rs_result = self._flash_with_probe_rs(project, artifact, chip)
        if probe_rs_result is not None:
            return probe_rs_result

        # 2) Fallback: STM32_Programmer_CLI
        if programmer_cli is not None:
            probe_inventory = self._list_stlink_probes(programmer_cli, project)
            flash_artifact = artifact
            temp_artifact: Path | None = None
            if artifact.suffix.lower() not in {".elf", ".bin", ".hex", ".srec", ".s19"}:
                temp_artifact = project / "build" / f"{artifact.name}.elf"
                shutil.copy2(artifact, temp_artifact)
                flash_artifact = temp_artifact
            connect_arg = self._build_programmer_connect_arg(probe)
            command = [programmer_cli, "-c", connect_arg, "-w", str(flash_artifact)]
            if flash_artifact.suffix.lower() == ".bin":
                command.append("0x08000000")
            command.extend(["-v", "-rst"])
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=project,
                )
                return FlashResult(
                    success=(result.returncode == 0),
                    command=command,
                    return_code=result.returncode,
                    stdout=result.stdout or "",
                    stderr=self._augment_flash_stderr(
                        stderr=result.stderr or "",
                        probe_inventory=probe_inventory,
                    ),
                    artifact_path=str(flash_artifact),
                )
            finally:
                if temp_artifact is not None:
                    temp_artifact.unlink(missing_ok=True)
        # 3) Last resort: OpenOCD
        if openocd_bin is not None:
            command = [
                openocd_bin,
                "-f", self.openocd_interface,
                "-f", self.openocd_target,
                "-c", f"program {{{artifact}}} verify reset exit",
            ]
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=project,
                )
                return FlashResult(
                    success=(result.returncode == 0),
                    command=command,
                    return_code=result.returncode,
                    stdout=result.stdout or "",
                    stderr=result.stderr or "",
                    artifact_path=str(artifact),
                )
            except Exception as exc:
                return FlashResult(
                    success=False,
                    command=command,
                    return_code=-1,
                    stderr=str(exc),
                    artifact_path=str(artifact),
                )

        return FlashResult(
            success=False,
            command=[],
            return_code=-1,
            stderr="No flash tool available (probe-rs, STM32_Programmer_CLI, or OpenOCD).",
            artifact_path=str(artifact),
        )

    def monitor(self, project_path: str, **kwargs) -> MonitorResult:
        port = str(kwargs.get("port", ""))
        timeout = float(kwargs.get("timeout", 2))
        lines_to_read = int(kwargs.get("lines", 10))

        try:
            import serial
            from serial.tools import list_ports
        except ImportError:
            return MonitorResult(
                success=False,
                port=port,
                error="pyserial is not installed.",
                port_released=True,
            )

        if not port:
            port = self._auto_detect_serial_port(list_ports.comports())
            if not port:
                return MonitorResult(
                    success=False,
                    port="",
                    error="Serial port is required and no serial ports were detected.",
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

    def start_background_monitor(self, port: str, baudrate: int = 115200) -> None:
        """Open serial port in background thread, continuously reading into buffer."""
        self._bg_monitor = BackgroundSerialMonitor(port, baudrate)
        self._bg_monitor.start()

    def stop_background_monitor(self, extra_wait: float = 2.0) -> list[str]:
        """Stop background monitor, wait for trailing output, return all lines."""
        if self._bg_monitor is None:
            return []
        lines = self._bg_monitor.stop(extra_wait=extra_wait)
        self._bg_monitor = None
        return lines

    def _auto_detect_serial_port_for_monitor(self) -> str:
        """Auto-detect serial port for use with background monitor."""
        try:
            from serial.tools import list_ports
        except ImportError:
            return ""
        return self._auto_detect_serial_port(list_ports.comports())
    def probe(self, project_path: str, probe_type: str = "i2c") -> ProbeResult:

        project = Path(project_path)
        project_info = self.check_project_config(project_path)
        normalized = probe_type.strip().lower() or "i2c"
        alias_map = {
            "i2c": "i2c",
            "iic": "i2c",
            "spi": "spi",
            "uart": "uart",
            "usart": "uart",
        }
        interface = alias_map.get(normalized, normalized)
        if not project.exists():
            return ProbeResult(
                success=False,
                probe_type=probe_type,
                interface=interface,
                project_path=str(project),
                error=f"Project path does not exist: {project}",
            )
        if not project_info.get("valid", False):
            return ProbeResult(
                success=False,
                probe_type=probe_type,
                interface=interface,
                project_path=str(project),
                error="STM32 project configuration is incomplete. Expected a .ioc file or firmware marker.",
            )

        ioc_files = [Path(path) for path in project_info.get("ioc_files", [])]
        main_sources = [project / "Core" / "Src" / "main.c", project / "App" / "Src" / "app_main.c"]
        text_sources: list[tuple[str, str]] = []
        for path in ioc_files + main_sources:
            if not path.exists():
                continue
            try:
                text_sources.append((str(path), path.read_text(encoding="utf-8", errors="ignore")))
            except Exception:
                continue

        pattern_map = {
            "i2c": re.compile(r"\b(I2C\d+)\b", re.IGNORECASE),
            "spi": re.compile(r"\b(SPI\d+)\b", re.IGNORECASE),
            "uart": re.compile(r"\b((?:USART|UART)\d+)\b", re.IGNORECASE),
        }
        pattern = pattern_map.get(interface)
        if pattern is None:
            return ProbeResult(
                success=False,
                probe_type=probe_type,
                interface=interface,
                project_path=str(project),
                error=f"Unsupported probe type: {probe_type}",
            )

        detected_instances: list[str] = []
        evidence: list[dict[str, object]] = []
        for source_path, text in text_sources:
            matches = sorted({match.upper() for match in pattern.findall(text)})
            if not matches:
                continue
            detected_instances.extend(matches)
            evidence.append(
                {
                    "kind": "config_match",
                    "source": source_path,
                    "matches": matches,
                }
            )

        unique_instances = sorted(set(detected_instances))
        if not unique_instances:
            return ProbeResult(
                success=False,
                probe_type=probe_type,
                interface=interface,
                project_path=str(project),
                evidence=evidence,
                error=f"No {interface.upper()} configuration evidence was detected in the current STM32 project files.",
                summary=f"No {interface.upper()} configuration evidence found.",
            )

        return ProbeResult(
            success=True,
            probe_type=probe_type,
            interface=interface,
            project_path=str(project),
            detected_instances=unique_instances,
            evidence=evidence,
            summary=f"Detected {interface.upper()} configuration for {', '.join(unique_instances)}.",
        )


    def _flash_with_probe_rs(self, project: Path, artifact: Path, chip: str) -> FlashResult | None:
        import subprocess, shutil
        if self.toolchain_manager is None:
            return None
        probe_rs_bin = self.toolchain_manager.resolve_probe_rs()
        if probe_rs_bin is None:
            probe_rs_bin = shutil.which('probe-rs')
        if probe_rs_bin is None:
            return None
        cmd = [probe_rs_bin, 'run', '--chip', chip, str(artifact)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', cwd=project)
            return FlashResult(
                success=(result.returncode == 0),
                command=cmd,
                return_code=result.returncode,
                stdout=result.stdout or '',
                stderr=result.stderr or '',
                artifact_path=str(artifact),
            )
        except Exception as exc:
            import logging
            logging.getLogger('luxar').debug('probe-rs flash attempt failed: %s', exc)
            return None

    def _detect_cmake_preset(self, project: Path) -> str | None:
        presets_file = project / "CMakePresets.json"
        if not presets_file.exists():
            return None
        try:
            data = json.loads(presets_file.read_text(encoding="utf-8"))
        except Exception:
            return None
        configure_presets = data.get("configurePresets", [])
        for preset in configure_presets:
            if not preset.get("hidden", False):
                return preset.get("name")
        return None

    def _build_with_preset(
        self,
        project: Path,
        preset_name: str,
        clean: bool,
        cmake_bin: str,
        env: dict,
    ) -> BuildResult:
        # Use standard CMake preset build dir pattern: build/<presetName>
        build_path = project / "build" / preset_name
        if clean and build_path.exists():
            shutil.rmtree(build_path)
        build_path.mkdir(parents=True, exist_ok=True)

        configure_cmd = [cmake_bin, "--preset", preset_name, "-S", str(project)]
        configure = subprocess.run(
            configure_cmd,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
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

        build_cmd = [cmake_bin, "--build", "--preset", preset_name]
        build = subprocess.run(
            build_cmd,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            cwd=project,
            env=env,
        )
        warnings = [
            line for line in ((build.stdout or "") + "\n" + (build.stderr or "")).splitlines()
            if "warning" in line.lower()
        ]
        combined = (build.stdout or "") + "\n" + (build.stderr or "")
        errors = [
            line for line in combined.splitlines()
            if "error" in line.lower() or "fatal:" in line.lower() or "failed:" in line.lower()
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

    def _build_with_make(self, project: Path, makefile: Path, clean: bool) -> BuildResult:
        import shutil
        make_bin = shutil.which("make")
        if make_bin is None:
            # Try bundled toolchain
            if self.toolchain_manager is not None:
                toolchain_bin = self.toolchain_manager.resolve_arm_gcc_bin_dir()
                if toolchain_bin:
                    candidates = [
                        Path(toolchain_bin).parent / "bin" / "make.exe",
                        Path(toolchain_bin).parent / "make.exe",
                    ]
                    for c in candidates:
                        if c.exists():
                            make_bin = str(c)
                            break
        if make_bin is None:
            return BuildResult(
                success=False,
                command=[],
                return_code=-1,
                stderr="make not found in PATH or bundled toolchains.",
                errors=["make_not_found"],
            )

        env = os.environ.copy()
        if self.toolchain_manager is not None:
            arm_gcc_bin = self.toolchain_manager.resolve_arm_gcc_bin_dir()
            if arm_gcc_bin:
                env["PATH"] = arm_gcc_bin + os.pathsep + env.get("PATH", "")

        cmd = [make_bin, "-C", str(project)]
        if clean:
            cmd.append("clean")
        cmd.append("-j4")

        try:
            result = subprocess.run(
                cmd,
                cwd=str(project),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            success = result.returncode == 0
            return BuildResult(
                success=success,
                command=cmd,
                return_code=result.returncode,
                stdout=result.stdout.strip(),
                stderr=result.stderr.strip() if not success else "",
                errors=[] if success else ["make_failed"],
            )
        except Exception as exc:
            return BuildResult(
                success=False,
                command=cmd,
                return_code=-1,
                stderr=str(exc),
                errors=["make_exception"],
            )

    def _auto_detect_serial_port(self, ports) -> str:
        candidates = list(ports or [])
        if not candidates:
            return ""

        def score(port) -> tuple[int, str]:
            text = " ".join(
                str(getattr(port, attr, "") or "")
                for attr in ("device", "description", "hwid", "manufacturer", "product")
            ).lower()
            value = 0
            if any(token in text for token in ("usb-serial", "usb serial", "usb-enhanced-serial", "ch340", "ch341", "ch343", "cp210", "ftdi")):
                value += 30
            if any(token in text for token in ("uart", "serial", "com")):
                value += 10
            if any(token in text for token in ("stlink", "st-link")):
                value += 5
            return (-value, str(getattr(port, "device", "") or ""))

        best = sorted(candidates, key=score)[0]
        return str(getattr(best, "device", "") or "")

    def _find_flash_artifacts(self, project: Path) -> list[Path]:
        build_dir = project / "build" / "Debug"
        if not build_dir.exists():
            return []

        candidates = [
            path for path in (list(build_dir.glob("**/*.elf")) + list(build_dir.glob("**/*.bin")))
            if "CMakeFiles" not in path.parts and ".cmake" not in path.parts
        ]
        if candidates:
            return sorted(candidates, key=lambda p: (0 if p.suffix == '.elf' else 1, p.name))

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

    def _build_fallback_direct(
        self,
        *,
        project: Path,
        build_dir: Path,
        cmake_bin: str,
        env: dict[str, str],
    ) -> BuildResult:
        """Fall back to direct compilation when the cmake build tool (e.g. Ninja)
        fails due to I/O compatibility issues on this platform.

        Reads compile_commands.json to extract per-file build commands, executes
        them individually, then links the final executable.
        """
        compile_db = build_dir / "compile_commands.json"
        if not compile_db.exists():
            return BuildResult(
                success=False,
                command=[],
                return_code=-1,
                stderr="Ninja build failed and no compile_commands.json available for direct fallback.",
                errors=["ninja_fatal_unsupported"],
            )

        try:
            entries = json.loads(compile_db.read_text(encoding="utf-8"))
        except Exception:
            return BuildResult(
                success=False,
                command=[],
                return_code=-1,
                stderr="Failed to read compile_commands.json after Ninja build failure.",
                errors=["ninja_fatal_unsupported"],
            )

        warnings: list[str] = []
        errors: list[str] = []
        object_files: list[str] = []

        for entry in entries:
            src_file = entry.get("file", "")
            command_str = entry.get("command", "")
            if not src_file or not command_str:
                continue
            result = subprocess.run(
                command_str,
                shell=True,
                capture_output=True,
                text=True,
                errors="replace",
                cwd=entry.get("directory", str(project)) or str(project),
                env=env,
            )
            output = (result.stdout or "") + "\n" + (result.stderr or "")
            for line in output.splitlines():
                if "warning:" in line.lower():
                    warnings.append(line)
                if "error:" in line.lower() or "fatal:" in line.lower():
                    errors.append(line)
            if result.returncode == 0:
                # Extract object file from -o flag in the command
                obj_match = re.search(r"-o\s+(\S+)", command_str)
                if obj_match:
                    object_files.append(obj_match.group(1))
            else:
                return BuildResult(
                    success=False,
                    command=[command_str],
                    return_code=result.returncode,
                    stdout=result.stdout or "",
                    stderr=result.stderr or "",
                    warnings=warnings,
                    errors=errors or ["direct_compile_failed"],
                )

        if not object_files:
            return BuildResult(
                success=False,
                command=[],
                return_code=-1,
                stderr="No object files were produced by direct compilation fallback.",
                errors=["no_objects_generated"],
            )

        # Link all objects into the final elf
        elf_path = build_dir / "stm32_firmware_app.elf"
        linker_script = project / "cmake" / "stm32.ld"
        link_flags = "-mcpu=cortex-m3 -mthumb -specs=nosys.specs -specs=nano.specs -Wl,--gc-sections"
        if linker_script.exists():
            link_flags += f" -T{linker_script.resolve().as_posix()}"
        objects_str = " ".join(f'"{o}"' for o in object_files)
        # Use arm-none-eabi-gcc for linking
        gcc = shutil.which("arm-none-eabi-gcc") or "arm-none-eabi-gcc"
        link_cmd = f'"{gcc}" {link_flags} -o "{elf_path}" {objects_str}'
        link_result = subprocess.run(
            link_cmd,
            shell=True,
            capture_output=True,
            text=True,
            errors="replace",
            cwd=project,
            env=env,
        )
        combined = (link_result.stdout or "") + "\n" + (link_result.stderr or "")
        for line in combined.splitlines():
            if "warning:" in line.lower():
                warnings.append(line)
            if "error:" in line.lower() or "fatal:" in line.lower():
                errors.append(line)
        return BuildResult(
            success=link_result.returncode == 0,
            command=[link_cmd],
            return_code=link_result.returncode,
            stdout=link_result.stdout or "",
            stderr=link_result.stderr or "",
            warnings=warnings,
            errors=errors,
        )

    def _build_programmer_connect_arg(self, probe: str | None) -> str:
        if not probe or probe.lower() == "stlink":
            return "port=SWD"
        normalized = probe.strip()
        if normalized.startswith("sn=") or normalized.startswith("index="):
            return f"port=SWD {normalized}"
        if normalized.isdigit():
            return f"port=SWD index={normalized}"
        return f"port=SWD sn={normalized}"

    def _detect_chip_from_build(self, project: Path) -> str:
        import json
        meta = project.parent / project.name / '.agent_project.json'
        if meta.exists():
            try:
                data = json.loads(meta.read_text(encoding='utf-8'))
                mcu = data.get('mcu', '')
                if mcu:
                    return mcu
            except Exception:
                pass
        compile_commands = project / 'build' / 'compile_commands.json'
        if compile_commands.exists():
            try:
                data = json.loads(compile_commands.read_text(encoding='utf-8'))
                for entry in data:
                    cmd = entry.get('command', '')
                    if '-mcpu=' in cmd:
                        cpu = cmd.split('-mcpu=')[1].split()[0]
                        return 'STM32F103C8' if 'cortex-m3' in cpu else cpu
            except Exception:
                pass
        return 'STM32F103C8'

    def _build_with_platformio(self, project: Path, clean: bool) -> BuildResult | None:
        import subprocess, shutil
        if self.toolchain_manager is None:
            return None
        pio_bin = self.toolchain_manager.resolve_platformio()
        if pio_bin is None:
            pio_bin = shutil.which('platformio')
        if pio_bin is None:
            return None
        ini = project / 'platformio.ini'
        if not ini.exists():
            return None
        env = os.environ.copy()
        env = os.environ.copy()
        if clean:
            # Clean first
            clean_cmd = [pio_bin, 'run', '--project-dir', str(project), '--target', 'clean']
            try:
                subprocess.run(clean_cmd, capture_output=True, text=True,
                               encoding='utf-8', errors='replace', cwd=project, env=env)
            except Exception:
                pass
        # Build
        cmd = [pio_bin, 'run', '--project-dir', str(project)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', cwd=project, env=env)
            return BuildResult(
                success=(result.returncode == 0),
                command=cmd,
                return_code=result.returncode,
                stdout=result.stdout or '',
                stderr=result.stderr or '',
            )
        except Exception as exc:
            import logging
            logging.getLogger('luxar').debug('platformio build attempt failed: %s', exc)
            return None

    def _list_stlink_probes(self, programmer_cli: str, project: Path) -> str:
        try:
            result = subprocess.run(
                [programmer_cli, "-l", "stlink"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=project,
            )
        except Exception:
            return ""
        return "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part).strip()

    def _augment_flash_stderr(self, *, stderr: str, probe_inventory: str) -> str:
        lowered = stderr.lower()
        inventory_lowered = probe_inventory.lower()
        stlink_visible = "st-link" in inventory_lowered or "stlink" in inventory_lowered
        if "no debug probe detected" in lowered and stlink_visible:
            return (
                f"{stderr}\n"
                "Host-side ST-Link enumeration succeeded, but the target connection did not. "
                "This usually means the probe is visible in Device Manager while SWD to the MCU is failing. "
                "Check target power, SWDIO/SWCLK/GND wiring, NRST if needed, and whether another tool is holding the probe.\n"
                f"Probe inventory:\n{probe_inventory}"
            ).strip()
        if "cannot identify the device" in lowered and stlink_visible:
            return (
                f"{stderr}\n"
                "ST-Link is visible to STM32CubeProgrammer, but the target MCU could not be identified. "
                "This usually points to board power, SWD wiring, reset mode, or readout/protection state.\n"
                f"Probe inventory:\n{probe_inventory}"
            ).strip()
        return stderr