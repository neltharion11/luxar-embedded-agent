from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from luxar.models.schemas import EngineeringContext
from luxar.server.app import _repair_messages_for_reasoning_handoff, create_app


class ServerAppTests(unittest.TestCase):
    @staticmethod
    def _cfg_stub():
        cfg = type("Cfg", (), {})()
        cfg.platform = type("Platform", (), {"default_platform": "stm32cubemx", "default_runtime": "baremetal"})()
        cfg.stm32 = type("Stm32", (), {"project_mode": "firmware", "firmware_package": "STM32Cube_FW_F1"})()
        cfg.llm = type(
            "LLM",
            (),
            {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "temperature": 0.2,
                "max_tokens": 1024,
                "timeout_sec": 30,
                "retry_attempts": 1,
                "retry_min_delay": 1,
                "retry_max_delay": 1,
                "base_url": "",
                "api_key_env": "",
            },
        )()
        cfg.toolchains = type(
            "Toolchains",
            (),
            {
                "root": "toolchains",
                "cmake": "",
                "arm_gcc": "",
                "ninja": "",
                "openocd": "",
                "programmer_cli": "",
            },
        )()
        cfg.build = type("Build", (), {"toolchain_prefix": "arm-none-eabi-"})()
        cfg.api_keys = {"deepseek": "test-key"}
        return cfg

    def test_analyze_docs_endpoint_returns_engineering_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("luxar.server.app.ConfigManager") as cm_cls, \
                 patch("luxar.server.app.DocumentEngineeringAnalyzer") as analyzer_cls:
                cm = cm_cls.return_value
                cm.ensure_default_config.return_value = object()
                cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                cm.workspace_root.return_value = Path(tmpdir) / "projects"
                cm.project_root.return_value = Path(tmpdir)
                analyzer_cls.return_value.analyze.return_value = EngineeringContext(
                    document_summary="SPI sensor with CS and INT."
                )
                with TestClient(create_app()) as client:
                    response = client.post("/api/analyze-docs", json={"docs": ["workspace/docs/bmi270.pdf"], "query": "wiring"})

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertIn("engineering_context", payload)
        self.assertEqual("SPI sensor with CS and INT.", payload["engineering_context"]["document_summary"])

    def test_run_task_endpoint_uses_single_entry_orchestrator(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("luxar.server.app.ConfigManager") as cm_cls, \
                 patch("luxar.server.app.run_task") as run_task_mock:
                cm = cm_cls.return_value
                cm.ensure_default_config.return_value = object()
                cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                cm.workspace_root.return_value = Path(tmpdir) / "projects"
                cm.project_root.return_value = Path(tmpdir)
                run_task_mock.return_value = {"success": True, "mode": "plan"}
                with TestClient(create_app()) as client:
                    response = client.post(
                        "/api/run-task",
                        json={"project": "DirectF1C", "task": "Blink LED and print UART logs", "plan_only": True},
                    )

        self.assertEqual(200, response.status_code)
        self.assertEqual("plan", response.json()["mode"])
        self.assertTrue(run_task_mock.called)

    def test_create_project_endpoint_initializes_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("luxar.server.app.ConfigManager") as cm_cls, \
                 patch("luxar.server.app.run_init_project") as init_mock:
                cm = cm_cls.return_value
                cfg = type("Cfg", (), {})()
                cfg.platform = type("Platform", (), {"default_platform": "stm32cubemx", "default_runtime": "baremetal"})()
                cfg.stm32 = type("Stm32", (), {"project_mode": "firmware", "firmware_package": "STM32Cube_FW_F1"})()
                cm.ensure_default_config.return_value = cfg
                cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                cm.workspace_root.return_value = Path(tmpdir) / "projects"
                cm.project_root.return_value = Path(tmpdir)
                init_mock.return_value = type("Proj", (), {"model_dump": lambda self, mode="json": {"name": "BlinkTest", "mcu": "STM32F103C8T6"}})()
                with TestClient(create_app()) as client:
                    response = client.post("/api/projects", json={"name": "BlinkTest", "mcu": "STM32F103C8T6"})

        self.assertEqual(200, response.status_code)
        self.assertEqual("BlinkTest", response.json()["project"]["name"])

    def test_import_project_endpoint_registers_existing_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "external_project"
            source.mkdir(parents=True, exist_ok=True)
            with patch("luxar.server.app.ConfigManager") as cm_cls:
                cm = cm_cls.return_value
                cfg = type("Cfg", (), {})()
                cfg.platform = type("Platform", (), {"default_platform": "stm32cubemx", "default_runtime": "baremetal"})()
                cfg.stm32 = type("Stm32", (), {"project_mode": "firmware", "firmware_package": "STM32Cube_FW_F1"})()
                cm.ensure_default_config.return_value = cfg
                cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                cm.workspace_root.return_value = Path(tmpdir) / "projects"
                cm.project_root.return_value = Path(tmpdir)
                with TestClient(create_app()) as client:
                    response = client.post(
                        "/api/projects/import",
                        json={"source_path": str(source), "name": "ImportedProj", "mcu": "STM32F103C8T6"},
                    )

        self.assertEqual(200, response.status_code)
        self.assertEqual("ImportedProj", response.json()["project"]["name"])

    def test_pick_directory_endpoint_returns_selected_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("luxar.server.app.ConfigManager") as cm_cls, \
                 patch("tkinter.Tk") as tk_cls, \
                 patch("tkinter.filedialog.askdirectory", return_value=str(Path(tmpdir) / "picked")):
                cm = cm_cls.return_value
                cfg = type("Cfg", (), {})()
                cfg.platform = type("Platform", (), {"default_platform": "stm32cubemx", "default_runtime": "baremetal"})()
                cfg.stm32 = type("Stm32", (), {"project_mode": "firmware", "firmware_package": "STM32Cube_FW_F1"})()
                cm.ensure_default_config.return_value = cfg
                cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                cm.workspace_root.return_value = Path(tmpdir) / "projects"
                cm.project_root.return_value = Path(tmpdir)
                with TestClient(create_app()) as client:
                    response = client.get("/api/pick-directory")

        self.assertEqual(200, response.status_code)
        self.assertIn("picked", response.json()["path"])

    def test_pick_files_endpoint_returns_selected_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            file_a = str(Path(tmpdir) / "doc1.pdf")
            file_b = str(Path(tmpdir) / "doc2.md")
            with patch("luxar.server.app.ConfigManager") as cm_cls, \
                 patch("tkinter.Tk"), \
                 patch("tkinter.filedialog.askopenfilenames", return_value=(file_a, file_b)):
                cm = cm_cls.return_value
                cfg = type("Cfg", (), {})()
                cfg.platform = type("Platform", (), {"default_platform": "stm32cubemx", "default_runtime": "baremetal"})()
                cfg.stm32 = type("Stm32", (), {"project_mode": "firmware", "firmware_package": "STM32Cube_FW_F1"})()
                cm.ensure_default_config.return_value = cfg
                cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                cm.workspace_root.return_value = Path(tmpdir) / "projects"
                cm.project_root.return_value = Path(tmpdir)
                with TestClient(create_app()) as client:
                    response = client.get("/api/pick-files")

        self.assertEqual(200, response.status_code)
        self.assertEqual([file_a, file_b], response.json()["paths"])

    def test_conversation_endpoint_defaults_to_single_entry_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("luxar.server.app.ConfigManager") as cm_cls, \
                 patch("luxar.server.app._run_agent_loop") as run_loop_mock:
                cm = cm_cls.return_value
                cm.ensure_default_config.return_value = self._cfg_stub()
                cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                cm.workspace_root.return_value = Path(tmpdir) / "projects"
                cm.project_root.return_value = Path(tmpdir)
                run_loop_mock.return_value = {"content": "ok", "reasoning_content": ""}
                with TestClient(create_app()) as client:
                    response = client.post(
                        "/api/conversations/DirectF1C",
                        json={"message": "Blink LED and print UART", "stream": False},
                    )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertIn("message", payload)
        self.assertTrue(run_loop_mock.called)

    def test_non_stream_conversation_persists_reasoning_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("luxar.server.app.ConfigManager") as cm_cls, \
                 patch("luxar.core.llm_client.LLMClient.complete_with_tools") as complete_mock:
                cm = cm_cls.return_value
                cm.ensure_default_config.return_value = self._cfg_stub()
                cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                cm.workspace_root.return_value = Path(tmpdir) / "projects"
                cm.project_root.return_value = Path(tmpdir)
                complete_mock.return_value = type(
                    "Resp",
                    (),
                    {"content": "你好，我在。", "reasoning_content": "internal-thought", "tool_calls": None},
                )()
                with TestClient(create_app()) as client:
                    post_response = client.post(
                        "/api/conversations/ReasoningKeep",
                        json={"message": "你好", "stream": False},
                    )
                    get_response = client.get("/api/conversations/ReasoningKeep")

        self.assertEqual(200, post_response.status_code)
        self.assertEqual(200, get_response.status_code)
        messages = get_response.json()["messages"]
        self.assertEqual("assistant", messages[-1]["role"])
        self.assertEqual("internal-thought", messages[-1]["reasoning_content"])

    def test_import_conversation_endpoint_copies_messages_to_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("luxar.server.app.ConfigManager") as cm_cls, \
                 patch("luxar.server.app._run_agent_loop") as run_loop_mock:
                cm = cm_cls.return_value
                cm.ensure_default_config.return_value = self._cfg_stub()
                cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                cm.workspace_root.return_value = Path(tmpdir) / "projects"
                cm.project_root.return_value = Path(tmpdir)
                run_loop_mock.return_value = {"content": "已记录项目需求。", "reasoning_content": ""}
                with TestClient(create_app()) as client:
                    post_response = client.post(
                        "/api/conversations/__global__",
                        json={"message": "项目名：EnvMonitor", "stream": False},
                    )
                    import_response = client.post(
                        "/api/conversations/EnvMonitor/import",
                        json={"source_project": "__global__", "replace": True},
                    )
                    get_response = client.get("/api/conversations/EnvMonitor")

        self.assertEqual(200, post_response.status_code)
        self.assertEqual(200, import_response.status_code)
        self.assertEqual(2, import_response.json()["imported_messages"])
        messages = get_response.json()["messages"]
        self.assertEqual("user", messages[0]["role"])
        self.assertEqual("项目名：EnvMonitor", messages[0]["content"])
        self.assertEqual("assistant", messages[1]["role"])

    def test_streaming_conversation_emits_done_event_without_tool_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("luxar.server.app.ConfigManager") as cm_cls, \
                 patch("luxar.core.llm_client.LLMClient.complete_stream") as complete_stream_mock:
                cm = cm_cls.return_value
                cm.ensure_default_config.return_value = self._cfg_stub()
                cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                cm.workspace_root.return_value = Path(tmpdir) / "projects"
                cm.project_root.return_value = Path(tmpdir)
                complete_stream_mock.return_value = iter([
                    {"type": "token", "content": "hello"},
                ])
                with TestClient(create_app()) as client:
                    response = client.post(
                        "/api/conversations/StreamPlain",
                        json={"message": "你好", "stream": True},
                        headers={"Accept": "text/event-stream"},
                    )

        self.assertEqual(200, response.status_code)
        self.assertIn("event: token", response.text)
        self.assertIn("event: done", response.text)

    def test_streaming_conversation_emits_tool_running_before_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("luxar.server.app.ConfigManager") as cm_cls, \
                 patch("luxar.core.llm_client.LLMClient.complete_stream") as complete_stream_mock, \
                 patch("luxar.server.app._execute_tool", return_value='{"ok": true}') as exec_tool_mock:
                cm = cm_cls.return_value
                cm.ensure_default_config.return_value = self._cfg_stub()
                cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                cm.workspace_root.return_value = Path(tmpdir) / "projects"
                cm.project_root.return_value = Path(tmpdir)

                rounds = iter([
                    [
                        {
                            "type": "tool_call",
                            "id": "call-1",
                            "name": "run_task",
                            "arguments": '{"task":"blink"}',
                        }
                    ],
                    [
                        {"type": "token", "content": "done"},
                    ],
                ])

                def _stream_side_effect(*args, **kwargs):
                    yield from next(rounds)

                complete_stream_mock.side_effect = _stream_side_effect
                with TestClient(create_app()) as client:
                    response = client.post(
                        "/api/conversations/StreamTool",
                        json={"message": "帮我跑任务", "stream": True},
                        headers={"Accept": "text/event-stream"},
                    )

        self.assertEqual(200, response.status_code)
        self.assertTrue(exec_tool_mock.called)
        self.assertIn("event: tool_call", response.text)
        self.assertIn("event: tool_running", response.text)
        self.assertIn("event: tool_result", response.text)
        self.assertIn("event: done", response.text)

    def test_reasoning_handoff_repair_drops_plain_assistant_turns(self) -> None:
        repaired = _repair_messages_for_reasoning_handoff([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "old reply without reasoning"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "review_project", "arguments": "{}"},
            }]},
            {"role": "tool", "tool_call_id": "call-1", "content": "{}"},
        ])

        self.assertEqual("system", repaired[0]["role"])
        self.assertEqual("user", repaired[1]["role"])
        self.assertNotIn("old reply without reasoning", [msg.get("content") for msg in repaired])
        self.assertEqual(2, len(repaired))


if __name__ == "__main__":
    unittest.main()
