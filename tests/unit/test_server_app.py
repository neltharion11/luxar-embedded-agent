from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from luxar.models.schemas import EngineeringContext
from luxar.server.chat_support import repair_messages_for_reasoning_handoff as _repair_messages_for_reasoning_handoff
from luxar.server.app import (
    LEGACY_HTTP_SURFACE_ENV,
    PUBLIC_TOOL_NAMES,
    _execute_tool,
    _run_agent_loop,
    create_app,
)
from luxar.server.tool_execution import (
    build_tool_envelope as _build_tool_envelope,
    format_tool_result_summary as _format_tool_result_summary,
    is_tool_result_failure as _is_tool_result_failure,
    serialize_tool_content_for_llm as _serialize_tool_content_for_llm,
)
from luxar.server.tool_runtime import AgentToolTimeoutError


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

    def test_legacy_http_surface_is_disabled_by_default(self) -> None:
        """Legacy conversation endpoints require an explicit opt-in switch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict("os.environ", {LEGACY_HTTP_SURFACE_ENV: "0"}, clear=False), patch(
                "luxar.server.app.ConfigManager"
            ) as cm_cls:
                cm = cm_cls.return_value
                cm.ensure_default_config.return_value = object()
                cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                cm.workspace_root.return_value = Path(tmpdir) / "projects"
                cm.project_root.return_value = Path(tmpdir)
                with TestClient(create_app()) as client:
                    for method, path in [
                        ("get", "/api/conversations/Demo"),
                        ("post", "/api/conversations/Demo"),
                    ]:
                        response = getattr(client, method)(path)
                        self.assertEqual(404, response.status_code, path)

    def test_analyze_docs_endpoint_returns_engineering_context_when_legacy_http_surface_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict("os.environ", {LEGACY_HTTP_SURFACE_ENV: "1"}, clear=False), patch(
                "luxar.server.app.ConfigManager"
            ) as cm_cls, patch("luxar.core.document_engineering.DocumentEngineeringAnalyzer") as analyzer_cls:
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
        self.assertEqual("SPI sensor with CS and INT.", payload["engineering_context"]["document_summary"])

    def test_legacy_public_endpoints_are_removed(self) -> None:
        """Verify truly removed legacy endpoints return 404."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("luxar.server.app.ConfigManager") as cm_cls:
                cm = cm_cls.return_value
                cm.ensure_default_config.return_value = self._cfg_stub()
                cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                cm.workspace_root.return_value = Path(tmpdir) / "projects"
                cm.project_root.return_value = Path(tmpdir)
                with TestClient(create_app()) as client:
                    for method, path in [
                        ("post", "/api/run-task"),
                        ("get", "/api/review/Demo"),
                        ("get", "/api/project-context/Demo"),
                        ("get", "/api/git/Demo"),
                        ("get", "/api/knowledge-base"),
                        ("get", "/api/toolchains"),
                        ("post", "/api/generate-driver"),
                        ("post", "/api/generate-driver-loop"),
                    ]:
                        response = getattr(client, method)(path)
                        self.assertEqual(404, response.status_code, path)

    def test_agent_tool_surface_excludes_legacy_control_plane_names(self) -> None:
        self.assertNotIn("init_project", PUBLIC_TOOL_NAMES)
        self.assertNotIn("forge_project", PUBLIC_TOOL_NAMES)
        self.assertNotIn("run_task", PUBLIC_TOOL_NAMES)
        self.assertNotIn("run_workflow", PUBLIC_TOOL_NAMES)

    def test_execute_tool_rejects_legacy_tool_name(self) -> None:
        cfg = self._cfg_stub()
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = type("CM", (), {})()
            cm.workspace_root = lambda: Path(tmpdir) / "projects"
            cm.driver_library_root = lambda: Path(tmpdir) / "driver_library"

            result = _execute_tool("init_project", {}, cfg, cm)

        self.assertFalse(result.ok)
        self.assertIn("not part of the LUXAR 0.2.3 public control plane", result.error or "")

    def test_conversation_endpoint_uses_vnext_agent_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict("os.environ", {LEGACY_HTTP_SURFACE_ENV: "1"}, clear=False), patch(
                "luxar.server.app.ConfigManager"
            ) as cm_cls, patch("luxar.server.app._run_agent_loop") as run_loop_mock:
                cm = cm_cls.return_value
                cm.ensure_default_config.return_value = self._cfg_stub()
                cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                cm.workspace_root.return_value = Path(tmpdir) / "projects"
                cm.project_root.return_value = Path(tmpdir)
                run_loop_mock.return_value = {"content": "ok", "reasoning_content": ""}
                with TestClient(create_app()) as client:
                    response = client.post(
                        "/api/conversations/DirectF1C",
                        json={"message": "Bring up OLED", "stream": False},
                    )

        self.assertEqual(200, response.status_code)
        self.assertTrue(run_loop_mock.called)

    def test_streaming_project_creation_does_not_emit_legacy_init_project_tool_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict("os.environ", {LEGACY_HTTP_SURFACE_ENV: "1"}, clear=False), patch(
                "luxar.server.app.ConfigManager"
            ) as cm_cls, patch("luxar.server.legacy_surface.run_init_project") as init_project_mock:
                cm = cm_cls.return_value
                cm.ensure_default_config.return_value = self._cfg_stub()
                cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                cm.workspace_root.return_value = Path(tmpdir) / "projects"
                cm.project_root.return_value = Path(tmpdir)
                init_project_mock.return_value = type(
                    "Project",
                    (),
                    {
                        "model_dump": lambda self, mode="json": {
                            "name": "Demo",
                            "mcu": "STM32F103C8T6",
                            "platform": "stm32cubemx",
                            "runtime": "baremetal",
                        }
                    },
                )()
                with TestClient(create_app()) as client:
                    response = client.post(
                        "/api/conversations/__global__",
                        json={
                            "message": "项目名: Demo\nMCU: STM32F103C8T6\n平台: stm32cubemx\n系统: baremetal",
                            "stream": True,
                        },
                        headers={"Accept": "text/event-stream"},
                    )

        self.assertEqual(200, response.status_code)
        self.assertIn("event: project_created", response.text)
        self.assertNotIn('"tool_call": "init_project"', response.text)
        self.assertNotIn('"tool": "init_project"', response.text)

    def test_streaming_conversation_emits_vnext_phase_and_skill_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict("os.environ", {LEGACY_HTTP_SURFACE_ENV: "1"}, clear=False), patch(
                "luxar.server.app.ConfigManager"
            ) as cm_cls, patch("luxar.core.llm_client.LLMClient.complete_stream") as complete_stream_mock, patch(
                "luxar.server.app._execute_tool_with_timeout",
                return_value=_build_tool_envelope("skills_list", {"skills": [{"name": "oled-ch1116"}]}),
            ):
                cm = cm_cls.return_value
                cm.ensure_default_config.return_value = self._cfg_stub()
                cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                cm.workspace_root.return_value = Path(tmpdir) / "projects"
                cm.project_root.return_value = Path(tmpdir)

                rounds = iter(
                    [
                        [{"type": "tool_call", "id": "call-1", "name": "skills_list", "arguments": "{}"}],
                        [{"type": "token", "content": "done"}],
                    ]
                )

                def _stream_side_effect(*args, **kwargs):
                    yield from next(rounds)

                complete_stream_mock.side_effect = _stream_side_effect
                with TestClient(create_app()) as client:
                    response = client.post(
                        "/api/conversations/StreamSkills",
                        json={"message": "List skills", "stream": True},
                        headers={"Accept": "text/event-stream"},
                    )

        self.assertEqual(200, response.status_code)
        self.assertIn("event: phase_changed", response.text)
        self.assertIn("event: skill_loaded", response.text)
        self.assertNotIn("event: workflow_started", response.text)

    def test_streaming_conversation_emits_lesson_and_promotion_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict("os.environ", {LEGACY_HTTP_SURFACE_ENV: "1"}, clear=False), patch(
                "luxar.server.app.ConfigManager"
            ) as cm_cls, patch("luxar.core.llm_client.LLMClient.complete_stream") as complete_stream_mock, patch(
                "luxar.server.app._execute_tool_with_timeout"
            ) as execute_mock:
                cm = cm_cls.return_value
                cm.ensure_default_config.return_value = self._cfg_stub()
                cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                cm.workspace_root.return_value = Path(tmpdir) / "projects"
                cm.project_root.return_value = Path(tmpdir)
                execute_mock.side_effect = [
                    _build_tool_envelope("lesson_record", {"lesson": {"topic": "oled_dark_screen"}}),
                    _build_tool_envelope("skill_promote", {"promotion_level": "validated"}),
                ]

                rounds = iter(
                    [
                        [{"type": "tool_call", "id": "call-1", "name": "lesson_record", "arguments": '{"payload":{"topic":"oled_dark_screen"}}'}],
                        [{"type": "tool_call", "id": "call-2", "name": "skill_promote", "arguments": '{"name":"oled-ch1116"}'}],
                        [{"type": "token", "content": "done"}],
                    ]
                )

                def _stream_side_effect(*args, **kwargs):
                    yield from next(rounds)

                complete_stream_mock.side_effect = _stream_side_effect
                with TestClient(create_app()) as client:
                    response = client.post(
                        "/api/conversations/Lessons",
                        json={"message": "Record and promote", "stream": True},
                        headers={"Accept": "text/event-stream"},
                    )

        self.assertEqual(200, response.status_code)
        self.assertIn("event: lesson_recorded", response.text)
        self.assertIn("event: promotion_applied", response.text)

    def test_streaming_conversation_emits_escalation_on_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict("os.environ", {LEGACY_HTTP_SURFACE_ENV: "1"}, clear=False), patch(
                "luxar.server.app.ConfigManager"
            ) as cm_cls, patch("luxar.core.llm_client.LLMClient.complete_stream") as complete_stream_mock, patch(
                "luxar.server.app._execute_tool_with_timeout",
                side_effect=AgentToolTimeoutError("sentinel"),
            ):
                cm = cm_cls.return_value
                cm.ensure_default_config.return_value = self._cfg_stub()
                cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                cm.workspace_root.return_value = Path(tmpdir) / "projects"
                cm.project_root.return_value = Path(tmpdir)

                rounds = iter(
                    [
                        [{"type": "tool_call", "id": "call-1", "name": "workspace_build", "arguments": '{"project":"Demo"}'}],
                    ]
                )

                def _stream_side_effect(*args, **kwargs):
                    yield from next(rounds)

                complete_stream_mock.side_effect = _stream_side_effect
                with TestClient(create_app()) as client:
                    response = client.post(
                        "/api/conversations/StreamTimeout",
                        json={"message": "Build", "stream": True},
                        headers={"Accept": "text/event-stream"},
                    )

        self.assertEqual(200, response.status_code)
        self.assertIn("event: escalation_triggered", response.text)
        self.assertIn("event: error", response.text)

    def test_run_agent_loop_stops_when_tool_call_limit_is_exceeded(self) -> None:
        cfg = self._cfg_stub()
        cm = type("CM", (), {})()
        cm.workspace_root = lambda: Path(".")
        cm.project_root = lambda: Path(".")
        cm.driver_library_root = lambda: Path(".")

        tool_calls = [
            type(
                "ToolCall",
                (),
                {"id": f"call-{index}", "function_name": "runtime_run", "arguments": {"task": f"task-{index}"}},
            )()
            for index in range(51)
        ]
        client = type("Client", (), {})()
        client.has_valid_api_key = lambda: True
        client.complete_with_tools = lambda **kwargs: type(
            "Resp",
            (),
            {"content": None, "reasoning_content": "", "tool_calls": tool_calls},
        )()

        with patch("luxar.server.app._prepare_agent_context", return_value=[]), patch(
            "luxar.server.app._execute_tool", return_value=_build_tool_envelope("runtime_run", {"success": True})
        ):
            reply = asyncio.run(_run_agent_loop([], "run many tools", "Demo", cfg, cm, client))

        self.assertIn("Tool call limit exceeded", reply["content"])
        self.assertIn("attempted call 51", reply["content"])

    def test_streaming_tool_result_payloads_stay_parseable_for_large_results(self) -> None:
        large_tools = {
            "workspace_build": {"success": False, "stderr": "error:\n" * 1200, "stdout": "", "return_code": 1},
            "skill_execute": {"success": True, "evidence": [{"step": "probe", "detail": "x" * 4000}], "raw_response": "x" * 7000},
            "skills_list": {"skills": [{"name": f"skill-{index}"} for index in range(120)]},
            "workspace_inspect": {"workspace_root": "C:/tmp", "projects": [{"name": f"proj-{index}"} for index in range(120)]},
        }

        for tool_name, payload in large_tools.items():
            with self.subTest(tool=tool_name):
                with tempfile.TemporaryDirectory() as tmpdir:
                    with patch.dict("os.environ", {LEGACY_HTTP_SURFACE_ENV: "1"}, clear=False), patch(
                        "luxar.server.app.ConfigManager"
                    ) as cm_cls, patch("luxar.core.llm_client.LLMClient.complete_stream") as complete_stream_mock, patch(
                        "luxar.server.app._execute_tool", return_value=_build_tool_envelope(tool_name, payload)
                    ):
                        cm = cm_cls.return_value
                        cm.ensure_default_config.return_value = self._cfg_stub()
                        cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                        cm.workspace_root.return_value = Path(tmpdir) / "projects"
                        cm.project_root.return_value = Path(tmpdir)

                        rounds = iter(
                            [
                                [{"type": "tool_call", "id": "call-1", "name": tool_name, "arguments": '{"project":"Demo"}'}],
                                [{"type": "token", "content": "done"}],
                            ]
                        )

                        def _stream_side_effect(*args, **kwargs):
                            yield from next(rounds)

                        complete_stream_mock.side_effect = _stream_side_effect
                        with TestClient(create_app()) as client:
                            response = client.post(
                                "/api/conversations/Demo",
                                json={"message": "run", "stream": True},
                                headers={"Accept": "text/event-stream"},
                            )

                self.assertEqual(200, response.status_code)
                self.assertNotIn("无法解析的结果", response.text)
                tool_result_payload = None
                current_event = None
                for line in response.text.splitlines():
                    if line.startswith("event: "):
                        current_event = line.split(": ", 1)[1]
                    elif current_event == "tool_result" and line.startswith("data: "):
                        tool_result_payload = json.loads(line.split(": ", 1)[1])
                        break
                self.assertIsNotNone(tool_result_payload)
                self.assertEqual(tool_name, tool_result_payload["tool"])
                self.assertIsInstance(json.loads(tool_result_payload["result"]), dict)

    def test_tool_content_serializer_compacts_large_fields_without_mutating_canonical_data(self) -> None:
        payload = {
            "diff": "d" * 10000,
            "raw_response": "r" * 10000,
            "skills": [{"name": f"skill-{index}"} for index in range(50)],
        }
        envelope = _build_tool_envelope("skills_list", payload)
        original_diff = envelope.data["diff"]
        original_raw_response = envelope.data["raw_response"]
        original_skills = list(envelope.data["skills"])

        serialized = _serialize_tool_content_for_llm(envelope, max_chars=1500)
        compacted = json.loads(serialized)

        self.assertLessEqual(len(serialized), 1500)
        self.assertNotEqual(original_diff, compacted.get("diff", ""))
        self.assertNotEqual(original_raw_response, compacted.get("raw_response", ""))
        self.assertLess(len(compacted.get("skills", [])), len(original_skills))
        self.assertEqual(original_diff, envelope.data["diff"])
        self.assertEqual(original_raw_response, envelope.data["raw_response"])
        self.assertEqual(original_skills, envelope.data["skills"])

    def test_tool_summary_uses_vnext_payload_shapes(self) -> None:
        cases = [
            (
                "workspace_build",
                _build_tool_envelope("workspace_build", {"success": False, "stderr": "error: one\nerror: two"}),
                "构建失败: 2 个错误",
            ),
            (
                "skill_execute",
                _build_tool_envelope("skill_execute", {"success": True, "evidence": [{"step": "probe"}]}),
                "技能执行完成: 1 条证据",
            ),
            (
                "runtime_run",
                _build_tool_envelope(
                    "runtime_run",
                    {"selected_skills": [{"name": "a"}], "selected_executable_skills": [{"name": "b"}]},
                ),
                "runtime 规划完成: 1 个技能, 1 个可执行技能",
            ),
            ("skills_list", _build_tool_envelope("skills_list", {"skills": [{"name": "a"}, {"name": "b"}]}), "已加载 2 个技能"),
        ]

        for tool_name, envelope, expected in cases:
            with self.subTest(tool=tool_name):
                ok, summary = _format_tool_result_summary(tool_name, {}, envelope)
                self.assertTrue(ok or tool_name == "workspace_build")
                self.assertEqual(expected, summary)

    def test_tool_failure_detection_treats_blocked_as_non_failure_and_bad_json_as_failure(self) -> None:
        blocked = _build_tool_envelope("runtime_run", {"success": False, "blocked": True, "reason": "blocked"})
        self.assertFalse(_is_tool_result_failure(blocked))
        self.assertTrue(_is_tool_result_failure("{bad json"))

    def test_reasoning_handoff_repair_drops_plain_assistant_turns(self) -> None:
        repaired = _repair_messages_for_reasoning_handoff(
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "old reply without reasoning"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "skill_execute", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call-1", "content": "{}"},
            ]
        )

        self.assertEqual("system", repaired[0]["role"])
        self.assertEqual("user", repaired[1]["role"])
        self.assertNotIn("old reply without reasoning", [msg.get("content") for msg in repaired])
        self.assertEqual(2, len(repaired))


if __name__ == "__main__":
    unittest.main()
