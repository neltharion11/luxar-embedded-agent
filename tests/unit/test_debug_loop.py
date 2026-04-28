from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from luxar.core.config_manager import AgentConfig
from luxar.core.debug_loop import DebugLoop
from luxar.models.schemas import BuildResult, FlashResult, MonitorResult

WINDOWS_PREFIX = "C:"


def _make_context(project: Path) -> dict:
    return {
        "project": project,
        "logger": mock.Mock(),
        "snapshot_path": str(project / ".agent_backups" / "snap"),
        "log_dir": str(project / "logs"),
        "build_system": mock.Mock(),
        "flash_system": mock.Mock(),
        "uart_monitor": mock.Mock(),
    }


class DebugLoopRunTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tmpdir.name) / "TestProject"
        self.project.mkdir(parents=True, exist_ok=True)
        self.loop = DebugLoop(config=AgentConfig(), project_root=self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_run_build_failure_returns_with_build_diagnosis(self) -> None:
        context = _make_context(self.project)
        context["build_system"].build_project.return_value = BuildResult(
            success=False, return_code=1, stderr="arm-none-eabi-gcc: not found"
        )
        with mock.patch.object(self.loop, "_create_context", return_value=context):
            result = self.loop.run(project_path=str(self.project))
        self.assertFalse(result.success)
        self.assertEqual("build", result.stage)
        self.assertIn("ARM GCC", result.diagnosis)
        self.assertIsNotNone(result.build_result)
        self.assertIsNone(result.flash_result)
        self.assertIsNone(result.monitor_result)

    def test_run_flash_failure_returns_with_flash_diagnosis(self) -> None:
        context = _make_context(self.project)
        context["build_system"].build_project.return_value = BuildResult(success=True, return_code=0)
        context["flash_system"].flash_project.return_value = FlashResult(
            success=False, stderr="No debug probe detected"
        )
        with mock.patch.object(self.loop, "_create_context", return_value=context):
            result = self.loop.run(project_path=str(self.project))
        self.assertFalse(result.success)
        self.assertEqual("flash", result.stage)
        self.assertIn("debug probe", result.diagnosis)
        self.assertIsNotNone(result.flash_result)

    def test_run_full_success_returns_complete(self) -> None:
        context = _make_context(self.project)
        context["build_system"].build_project.return_value = BuildResult(success=True, return_code=0)
        context["flash_system"].flash_project.return_value = FlashResult(success=True)
        context["uart_monitor"].monitor_project.return_value = MonitorResult(
            success=True, port="COM3", lines=["Hello"], port_released=True
        )
        with mock.patch.object(self.loop, "_create_context", return_value=context):
            result = self.loop.run(project_path=str(self.project), port="COM3")
        self.assertTrue(result.success)
        self.assertEqual("complete", result.stage)

    def test_run_monitor_no_lines_returns_monitor_failure(self) -> None:
        context = _make_context(self.project)
        context["build_system"].build_project.return_value = BuildResult(success=True, return_code=0)
        context["flash_system"].flash_project.return_value = FlashResult(success=True)
        context["uart_monitor"].monitor_project.return_value = MonitorResult(
            success=True, port="COM3", lines=[], port_released=True
        )
        with mock.patch.object(self.loop, "_create_context", return_value=context):
            result = self.loop.run(project_path=str(self.project), port="COM3")
        self.assertFalse(result.success)
        self.assertEqual("monitor", result.stage)

    def test_run_logs_debug_event_on_completion(self) -> None:
        context = _make_context(self.project)
        context["build_system"].build_project.return_value = BuildResult(success=True, return_code=0)
        context["flash_system"].flash_project.return_value = FlashResult(success=True)
        context["uart_monitor"].monitor_project.return_value = MonitorResult(
            success=True, port="COM3", lines=["data"], port_released=True
        )
        with mock.patch.object(self.loop, "_create_context", return_value=context):
            result = self.loop.run(project_path=str(self.project), port="COM3")
        context["logger"].log_event.assert_any_call("DEBUG_LOOP", self.project.name, mock.ANY)


class DebugLoopDiagnoseBuildTests(unittest.TestCase):
    def setUp(self):
        self.loop = DebugLoop(config=AgentConfig(), project_root=".")

    def test_diagnose_build_failure_cmake_not_found(self) -> None:
        self.assertIn("CMake", self.loop._diagnose_build_failure("CMake not found"))

    def test_diagnose_build_failure_toolchain_missing(self) -> None:
        self.assertIn("ARM GCC", self.loop._diagnose_build_failure("arm-none-eabi-gcc: not found"))

    def test_diagnose_build_failure_link_errors(self) -> None:
        self.assertIn("link", self.loop._diagnose_build_failure("undefined reference to `main'").lower())

    def test_diagnose_build_failure_generic(self) -> None:
        self.assertIn("stderr", self.loop._diagnose_build_failure("random error").lower())


class DebugLoopClassifyBuildTests(unittest.TestCase):
    def setUp(self):
        self.loop = DebugLoop(config=AgentConfig(), project_root=".")

    def test_tool_missing(self) -> None:
        self.assertEqual("tool_missing", self.loop._classify_build_failure("CMake not found"))

    def test_toolchain_missing(self) -> None:
        self.assertEqual("toolchain_missing", self.loop._classify_build_failure("arm-none-eabi-gcc: not found"))

    def test_compile_error(self) -> None:
        self.assertEqual("compile_error", self.loop._classify_build_failure("main.c:10:5: error: bad"))

    def test_link_error(self) -> None:
        self.assertEqual("link_error", self.loop._classify_build_failure("undefined reference to `x'"))

    def test_configure_issue(self) -> None:
        self.assertEqual("configure_or_cache_issue", self.loop._classify_build_failure("CMake Error: no"))

    def test_generic_failure(self) -> None:
        self.assertEqual("generic_build_failure", self.loop._classify_build_failure("unknown error"))


class DebugLoopClassifyLinkTests(unittest.TestCase):
    def setUp(self):
        self.loop = DebugLoop(config=AgentConfig(), project_root=".")

    def test_linker_script_missing(self) -> None:
        self.assertEqual("linker_script_missing", self.loop._classify_link_failure(
            "cannot open linker script file STM32.ld"))

    def test_startup_symbol_missing(self) -> None:
        self.assertEqual("startup_symbol_missing", self.loop._classify_link_failure(
            "undefined reference to `Reset_Handler'"))

    def test_entry_point_missing_main(self) -> None:
        self.assertEqual("entry_point_missing", self.loop._classify_link_failure(
            "undefined reference to `main'"))

    def test_entry_point_missing_app(self) -> None:
        self.assertEqual("entry_point_missing", self.loop._classify_link_failure(
            "undefined reference to `app_main_init'"))

    def test_generic_link_error(self) -> None:
        self.assertEqual("generic_link_error", self.loop._classify_link_failure(
            "undefined reference to `some_func'"))


class DebugLoopDiagnoseFlashTests(unittest.TestCase):
    def setUp(self):
        self.loop = DebugLoop(config=AgentConfig(), project_root=".")

    def test_probe_missing(self) -> None:
        self.assertIn("debug probe", self.loop._diagnose_flash_failure("No debug probe detected.", ""))

    def test_target_not_identified(self) -> None:
        self.assertIn("target MCU", self.loop._diagnose_flash_failure("", "Cannot identify the device"))

    def test_artifact_format(self) -> None:
        self.assertIn("artifact format", self.loop._diagnose_flash_failure("Wrong extension", "").lower())

    def test_generic_failure(self) -> None:
        self.assertIn("programmer output", self.loop._diagnose_flash_failure("random error", "").lower())


class DebugLoopDiagnoseMonitorTests(unittest.TestCase):
    def setUp(self):
        self.loop = DebugLoop(config=AgentConfig(), project_root=".")

    def test_success(self) -> None:
        m = MonitorResult(success=True, lines=["data"], port="COM3")
        self.assertIn("completed successfully", self.loop._diagnose_monitor_result(m).lower())

    def test_port_busy(self) -> None:
        m = MonitorResult(success=False, error="Port busy", port="COM3")
        self.assertIn("busy", self.loop._diagnose_monitor_result(m).lower())

    def test_no_data(self) -> None:
        m = MonitorResult(success=False, error="No serial data captured within timeout.", port="COM3")
        self.assertIn("UART", self.loop._diagnose_monitor_result(m))

    def test_generic_error(self) -> None:
        m = MonitorResult(success=False, error="Something went wrong", port="COM3")
        self.assertIn("Something went wrong", self.loop._diagnose_monitor_result(m))

    def test_no_output(self) -> None:
        m = MonitorResult(success=False, error="", port="COM3")
        self.assertIn("without serial output", self.loop._diagnose_monitor_result(m).lower())


class DebugLoopExtractBuildErrorTests(unittest.TestCase):
    def setUp(self):
        self.loop = DebugLoop(config=AgentConfig(), project_root=".")

    def test_parses_stderr(self) -> None:
        project = Path(f"{WINDOWS_PREFIX}\\test\\proj")
        stderr = (
            f"{WINDOWS_PREFIX}\\test\\proj\\Src\\main.c:42:5: error: expected ';'\n"
            f"{WINDOWS_PREFIX}\\test\\proj\\Src\\main.c:55:10: error: 'x' undeclared\n"
        )
        reports = self.loop._extract_build_error_reports(project, stderr)
        expected_key = str((project / "Src" / "main.c").resolve())
        self.assertIn(expected_key, reports)
        self.assertEqual(2, reports[expected_key].total_issues)

    def test_skips_non_source_files(self) -> None:
        project = Path(f"{WINDOWS_PREFIX}\\test\\proj")
        stderr = f"{WINDOWS_PREFIX}\\test\\proj\\build\\output.o:1:1: error: bad\n"
        self.assertEqual(0, len(self.loop._extract_build_error_reports(project, stderr)))

    def test_skips_outside_project(self) -> None:
        project = Path(f"{WINDOWS_PREFIX}\\test\\proj")
        stderr = f"{WINDOWS_PREFIX}\\other\\lib.c:10:5: error: bad\n"
        self.assertEqual(0, len(self.loop._extract_build_error_reports(project, stderr)))

    def test_skips_build_dir(self) -> None:
        project = Path(f"{WINDOWS_PREFIX}\\test\\proj")
        stderr = f"{WINDOWS_PREFIX}\\test\\proj\\build\\Src\\main.c:10:5: error: bad\n"
        self.assertEqual(0, len(self.loop._extract_build_error_reports(project, stderr)))

    def test_empty_stderr(self) -> None:
        self.assertEqual(0, len(self.loop._extract_build_error_reports(Path("/test"), "")))


class DebugLoopLoadProjectConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tmpdir.name) / "TestProject"
        self.project.mkdir(parents=True, exist_ok=True)
        self.loop = DebugLoop(config=AgentConfig(), project_root=self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_missing_file_returns_none(self) -> None:
        self.assertIsNone(self.loop._load_project_config(self.project))

    def test_valid_json_parses(self) -> None:
        payload = {"name": "TestProject", "path": str(self.project)}
        (self.project / ".agent_project.json").write_text(json.dumps(payload), encoding="utf-8")
        config = self.loop._load_project_config(self.project)
        self.assertEqual("TestProject", config.name)

    def test_invalid_json_returns_none(self) -> None:
        (self.project / ".agent_project.json").write_text("not json", encoding="utf-8")
        self.assertIsNone(self.loop._load_project_config(self.project))


if __name__ == "__main__":
    unittest.main()

