from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from luxar.cli import main
from luxar.core.config_manager import AgentConfig
from luxar.models.schemas import (
    BuildResult,
    CodeFixResult,
    DebugLoopResult,
    DriverGenerationResult,
    DriverPipelineResult,
    FlashResult,
    MonitorResult,
    ProjectConfig,
    SkillArtifact,
    WorkflowRunResult,
)


def _fake_project(name: str = "test-proj") -> ProjectConfig:
    return ProjectConfig(
        name=name,
        path=f"/fake/workspace/{name}",
        platform="stm32cubemx",
        runtime="baremetal",
        project_mode="firmware",
        mcu="stm32f103c8",
    )


class CliConfigTests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    @patch("luxar.cli.ConfigManager")
    def test_config_show(self, mock_cfg_cls):
        inst = MagicMock()
        cfg = AgentConfig()
        inst.ensure_default_config.return_value = cfg
        mock_cfg_cls.return_value = inst
        result = self.runner.invoke(main, ["config", "show"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("agent", result.output)
        self.assertIn("stm32", result.output)

    @patch("luxar.cli.ConfigManager")
    def test_config_toolchains(self, mock_cfg_cls):
        inst = MagicMock()
        cfg = AgentConfig()
        inst.ensure_default_config.return_value = cfg
        inst.project_root.return_value = "/fake/root"
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.ToolchainManager") as mock_tc:
            mock_tc.return_value.status.return_value = {"cmake": "ok"}
            result = self.runner.invoke(main, ["config", "toolchains"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("cmake", result.output)

    @patch("luxar.cli.ConfigManager")
    def test_config_firmware(self, mock_cfg_cls):
        inst = MagicMock()
        cfg = AgentConfig()
        inst.ensure_default_config.return_value = cfg
        inst.firmware_library_root.return_value = MagicMock().__str__()
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.FirmwareLibraryManager") as mock_fl:
            mock_fl.return_value.list_stm32_packages.return_value = []
            result = self.runner.invoke(main, ["config", "firmware"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("firmware_root", result.output)

    @patch("luxar.cli.ConfigManager")
    def test_config_workspace(self, mock_cfg_cls):
        inst = MagicMock()
        cfg = AgentConfig()
        inst.ensure_default_config.return_value = cfg
        ws_root = MagicMock()
        ws_root.__str__ = lambda s: "/fake/ws"
        ws_root.__truediv__ = lambda s, o: MagicMock()
        ws_root.glob.return_value = []
        inst.workspace_root.return_value = ws_root
        mock_cfg_cls.return_value = inst
        result = self.runner.invoke(main, ["config", "workspace"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("workspace_root", result.output)


class CliCommandTests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    @patch("luxar.cli.ConfigManager")
    def test_init(self, mock_cfg_cls):
        inst = MagicMock()
        cfg = AgentConfig()
        inst.ensure_default_config.return_value = cfg
        inst.workspace_root.return_value = MagicMock()
        mock_cfg_cls.return_value = inst
        project = _fake_project("my-proj")
        with patch("luxar.cli.run_init_project", return_value=project):
            result = self.runner.invoke(
                main, ["init", "--name", "my-proj", "--mcu", "stm32f103c8"]
            )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("my-proj", result.output)
        self.assertIn("stm32f103c8", result.output)

    @patch("luxar.cli.ConfigManager")
    def test_generate_driver(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        inst.project_root.return_value = "/fake/root"
        mock_cfg_cls.return_value = inst
        result_obj = DriverGenerationResult(
            success=True, chip="stm32f103c8", interface="spi"
        )
        with patch("luxar.cli.run_generate_driver", return_value=result_obj):
            res = self.runner.invoke(
                main,
                [
                    "generate-driver",
                    "--chip", "stm32f103c8",
                    "--interface", "spi",
                    "--doc-summary", "SPI driver",
                ],
            )
        self.assertEqual(res.exit_code, 0)
        self.assertIn("spi", res.output)

    @patch("luxar.cli.ConfigManager")
    def test_generate_driver_loop(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        inst.project_root.return_value = "/fake/root"
        mock_cfg_cls.return_value = inst
        result_obj = DriverPipelineResult(
            success=True, chip="stm32f103c8", interface="i2c"
        )
        with patch("luxar.cli.run_generate_driver_loop", return_value=result_obj):
            res = self.runner.invoke(
                main,
                [
                    "generate-driver-loop",
                    "--chip", "stm32f103c8",
                    "--interface", "i2c",
                    "--doc-summary", "I2C driver",
                ],
            )
        self.assertEqual(res.exit_code, 0)
        self.assertIn("i2c", res.output)

    @patch("luxar.cli.ConfigManager")
    def test_search_driver(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        inst.project_root.return_value = "/fake/root"
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.run_search_driver", return_value={"results": [], "stats": {}}):
            res = self.runner.invoke(
                main, ["search-driver", "--keyword", "spi"]
            )
        self.assertEqual(res.exit_code, 0)
        self.assertIn("results", res.output)

    @patch("luxar.cli.ConfigManager")
    def test_snapshot(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        ws_root = MagicMock()
        ws_root.__str__ = lambda s: "/fake/ws"
        inst.workspace_root.return_value = ws_root
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.ProjectManager") as mock_pm:
            mock_pm.return_value.load_project.return_value = _fake_project()
            with patch("luxar.cli.BackupManager") as mock_bm:
                mock_bm.return_value.create_snapshot.return_value = "/fake/snapshot"
                res = self.runner.invoke(
                    main, ["snapshot", "--project", "test-proj", "--label", "v1"]
                )
        self.assertEqual(res.exit_code, 0)
        self.assertIn("snapshot_path", res.output)

    @patch("luxar.cli.ConfigManager")
    def test_check_ioc(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        ws_root = MagicMock()
        ws_root.__str__ = lambda s: "/fake/ws"
        inst.workspace_root.return_value = ws_root
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.ProjectManager") as mock_pm:
            mock_pm.return_value.load_project.return_value = _fake_project()
            with patch("luxar.cli.run_check_ioc", return_value={"status": "ok"}):
                res = self.runner.invoke(main, ["check-ioc", "--project", "test-proj"])
        self.assertEqual(res.exit_code, 0)
        self.assertIn("status", res.output)

    @patch("luxar.cli.ConfigManager")
    def test_assemble(self, mock_cfg_cls):
        inst = MagicMock()
        cfg = AgentConfig()
        inst.ensure_default_config.return_value = cfg
        ws_root = MagicMock()
        ws_root.__str__ = lambda s: "/fake/ws"
        inst.workspace_root.return_value = ws_root
        inst.firmware_library_root.return_value = "/fake/fw"
        inst.driver_library_root.return_value = "/fake/drivers"
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.ProjectManager") as mock_pm:
            mock_pm.return_value.load_project.return_value = _fake_project()
            with patch("luxar.cli.run_assemble_project", return_value={"created_files": [], "project": "test-proj"}):
                res = self.runner.invoke(main, ["assemble", "--project", "test-proj", "--drivers", "spi"])
        self.assertEqual(res.exit_code, 0)
        self.assertIn("created_files", res.output)

    @patch("luxar.cli.ConfigManager")
    def test_diff(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        ws_root = MagicMock()
        ws_root.__str__ = lambda s: "/fake/ws"
        inst.workspace_root.return_value = ws_root
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.ProjectManager") as mock_pm:
            mock_pm.return_value.load_project.return_value = _fake_project()
            with patch("luxar.cli.GitManager") as mock_git:
                mock_git.return_value.get_diff_since_last_human_commit.return_value = "diff output"
                res = self.runner.invoke(main, ["diff", "--project", "test-proj"])
        self.assertEqual(res.exit_code, 0)
        self.assertIn("diff output", res.output)

    @patch("luxar.cli.ConfigManager")
    def test_build(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        ws_root = MagicMock()
        ws_root.__str__ = lambda s: "/fake/ws"
        inst.workspace_root.return_value = ws_root
        inst.project_root.return_value = "/fake/root"
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.ProjectManager") as mock_pm:
            mock_pm.return_value.load_project.return_value = _fake_project()
            with patch("luxar.cli.run_build_project", return_value=BuildResult(success=True)):
                res = self.runner.invoke(main, ["build", "--project", "test-proj"])
        self.assertEqual(res.exit_code, 0)
        self.assertIn("success", res.output)

    @patch("luxar.cli.ConfigManager")
    def test_build_clean_skip_review(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        ws_root = MagicMock()
        ws_root.__str__ = lambda s: "/fake/ws"
        inst.workspace_root.return_value = ws_root
        inst.project_root.return_value = "/fake/root"
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.ProjectManager") as mock_pm:
            mock_pm.return_value.load_project.return_value = _fake_project()
            with patch("luxar.cli.run_build_project", return_value=BuildResult(success=True)) as mock_run:
                res = self.runner.invoke(
                    main, ["build", "--project", "test-proj", "--clean", "--skip-review"]
                )
        self.assertEqual(res.exit_code, 0)
        _, kwargs = mock_run.call_args
        self.assertTrue(kwargs["clean"])
        self.assertTrue(kwargs["skip_review"])

    @patch("luxar.cli.ConfigManager")
    def test_flash(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        ws_root = MagicMock()
        ws_root.__str__ = lambda s: "/fake/ws"
        inst.workspace_root.return_value = ws_root
        inst.project_root.return_value = "/fake/root"
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.ProjectManager") as mock_pm:
            mock_pm.return_value.load_project.return_value = _fake_project()
            with patch("luxar.cli.run_flash_project", return_value=FlashResult(success=True)):
                res = self.runner.invoke(main, ["flash", "--project", "test-proj"])
        self.assertEqual(res.exit_code, 0)
        self.assertIn("success", res.output)

    @patch("luxar.cli.ConfigManager")
    def test_monitor(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        ws_root = MagicMock()
        ws_root.__str__ = lambda s: "/fake/ws"
        inst.workspace_root.return_value = ws_root
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.ProjectManager") as mock_pm:
            mock_pm.return_value.load_project.return_value = _fake_project()
            with patch("luxar.cli.run_monitor_project", return_value=MonitorResult(success=True)):
                res = self.runner.invoke(main, ["monitor", "--project", "test-proj"])
        self.assertEqual(res.exit_code, 0)
        self.assertIn("success", res.output)

    @patch("luxar.cli.ConfigManager")
    def test_review(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        ws_root = MagicMock()
        ws_root.__str__ = lambda s: "/fake/ws"
        inst.workspace_root.return_value = ws_root
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.ProjectManager") as mock_pm:
            mock_pm.return_value.load_project.return_value = _fake_project()
            with patch("luxar.cli.run_review_project", return_value={"report": {}}):
                res = self.runner.invoke(main, ["review", "--project", "test-proj"])
        self.assertEqual(res.exit_code, 0)
        self.assertIn("report", res.output)

    @patch("luxar.cli.ConfigManager")
    def test_parse_doc(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        inst.project_root.return_value = "/fake/root"
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.run_parse_doc", return_value={"parse_result": {}, "knowledge_base": {}}):
            res = self.runner.invoke(
                main, ["parse-doc", "--doc", "/fake/doc.txt"]
            )
        self.assertEqual(res.exit_code, 0)
        self.assertIn("parse_result", res.output)

    @patch("luxar.cli.ConfigManager")
    def test_update_skill(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        inst.project_root.return_value = "/fake/root"
        mock_cfg_cls.return_value = inst
        artifact = SkillArtifact(
            name="spi_protocol_skill", protocol="spi", path="/fake/skill.md"
        )
        with patch("luxar.cli.run_update_skill", return_value=artifact):
            res = self.runner.invoke(
                main,
                [
                    "update-skill",
                    "--protocol", "spi",
                    "--device", "BMI270",
                    "--summary", "SPI skill",
                    "--source-project", "ProjA",
                ],
            )
        self.assertEqual(res.exit_code, 0)
        self.assertIn("spi", res.output)

    @patch("luxar.cli.ConfigManager")
    def test_list_skills(self, mock_cfg_cls):
        inst = MagicMock()
        cfg = AgentConfig()
        inst.ensure_default_config.return_value = cfg
        inst.project_root.return_value = "/fake/root"
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.SkillManager") as mock_sk:
            mock_sk.return_value.list_skills.return_value = [
                {"protocol": "spi", "validation_count": 1}
            ]
            res = self.runner.invoke(main, ["list-skills"])
        self.assertEqual(res.exit_code, 0)
        self.assertIn("spi", res.output)

    @patch("luxar.cli.ConfigManager")
    def test_list_skills_with_protocol_filter(self, mock_cfg_cls):
        inst = MagicMock()
        cfg = AgentConfig()
        inst.ensure_default_config.return_value = cfg
        inst.project_root.return_value = "/fake/root"
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.SkillManager") as mock_sk:
            mock_sk.return_value.list_skills.return_value = [
                {"protocol": "spi", "validation_count": 1}
            ]
            res = self.runner.invoke(main, ["list-skills", "--protocol", "spi"])
        self.assertEqual(res.exit_code, 0)
        mock_sk.return_value.list_skills.assert_called_with(protocol="spi")

    @patch("luxar.cli.ConfigManager")
    def test_status(self, mock_cfg_cls):
        inst = MagicMock()
        cfg = AgentConfig()
        inst.ensure_default_config.return_value = cfg
        ws_root = MagicMock()
        ws_root.__str__ = lambda s: "/fake/ws"
        inst.workspace_root.return_value = ws_root
        inst.project_root.return_value = "/fake/root"
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.ProjectManager") as mock_pm:
            mock_pm.return_value.load_project.return_value = _fake_project("my-proj")
            with patch("luxar.cli.ToolchainManager") as mock_tc:
                mock_tc.return_value.status.return_value = {"cmake": "ok"}
                with patch("luxar.cli.SkillManager") as mock_sk:
                    mock_sk.return_value.list_skills.return_value = []
                    with patch("luxar.cli.GitManager") as mock_git:
                        mock_git.return_value.get_diff_since_last_human_commit.return_value = ""
                        res = self.runner.invoke(main, ["status", "--project", "my-proj"])
        self.assertEqual(res.exit_code, 0)
        self.assertIn("my-proj", res.output)
        self.assertIn("toolchains", res.output)

    @patch("luxar.cli.ConfigManager")
    def test_single_entry_task_routes_through_run_task(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        inst.project_root.return_value = "/fake/root"
        inst.workspace_root.return_value = "/fake/ws"
        inst.driver_library_root.return_value = "/fake/drivers"
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.run_task", return_value={"success": True, "mode": "plan"}) as mock_run:
            res = self.runner.invoke(main, ["--project", "DirectF1C", "--plan-only", "Blink LED and print over UART"])
        self.assertEqual(res.exit_code, 0)
        _, kwargs = mock_run.call_args
        self.assertEqual("DirectF1C", kwargs["project_name"])
        self.assertEqual("Blink LED and print over UART", kwargs["task"])
        self.assertTrue(kwargs["plan_only"])

    @patch("luxar.cli.ConfigManager")
    def test_run_command_supports_docs_and_dry_run(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        inst.project_root.return_value = "/fake/root"
        inst.workspace_root.return_value = "/fake/ws"
        inst.driver_library_root.return_value = "/fake/drivers"
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.run_task", return_value={"success": True, "mode": "plan"}) as mock_run:
            res = self.runner.invoke(
                main,
                [
                    "run",
                    "--project", "DirectF1C",
                    "--doc", "workspace/docs/bmi270.pdf",
                    "--dry-run",
                    "--task", "Wire the BMI270 and generate the project",
                ],
            )
        self.assertEqual(res.exit_code, 0)
        _, kwargs = mock_run.call_args
        self.assertEqual(["workspace/docs/bmi270.pdf"], kwargs["docs"])
        self.assertTrue(kwargs["dry_run"])

    @patch("luxar.cli.ConfigManager")
    def test_fix_code(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        ws_root = MagicMock()
        ws_root.__str__ = lambda s: "/fake/ws"
        inst.workspace_root.return_value = ws_root
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.ProjectManager") as mock_pm:
            mock_pm.return_value.load_project.return_value = _fake_project()
            with patch("luxar.cli.run_fix_code", return_value=CodeFixResult(success=True, applied=True)):
                res = self.runner.invoke(
                    main, ["fix-code", "--project", "test-proj", "--file", "src/main.c"]
                )
        self.assertEqual(res.exit_code, 0)
        self.assertIn("success", res.output)

    @patch("luxar.cli.ConfigManager")
    def test_fix_code_dry_run(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        ws_root = MagicMock()
        ws_root.__str__ = lambda s: "/fake/ws"
        inst.workspace_root.return_value = ws_root
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.ProjectManager") as mock_pm:
            mock_pm.return_value.load_project.return_value = _fake_project()
            with patch("luxar.cli.run_fix_code", return_value=CodeFixResult(success=True, applied=False)) as mock_run:
                res = self.runner.invoke(
                    main, ["fix-code", "--project", "test-proj", "--file", "src/main.c", "--dry-run"]
                )
        self.assertEqual(res.exit_code, 0)
        _, kwargs = mock_run.call_args
        self.assertFalse(kwargs["apply_changes"])

    @patch("luxar.cli.ConfigManager")
    def test_debug_loop(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        ws_root = MagicMock()
        ws_root.__str__ = lambda s: "/fake/ws"
        inst.workspace_root.return_value = ws_root
        inst.project_root.return_value = "/fake/root"
        mock_cfg_cls.return_value = inst
        with patch("luxar.cli.ProjectManager") as mock_pm:
            mock_pm.return_value.load_project.return_value = _fake_project()
            with patch("luxar.cli.run_debug_loop_project", return_value=DebugLoopResult(success=True, stage="complete")):
                res = self.runner.invoke(main, ["debug-loop", "--project", "test-proj"])
        self.assertEqual(res.exit_code, 0)
        self.assertIn("complete", res.output)

    @patch("luxar.cli.ConfigManager")
    def test_forge(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        ws_root = MagicMock()
        ws_root.__str__ = lambda s: "/fake/ws"
        inst.workspace_root.return_value = ws_root
        inst.project_root.return_value = "/fake/root"
        inst.driver_library_root.return_value = "/fake/drivers"
        mock_cfg_cls.return_value = inst
        result_obj = WorkflowRunResult(success=True, workflow="forge")
        with patch("luxar.cli.ProjectManager") as mock_pm:
            mock_pm.return_value.load_project.return_value = _fake_project()
            with patch("luxar.cli.run_forge_project", return_value=result_obj):
                res = self.runner.invoke(
                    main,
                    ["forge", "--project", "test-proj", "--prompt", "Blink LED once per second"],
                )
        self.assertEqual(res.exit_code, 0)
        self.assertIn("forge", res.output)

    @patch("luxar.cli.ConfigManager")
    def test_forge_with_plan_only_and_skip_flags(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        ws_root = MagicMock()
        ws_root.__str__ = lambda s: "/fake/ws"
        inst.workspace_root.return_value = ws_root
        inst.project_root.return_value = "/fake/root"
        inst.driver_library_root.return_value = "/fake/drivers"
        mock_cfg_cls.return_value = inst
        result_obj = WorkflowRunResult(success=True, workflow="forge")
        with patch("luxar.cli.ProjectManager") as mock_pm:
            mock_pm.return_value.load_project.return_value = _fake_project()
            with patch("luxar.cli.run_forge_project", return_value=result_obj) as mock_run:
                res = self.runner.invoke(
                    main,
                    [
                        "forge",
                        "--project", "test-proj",
                        "--prompt", "Blink LED once per second",
                        "--plan-only",
                        "--no-flash",
                        "--no-monitor",
                    ],
                )
        self.assertEqual(res.exit_code, 0)
        _, kwargs = mock_run.call_args
        self.assertTrue(kwargs["plan_only"])
        self.assertTrue(kwargs["no_flash"])
        self.assertTrue(kwargs["no_monitor"])

    @patch("luxar.cli.ConfigManager")
    def test_forge_with_docs(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        ws_root = MagicMock()
        ws_root.__str__ = lambda s: "/fake/ws"
        inst.workspace_root.return_value = ws_root
        inst.project_root.return_value = "/fake/root"
        inst.driver_library_root.return_value = "/fake/drivers"
        mock_cfg_cls.return_value = inst
        result_obj = WorkflowRunResult(success=True, workflow="forge")
        with patch("luxar.cli.ProjectManager") as mock_pm:
            mock_pm.return_value.load_project.return_value = _fake_project()
            with patch("luxar.cli.run_forge_project", return_value=result_obj) as mock_run:
                res = self.runner.invoke(
                    main,
                    [
                        "forge",
                        "--project", "test-proj",
                        "--prompt", "Read BMI270 over SPI",
                        "--doc", "workspace/docs/bmi270.pdf",
                        "--doc", "workspace/docs/board_notes.md",
                        "--doc-query", "chip id register",
                    ],
                )
        self.assertEqual(res.exit_code, 0)
        _, kwargs = mock_run.call_args
        self.assertEqual(["workspace/docs/bmi270.pdf", "workspace/docs/board_notes.md"], kwargs["docs"])
        self.assertEqual("chip id register", kwargs["doc_query"])

    @patch("luxar.cli.ConfigManager")
    def test_workflow_driver(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        inst.project_root.return_value = "/fake/root"
        mock_cfg_cls.return_value = inst
        result_obj = WorkflowRunResult(success=True, workflow="driver")
        with patch("luxar.cli.run_driver_workflow", return_value=result_obj):
            res = self.runner.invoke(
                main,
                [
                    "workflow", "driver",
                    "--chip", "stm32f103c8",
                    "--interface", "spi",
                    "--doc-summary", "SPI driver",
                ],
            )
        self.assertEqual(res.exit_code, 0)
        self.assertIn("driver", res.output)

    @patch("luxar.cli.ConfigManager")
    def test_workflow_debug(self, mock_cfg_cls):
        inst = MagicMock()
        inst.ensure_default_config.return_value = AgentConfig()
        ws_root = MagicMock()
        ws_root.__str__ = lambda s: "/fake/ws"
        inst.workspace_root.return_value = ws_root
        inst.project_root.return_value = "/fake/root"
        mock_cfg_cls.return_value = inst
        result_obj = WorkflowRunResult(success=True, workflow="debug")
        with patch("luxar.cli.ProjectManager") as mock_pm:
            mock_pm.return_value.load_project.return_value = _fake_project()
            with patch("luxar.cli.run_debug_workflow", return_value=result_obj):
                res = self.runner.invoke(
                    main, ["workflow", "debug", "--project", "test-proj"]
                )
        self.assertEqual(res.exit_code, 0)
        self.assertIn("debug", res.output)

    @patch("luxar.cli.ConfigManager")
    @patch("luxar.cli._write_service_state")
    @patch("luxar.cli._running_service_state", return_value=None)
    @patch("luxar.cli.os.getpid", return_value=4321)
    @patch("luxar.cli._load_service_state", return_value={"pid": 4321})
    @patch("luxar.cli._clear_service_state")
    @patch("luxar.server.app.create_app", return_value=object())
    @patch("uvicorn.run")
    def test_serve_records_service_state(
        self,
        mock_uvicorn_run,
        mock_create_app,
        mock_clear_state,
        mock_load_state,
        mock_getpid,
        mock_running_state,
        mock_write_state,
        mock_cfg_cls,
    ):
        inst = MagicMock()
        inst.project_root.return_value = Path("/fake/root")
        mock_cfg_cls.return_value = inst

        res = self.runner.invoke(main, ["serve", "--host", "0.0.0.0", "--port", "9000", "--reload"])

        self.assertEqual(res.exit_code, 0)
        mock_write_state.assert_called_once_with(
            inst,
            {"pid": 4321, "host": "0.0.0.0", "port": 9000, "reload": True},
        )
        mock_uvicorn_run.assert_called_once()
        mock_clear_state.assert_called_once_with(inst)

    @patch("luxar.cli.ConfigManager")
    @patch("luxar.cli._running_service_state", return_value={"pid": 1234, "host": "127.0.0.1", "port": 8000})
    def test_serve_rejects_when_service_already_running(self, mock_running_state, mock_cfg_cls):
        inst = MagicMock()
        mock_cfg_cls.return_value = inst

        res = self.runner.invoke(main, ["serve"])

        self.assertNotEqual(res.exit_code, 0)
        self.assertIn("already running", res.output)

    @patch("luxar.cli.ConfigManager")
    @patch("luxar.cli._stop_service_process", return_value={"stopped": True, "pid": 1234, "host": "127.0.0.1", "port": 8000})
    def test_stop_stops_running_service(self, mock_stop_service, mock_cfg_cls):
        inst = MagicMock()
        mock_cfg_cls.return_value = inst

        res = self.runner.invoke(main, ["stop"])

        self.assertEqual(res.exit_code, 0)
        self.assertIn("stopped", res.output)

    @patch("luxar.cli.ConfigManager")
    @patch("luxar.cli._stop_service_process", return_value={"stopped": False, "reason": "not_running"})
    def test_stop_reports_when_service_not_running(self, mock_stop_service, mock_cfg_cls):
        inst = MagicMock()
        mock_cfg_cls.return_value = inst

        res = self.runner.invoke(main, ["stop"])

        self.assertEqual(res.exit_code, 0)
        self.assertIn("not running", res.output)

    @patch("luxar.cli.ConfigManager")
    @patch("luxar.cli._stop_service_process", return_value={"stopped": False, "reason": "stale_state", "pid": 2222, "host": "127.0.0.1", "port": 8000})
    @patch("luxar.cli._load_service_state", return_value={"host": "0.0.0.0", "port": 9001, "reload": True})
    @patch("luxar.cli.click.get_current_context")
    def test_restart_uses_previous_service_settings(
        self,
        mock_get_current_context,
        mock_load_state,
        mock_stop_service,
        mock_cfg_cls,
    ):
        inst = MagicMock()
        mock_cfg_cls.return_value = inst
        ctx = MagicMock()
        mock_get_current_context.return_value = ctx

        res = self.runner.invoke(main, ["restart"])

        self.assertEqual(res.exit_code, 0)
        ctx.invoke.assert_called_once_with(main.commands["serve"], host="0.0.0.0", port=9001, reload=True)

    @patch("luxar.cli.ConfigManager")
    @patch("luxar.cli._stop_service_process", return_value={"stopped": False, "reason": "not_running"})
    @patch("luxar.cli._load_service_state", return_value=None)
    @patch("luxar.cli.click.get_current_context")
    def test_restart_allows_option_overrides(
        self,
        mock_get_current_context,
        mock_load_state,
        mock_stop_service,
        mock_cfg_cls,
    ):
        inst = MagicMock()
        mock_cfg_cls.return_value = inst
        ctx = MagicMock()
        mock_get_current_context.return_value = ctx

        res = self.runner.invoke(main, ["restart", "--host", "0.0.0.0", "--port", "8010", "--reload"])

        self.assertEqual(res.exit_code, 0)
        ctx.invoke.assert_called_once_with(main.commands["serve"], host="0.0.0.0", port=8010, reload=True)


class CliMissingOptionTests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def test_init_missing_name(self):
        result = self.runner.invoke(main, ["init", "--mcu", "stm32f103c8"])
        self.assertNotEqual(result.exit_code, 0)

    def test_build_missing_project(self):
        result = self.runner.invoke(main, ["build"])
        self.assertNotEqual(result.exit_code, 0)

    def test_status_missing_project(self):
        result = self.runner.invoke(main, ["status"])
        self.assertNotEqual(result.exit_code, 0)


if __name__ == "__main__":
    unittest.main()

