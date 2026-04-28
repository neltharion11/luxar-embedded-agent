from __future__ import annotations

import json
import re
from pathlib import Path

from luxar.core.assembler import Assembler
from luxar.core.backup_manager import BackupManager
from luxar.core.build_system import BuildSystem
from luxar.core.code_fixer import CodeFixer
from luxar.core.config_manager import AgentConfig
from luxar.core.flash_system import FlashSystem
from luxar.core.lock_manager import ProjectLock
from luxar.core.logger import AgentLogger
from luxar.core.review_engine import ReviewEngine
from luxar.core.toolchain_manager import ToolchainManager
from luxar.core.uart_monitor import UartMonitor
from luxar.models.schemas import DebugLoopResult, MonitorResult, ProjectConfig, ReviewIssue, ReviewReport
from luxar.platforms.stm32_adapter import STM32CubeMXAdapter


class DebugLoop:
    def __init__(self, config: AgentConfig, project_root: str):
        self.config = config
        self.project_root = Path(project_root)

    def _resolve_project(self, project_path: str) -> Path:
        return Path(project_path).resolve()

    def run(
        self,
        project_path: str,
        probe: str | None = None,
        port: str = "",
        clean: bool = False,
        lines: int = 10,
        baudrate: int | None = None,
    ) -> DebugLoopResult:
        project = self._resolve_project(project_path)
        context = self._create_context(project)

        with ProjectLock(str(project)):
            build_result = self._run_build(context, clean=clean)
            if not build_result.success:
                return DebugLoopResult(
                    success=False,
                    stage="build",
                    diagnosis=self._diagnose_build_failure(build_result.stderr),
                    build_result=build_result,
                    snapshot_path=context["snapshot_path"],
                    log_dir=context["log_dir"],
                )

            flash_result = self._run_flash(
                context,
                probe=probe or self.config.flash.default_probe,
            )
            if not flash_result.success:
                return DebugLoopResult(
                    success=False,
                    stage="flash",
                    diagnosis=self._diagnose_flash_failure(
                        flash_result.stdout,
                        flash_result.stderr,
                    ),
                    build_result=build_result,
                    flash_result=flash_result,
                    snapshot_path=context["snapshot_path"],
                    log_dir=context["log_dir"],
                )

            monitor_result = self._run_monitor(
                context,
                port=port,
                baudrate=baudrate or self.config.monitor.default_baudrate,
                timeout=self.config.monitor.default_timeout,
                lines=lines,
            )

            diagnosis = self._diagnose_monitor_result(monitor_result)
            success = monitor_result.success and bool(monitor_result.lines)
            stage = "complete" if success else "monitor"
            context["logger"].log_event(
                "DEBUG_LOOP",
                project.name,
                {
                    "success": success,
                    "stage": stage,
                    "diagnosis": diagnosis,
                    "snapshot_path": context["snapshot_path"],
                },
            )
            return DebugLoopResult(
                success=success,
                stage=stage,
                diagnosis=diagnosis,
                build_result=build_result,
                flash_result=flash_result,
                monitor_result=monitor_result,
                snapshot_path=context["snapshot_path"],
                log_dir=context["log_dir"],
            )

    def _create_context(self, project: Path) -> dict:
        logger = AgentLogger(str(project / "logs"))
        backup_manager = BackupManager(str(project))
        snapshot = backup_manager.create_snapshot("debug_loop")
        toolchain_manager = ToolchainManager(
            config=self.config,
            project_root=self.project_root,
        )
        adapter = STM32CubeMXAdapter(
            toolchain_manager=toolchain_manager,
            openocd_interface=self.config.flash.openocd_interface,
            openocd_target=self.config.flash.openocd_target,
        )
        return {
            "project": project,
            "logger": logger,
            "snapshot_path": str(snapshot),
            "log_dir": str(project / "logs"),
            "build_system": BuildSystem(adapter),
            "flash_system": FlashSystem(adapter),
            "uart_monitor": UartMonitor(adapter),
        }

    def _run_build(self, context: dict, clean: bool = False):
        project = context["project"]
        build_result = context["build_system"].build_project(str(project), clean=clean)
        context["logger"].log_event(
            "BUILD",
            project.name,
            {
                "success": build_result.success,
                "return_code": build_result.return_code,
                "errors": build_result.errors,
                "warnings": build_result.warnings,
            },
        )
        return build_result

    def _run_flash(self, context: dict, probe: str | None = None):
        project = context["project"]
        flash_result = context["flash_system"].flash_project(
            str(project),
            probe=probe,
        )
        context["logger"].log_event(
            "FLASH",
            project.name,
            {
                "success": flash_result.success,
                "return_code": flash_result.return_code,
                "artifact_path": flash_result.artifact_path,
            },
        )
        return flash_result

    def _run_monitor(self, context: dict, **kwargs):
        project = context["project"]
        monitor_result = context["uart_monitor"].monitor_project(str(project), **kwargs)
        context["logger"].log_event(
            "UART_MONITOR",
            project.name,
            {
                "success": monitor_result.success,
                "port": monitor_result.port,
                "line_count": len(monitor_result.lines),
                "error": monitor_result.error,
                "port_released": monitor_result.port_released,
            },
        )
        return monitor_result

    def _diagnose_build_failure(self, stderr: str) -> str:
        lowered = stderr.lower()
        if "cmake" in lowered and "not found" in lowered:
            return "Build failed because CMake is unavailable. Check bundled toolchains or PATH."
        if "arm-none-eabi" in lowered:
            return "Build failed because the ARM GCC toolchain is missing or misconfigured."
        if "undefined reference" in lowered:
            link_failure = self._classify_link_failure(stderr)
            if link_failure == "linker_script_missing":
                return "Build failed during link because the linker script is missing or could not be opened."
            if link_failure == "startup_symbol_missing":
                return "Build failed during link because startup/runtime symbols are missing. Check startup_stm32.s and system_stm32xx.c."
            if link_failure == "entry_point_missing":
                return "Build failed during link because the main entry path is incomplete. Check Core/Src/main.c and App sources."
            return "Build failed during link. Check startup files, linker script, or missing source files."
        return "Build failed. Inspect stderr for compiler or linker diagnostics."

    def _classify_build_failure(self, stderr: str) -> str:
        lowered = stderr.lower()
        if "cmake" in lowered and "not found" in lowered:
            return "tool_missing"
        if "arm-none-eabi" in lowered:
            return "toolchain_missing"
        if re.search(r":[0-9]+:[0-9]+:\s+error:", stderr):
            return "compile_error"
        if "undefined reference" in lowered:
            return "link_error"
        if "cmake" in lowered or "ninja" in lowered:
            return "configure_or_cache_issue"
        return "generic_build_failure"

    def _classify_link_failure(self, stderr: str) -> str:
        lowered = stderr.lower()
        if "cannot open linker script file" in lowered or "cannot find" in lowered and "stm32.ld" in lowered:
            return "linker_script_missing"
        if any(symbol in stderr for symbol in ("Reset_Handler", "SystemInit", "_estack", "__isr_vector")):
            return "startup_symbol_missing"
        if re.search(r"undefined reference to [`']main['`]", stderr) or re.search(
            r"undefined reference to [`']app_main_(init|loop)['`]",
            stderr,
        ):
            return "entry_point_missing"
        return "generic_link_error"

    def _attempt_build_fix(self, context: dict, build_result) -> dict[str, list[str]]:
        project = context["project"]
        reports = self._extract_build_error_reports(project, build_result.stderr)
        if not reports:
            return {"actions": [], "fixed_files": []}

        fixer = CodeFixer(self.config)
        actions: list[str] = []
        fixed_files: list[str] = []
        for file_path, report in reports.items():
            try:
                fix_result = fixer.fix_file(
                    project_path=str(project),
                    file_path=file_path,
                    review_report=report,
                    apply_changes=True,
                )
            except Exception as exc:
                actions.append(f"Build-fix attempt for {Path(file_path).name} was skipped: {exc}")
                continue
            if fix_result.success and fix_result.applied:
                actions.append(f"Applied build-aware code fix to {Path(file_path).name} based on compiler diagnostics.")
                fixed_files.append(str(Path(file_path).resolve()))
        return {"actions": actions, "fixed_files": fixed_files}

    def _attempt_link_repair(self, context: dict, build_result) -> dict[str, list[str]]:
        project = context["project"]
        project_config = self._load_project_config(project)
        if project_config is None or project_config.project_mode != "firmware":
            return {"actions": [], "fixed_files": []}

        family_path = project / "STM32_FAMILY.txt"
        if not family_path.exists():
            return {"actions": [], "fixed_files": []}

        assembler = Assembler()
        created_files = assembler.assemble_stm32_firmware_project(
            project=project_config,
            firmware_package=project_config.firmware_package or "STM32Cube_FW_UNKNOWN",
            stm32_family=family_path.read_text(encoding="utf-8").strip(),
            build_context={},
            staged_firmware_paths=[],
        )
        if not created_files:
            return {"actions": [], "fixed_files": []}

        link_failure = self._classify_link_failure(build_result.stderr)
        if link_failure == "linker_script_missing":
            message = "Restored missing STM32 linker/runtime scaffold files before retrying the link step."
        elif link_failure == "startup_symbol_missing":
            message = "Restored missing STM32 startup/runtime scaffold files before retrying the link step."
        elif link_failure == "entry_point_missing":
            message = "Restored missing STM32 entry-point scaffold files before retrying the link step."
        else:
            message = "Restored missing STM32 firmware scaffold files before retrying the link step."
        return {
            "actions": [message],
            "fixed_files": [str(Path(path).resolve()) for path in created_files],
        }

    def _review_fixed_files(self, context: dict, file_paths: list[str]) -> ReviewReport:
        project = context["project"]
        if not file_paths:
            report = ReviewReport(
                passed=False,
                total_issues=1,
                critical_count=0,
                error_count=1,
                warning_count=0,
                issues=[
                    ReviewIssue(
                        file=str(project),
                        line=1,
                        column=0,
                        severity="error",
                        rule_id="DEBUG-BUILD-FIX",
                        message="Automatic build fix did not produce any modified source files to review.",
                        suggestion="Inspect the compiler diagnostics and apply a manual fix or improve the fixer prompt.",
                    )
                ],
                raw_logs={"build_fix_review": {"enabled": True, "reason": "no files were modified"}},
            )
        else:
            report = ReviewEngine(str(project)).review_files(file_paths)
            report.raw_logs.update(
                {
                    "build_fix_review": {
                        "enabled": True,
                        "reviewed_files": file_paths,
                    }
                }
            )
        context["logger"].log_event(
            "CODE_REVIEW",
            project.name,
            {
                "scope": "build_fix",
                "passed": report.passed,
                "error_count": report.error_count,
                "warning_count": report.warning_count,
                "files": file_paths,
            },
        )
        return report

    def _load_project_config(self, project: Path) -> ProjectConfig | None:
        metadata_path = project / ".agent_project.json"
        if not metadata_path.exists():
            return None
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        try:
            return ProjectConfig.model_validate(data)
        except Exception:
            return None

    def _extract_build_error_reports(self, project: Path, stderr: str) -> dict[str, ReviewReport]:
        pattern = re.compile(
            r"^(?P<file>[A-Za-z]:[\\/].*?|[^:]+):(?P<line>\d+):(?P<column>\d+):\s+error:\s+(?P<message>.+)$",
            flags=re.MULTILINE,
        )
        grouped: dict[str, list[ReviewIssue]] = {}
        for match in pattern.finditer(stderr):
            raw_file = Path(match.group("file")).resolve()
            if raw_file.suffix.lower() not in {".c", ".h"}:
                continue
            try:
                raw_file.relative_to(project)
            except ValueError:
                continue
            if "build" in {part.lower() for part in raw_file.parts}:
                continue
            issue = ReviewIssue(
                file=str(raw_file),
                line=max(1, int(match.group("line"))),
                column=max(0, int(match.group("column"))),
                severity="error",
                rule_id="BUILD-COMPILE",
                message=match.group("message").strip(),
                suggestion="Fix the compiler-reported error while preserving project style and structure.",
            )
            grouped.setdefault(str(raw_file), []).append(issue)

        reports: dict[str, ReviewReport] = {}
        for file_path, issues in grouped.items():
            reports[file_path] = ReviewReport(
                passed=False,
                total_issues=len(issues),
                critical_count=0,
                error_count=len(issues),
                warning_count=0,
                issues=issues,
                raw_logs={"build_fix": {"source": "compiler_stderr"}},
            )
        return reports

    def _diagnose_flash_failure(self, stdout: str, stderr: str) -> str:
        combined = f"{stdout}\n{stderr}".lower()
        if "no debug probe detected" in combined:
            return "Flash failed because no debug probe was detected. Check ST-Link connection and USB driver state."
        if "cannot identify the device" in combined:
            return "Flash failed because the target MCU could not be identified. Check SWD wiring, target power, and programmer database."
        if "wrong extension" in combined or "missing the filepath" in combined:
            return "Flash failed because the build artifact format was not accepted by the programmer."
        return "Flash failed. Inspect programmer output for probe, target, or artifact issues."

    def _classify_flash_failure(self, stdout: str, stderr: str) -> str:
        combined = f"{stdout}\n{stderr}".lower()
        if "no debug probe detected" in combined:
            return "probe_missing"
        if "cannot identify the device" in combined:
            return "target_not_identified"
        if "wrong extension" in combined or "missing the filepath" in combined:
            return "artifact_format"
        return "generic_flash_failure"

    def _diagnose_monitor_result(self, monitor_result: MonitorResult) -> str:
        if monitor_result.success and monitor_result.lines:
            # Parse serial output for diagnostic markers
            anomalies = self._parse_serial_diagnostics(monitor_result.lines)
            if anomalies:
                return f"Serial output captured but anomalies detected: {'; '.join(anomalies)}"
            return "Debug loop completed successfully. Serial output was captured and the port was released."
        if "busy" in monitor_result.error.lower():
            return "Monitor failed because the serial port is busy. Close other serial tools and retry."
        if "no serial data captured" in monitor_result.error.lower():
            return "Monitor connected but no UART data was observed. Check UART pins, baudrate, and firmware print path."
        if monitor_result.error:
            return f"Monitor failed: {monitor_result.error}"
        return "Monitor finished without serial output."

    def _parse_serial_diagnostics(self, lines: list[str]) -> list[str]:
        """Scan serial output for [FAIL] markers and timing anomalies.

        Returns list of human-readable anomaly descriptions, or empty if all OK.
        """
        anomalies: list[str] = []
        fail_pattern = re.compile(r"\[FAIL\]\s*(.+)", re.IGNORECASE)
        dt_pattern = re.compile(r"dt\s*=\s*(\d+)")
        for line in lines:
            m = fail_pattern.search(line)
            if m:
                anomalies.append(f"FAIL: {m.group(1).strip()}")
            # Check SysTick timing diagnostic (expected ~10 for 10ms check)
            m = dt_pattern.search(line)
            if m and "OK" not in line.upper():
                dt_val = int(m.group(1))
                if dt_val < 8 or dt_val > 12:
                    anomalies.append(
                        f"TIMING: dt={dt_val} (expected 8-12 for 10ms SysTick check)"
                    )
        return anomalies

    def _serial_anomalies_to_fix_context(self, anomalies: list[str]) -> str:
        """Convert serial anomalies into a fix prompt context."""
        if not anomalies:
            return ""
        items = "\n".join(f"  - {a}" for a in anomalies)
        return (
            "The following anomalies were detected in the serial output "
            f"after flashing:\n{items}\n"
            "Please fix the firmware code to address these issues."
        )

    def _classify_monitor_failure(self, monitor_result: MonitorResult) -> str:
        lowered = monitor_result.error.lower()
        if "busy" in lowered:
            return "port_busy"
        if "no serial data captured" in lowered or "no uart data" in lowered:
            return "no_data"
        if lowered:
            return "generic_monitor_failure"
        return "no_output"


