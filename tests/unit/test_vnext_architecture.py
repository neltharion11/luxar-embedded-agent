from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from luxar.api import create_app
from luxar.core.task_router import LEGACY_TASK_ROUTER_COMPATIBILITY_MODE, TaskRouter
from luxar.server import app as server_app_module
from luxar.server.app import PUBLIC_TOOL_NAMES
from luxar.tools import run_task as run_task_module
from luxar.tools.run_task import LEGACY_COMPATIBILITY_MODE, LEGACY_RUN_TASK_WARNING, run_task
from luxar.core.config_manager import AgentConfig


class VNextArchitectureGuardrailTests(unittest.TestCase):
    def test_misbuilt_harness_directories_are_absent(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        self.assertFalse((repo_root / "src" / "luxar" / "harness").exists())
        self.assertFalse((repo_root / "workspace" / "harnesses").exists())

    def test_legacy_skill_library_directory_is_absent_from_repo_defaults(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        self.assertFalse((repo_root / "workspace" / "skill_library").exists())



    def test_legacy_workflows_directory_is_absent(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        self.assertFalse((repo_root / "src" / "luxar" / "workflows").exists(),
                         "src/luxar/workflows/ must be deleted per LUXAR 0.2.0")

    def test_legacy_prompts_directory_is_absent(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        self.assertFalse((repo_root / "src" / "luxar" / "prompts").exists(),
                         "src/luxar/prompts/ must be deleted per LUXAR 0.2.0")

    def test_legacy_top_level_tools_are_absent(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        tools_dir = repo_root / "src" / "luxar" / "tools"
        removed = ["forge_project.py", "review_code.py", "generate_driver_loop.py"]
        for fname in removed:
            self.assertFalse((tools_dir / fname).exists(),
                             f"src/luxar/tools/{fname} must be removed per LUXAR 0.2.0")

    def test_app_py_has_no_dead_core_imports(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        app_content = (repo_root / "src" / "luxar" / "server" / "app.py").read_text(encoding="utf-8")
        dead_imports = [
            "from luxar.core.review_engine import",
            "from luxar.core.context_compressor import",
        ]
        for imp in dead_imports:
            self.assertNotIn(imp, app_content.split(chr(10))[0:20],
                             f"Dead import {imp!r} should not be in app.py top-level imports")

        repo_root = Path(__file__).resolve().parents[2]
        self.assertFalse((repo_root / "src" / "luxar" / "workflows").exists(),
                         "src/luxar/workflows/ must be deleted per LUXAR 0.2.0")

        repo_root = Path(__file__).resolve().parents[2]
        self.assertFalse((repo_root / "src" / "luxar" / "prompts").exists(),
                         "src/luxar/prompts/ must be deleted per LUXAR 0.2.0")

        repo_root = Path(__file__).resolve().parents[2]
        tools_dir = repo_root / "src" / "luxar" / "tools"
        removed = ["forge_project.py", "review_code.py", "generate_driver_loop.py"]
        for fname in removed:
            self.assertFalse((tools_dir / fname).exists(),
                             f"src/luxar/tools/{fname} must be removed per LUXAR 0.2.0")

        repo_root = Path(__file__).resolve().parents[2]
        app_content = (repo_root / "src" / "luxar" / "server" / "app.py").read_text(encoding="utf-8")
        dead_imports = [
            "from luxar.core.review_engine import",
            "from luxar.core.context_compressor import",
        ]
        for imp in dead_imports:
            self.assertNotIn(imp, app_content.split(chr(10))[0:20],
                             f"Dead import {imp!r} should not be in app.py top-level imports")
        repo_root = Path(__file__).resolve().parents[2]
        self.assertFalse((repo_root / 'src' / 'luxar' / 'workflows').exists(),
                         'src/luxar/workflows/ must be deleted per LUXAR 0.2.0')

        repo_root = Path(__file__).resolve().parents[2]
        self.assertFalse((repo_root / 'src' / 'luxar' / 'prompts').exists(),
                         'src/luxar/prompts/ must be deleted per LUXAR 0.2.0')

        repo_root = Path(__file__).resolve().parents[2]
        tools_dir = repo_root / 'src' / 'luxar' / 'tools'
        removed = ['forge_project.py', 'review_code.py', 'generate_driver_loop.py']
        for fname in removed:
            self.assertFalse((tools_dir / fname).exists(),
                             f'src/luxar/tools/{fname} must be removed per LUXAR 0.2.0')

        repo_root = Path(__file__).resolve().parents[2]
        app_content = (repo_root / 'src' / 'luxar' / 'server' / 'app.py').read_text(encoding='utf-8')
        dead_imports = [
            'from luxar.core.review_engine import',
            'from luxar.core.context_compressor import',
        ]
        for imp in dead_imports:
            self.assertNotIn(imp, app_content.split(chr(10))[0:20],
                             f'Dead import {imp!r} should not be in app.py top-level imports')

    def test_orchestration_modules_moved_to_agent_workers(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        core_dir = repo_root / "src" / "luxar" / "core"
        workers_dir = repo_root / "src" / "luxar" / "agent" / "workers"
        moved_modules = [
            "workflow_engine.py",
            "driver_pipeline.py",
            "debug_loop.py",
            "_langgraph_driver.py",
            "_langgraph_debug.py",
        ]
        for mod in moved_modules:
            self.assertFalse((core_dir / mod).exists(),
                             f"src/luxar/core/{mod} must be moved to agent/workers/ per LUXAR 0.2.0")
            self.assertTrue((workers_dir / f"_{mod.lstrip("_")}" if not mod.startswith("_") else workers_dir / mod).exists(),
                            f"agent/workers/{mod} must exist after migration")

    def test_vnext_public_api_surface_is_present(self) -> None:
            expected = {
                ("POST", "/api/runtime/run"),
                ("GET", "/api/runtime/explain"),
                ("GET", "/api/skills"),
                ("GET", "/api/memory"),
                ("GET", "/api/memory/lessons"),
                ("GET", "/api/workspace"),
                ("POST", "/api/workspace/build"),
                ("POST", "/api/workspace/flash"),
                ("POST", "/api/workspace/monitor"),
                ("POST", "/api/workspace/probe"),
                ("GET", "/api/session-search"),
            }
            with tempfile.TemporaryDirectory() as tmpdir:
                with patch("luxar.server.app.ConfigManager") as cm_cls:
                    cm = cm_cls.return_value
                    cm.ensure_default_config.return_value = object()
                    cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                    cm.workspace_root.return_value = Path(tmpdir) / "projects"
                    cm.project_root.return_value = Path(tmpdir)
                    with TestClient(create_app()) as client:
                        routes = {
                            (method, route.path)
                            for route in client.app.routes
                            for method in getattr(route, "methods", set())
                            if method in {"GET", "POST"}
                        }
            missing = expected - routes
            self.assertFalse(missing, f"Missing expected vNext routes: {sorted(missing)}")

    def test_deprecated_public_api_surface_is_absent(self) -> None:
        deprecated = {
            ("POST", "/api/run-task"),
            ("GET", "/api/project-context/{name}"),
            ("POST", "/api/generate-driver"),
            ("POST", "/api/generate-driver-loop"),
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("luxar.server.app.ConfigManager") as cm_cls:
                cm = cm_cls.return_value
                cm.ensure_default_config.return_value = object()
                cm.driver_library_root.return_value = Path(tmpdir) / "driver_library"
                cm.workspace_root.return_value = Path(tmpdir) / "projects"
                cm.project_root.return_value = Path(tmpdir)
                with TestClient(create_app()) as client:
                    routes = {
                        (method, route.path)
                        for route in client.app.routes
                        for method in getattr(route, "methods", set())
                        if method in {"GET", "POST"}
                    }
        self.assertTrue(deprecated.isdisjoint(routes), f"Deprecated routes still present: {sorted(deprecated & routes)}")

    def test_agent_public_tool_surface_is_strictly_vnext_only(self) -> None:
        self.assertEqual(
            {
                "runtime_run",
                "runtime_explain",
                "skills_list",
                "skill_view",
                "skill_manage",
                "skill_promote",
                "skill_execute",
                "search_driver",
                "memory_read",
                "memory_write",
                "memory_search",
                "lesson_list",
                "lesson_search",
                "lesson_record",
                "lesson_promote",
                "workspace_inspect",
                "workspace_build",
                "workspace_list_projects",
                "workspace_create_project",
                "workspace_read_file",
                "workspace_flash",
                "workspace_monitor",
                "workspace_probe",
                "workspace_write_file",
                "workspace_shell",
                "workspace_monitor_start",
                "workspace_monitor_stop",
                "workspace_monitor_status",
                "analyze_document_engineering",
                "workspace_publish_driver",
            },
            set(PUBLIC_TOOL_NAMES),
        )

    def test_tool_schema_is_isolated_from_app_factory_module(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        app_content = (repo_root / "src" / "luxar" / "server" / "app.py").read_text(encoding="utf-8")
        schema_content = (repo_root / "src" / "luxar" / "server" / "tool_schema.py").read_text(encoding="utf-8")
        self.assertIn("tool_schema", app_content)
        self.assertNotIn("TOOLS: list[dict]", app_content)
        self.assertIn("TOOLS: list[dict]", schema_content)
        self.assertIn("PUBLIC_TOOL_NAMES", schema_content)

    def test_vnext_route_registration_is_isolated_from_app_factory_module(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        app_content = (repo_root / "src" / "luxar" / "server" / "app.py").read_text(encoding="utf-8")
        surface_content = (repo_root / "src" / "luxar" / "server" / "vnext_surface.py").read_text(encoding="utf-8")
        self.assertIn("register_vnext_http_surface", app_content)
        self.assertNotIn('def api_runtime_run', app_content)
        self.assertNotIn('def api_memory_write', app_content)
        self.assertNotIn('def api_workspace_probe', app_content)
        self.assertNotIn('def api_skill_execute', app_content)
        self.assertIn("def register_vnext_http_surface", surface_content)
        self.assertIn('def api_runtime_run', surface_content)
        self.assertIn('def api_memory_write', surface_content)
        self.assertIn('def api_workspace_probe', surface_content)
        self.assertIn('def api_skill_execute', surface_content)

    def test_app_shell_surface_is_isolated_from_app_factory_module(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        app_content = (repo_root / "src" / "luxar" / "server" / "app.py").read_text(encoding="utf-8")
        shell_content = (repo_root / "src" / "luxar" / "server" / "app_shell.py").read_text(encoding="utf-8")
        self.assertIn("register_app_shell_surface", app_content)
        self.assertIn("lifespan=", app_content)
        self.assertNotIn('@app.on_event("shutdown")', app_content)
        self.assertNotIn("def serve_index", app_content)
        self.assertNotIn("def get_config", app_content)
        self.assertNotIn("def update_config", app_content)
        self.assertIn("def register_app_shell_surface", shell_content)
        self.assertIn("def serve_index", shell_content)
        self.assertIn("def get_config", shell_content)
        self.assertIn("def update_config", shell_content)

    def test_project_status_helper_is_isolated_from_app_factory_module(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        app_content = (repo_root / "src" / "luxar" / "server" / "app.py").read_text(encoding="utf-8")
        helper_content = (repo_root / "src" / "luxar" / "server" / "project_status.py").read_text(encoding="utf-8")
        self.assertIn("project_status", app_content)
        self.assertIn("return _project_status_impl", app_content)
        self.assertIn("def project_status", helper_content)

    def test_skill_extraction_postprocess_is_isolated_from_app_factory_module(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        app_content = (repo_root / "src" / "luxar" / "server" / "app.py").read_text(encoding="utf-8")
        helper_content = (repo_root / "src" / "luxar" / "server" / "skill_extraction.py").read_text(encoding="utf-8")
        self.assertIn("skill_extraction", app_content)
        self.assertIn("return _try_extract_skill_impl", app_content)
        self.assertNotIn("from luxar.core.skill_extractor import SkillExtractor", app_content)
        self.assertIn("def try_extract_skill", helper_content)

    def test_legacy_run_task_explicitly_reports_compatibility_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_task(
                config=AgentConfig(),
                project_root=tmpdir,
                workspace_root=tmpdir,
                driver_library_root=str(Path(tmpdir) / "driver_library"),
                task="你好",
            )
        self.assertEqual(LEGACY_COMPATIBILITY_MODE, result["compatibility_mode"])
        self.assertEqual(LEGACY_RUN_TASK_WARNING, result["legacy_warning"])

    def test_legacy_task_router_explicitly_reports_public_mapping(self) -> None:
        plan = TaskRouter().route(task="Generate a project that blinks LED.", project="Demo")
        self.assertEqual(LEGACY_TASK_ROUTER_COMPATIBILITY_MODE, plan.compatibility_mode)
        self.assertEqual(LEGACY_TASK_ROUTER_COMPATIBILITY_MODE, plan.intent.compatibility_mode)
        self.assertEqual("runtime_run", plan.intent.public_intent)
        self.assertEqual("runtime_run", plan.intent.public_path)

    def test_legacy_run_task_shell_uses_runtime_adapters_not_direct_legacy_workers(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        content = (repo_root / "src" / "luxar" / "tools" / "run_task.py").read_text(encoding="utf-8")
        dependency_content = (repo_root / "src" / "luxar" / "tools" / "run_task_dependencies.py").read_text(encoding="utf-8")
        self.assertNotIn("runtime_adapters", content)
        self.assertNotIn("from luxar.tools.forge_project import", content)
        self.assertNotIn("from luxar.tools.run_workflow import", content)
        self.assertNotIn("from luxar.tools.generate_driver_loop import", content)
        self.assertNotIn("from luxar.tools.review_code import", content)
        self.assertNotIn("from luxar.tools.build_project import", content)
        self.assertIn("runtime_adapters", dependency_content)

    def test_legacy_run_task_exports_are_explicitly_inventory_listed(self) -> None:
        self.assertEqual(
            {
                "LEGACY_COMPATIBILITY_MODE",
                "LEGACY_RUN_TASK_WARNING",
                "run_task",
                "run_task_stream",
            },
            set(run_task_module.RETAINED_LEGACY_RUN_TASK_EXPORTS),
        )
        self.assertEqual(
            (
                "LEGACY_COMPATIBILITY_MODE",
                "LEGACY_RUN_TASK_WARNING",
                "run_task",
                "run_task_stream",
            ),
            tuple(run_task_module.PUBLIC_RUN_TASK_EXPORTS),
        )
        self.assertEqual(tuple(run_task_module.PUBLIC_RUN_TASK_EXPORTS), tuple(run_task_module.__all__))

    def test_legacy_server_exports_are_explicitly_inventory_listed(self) -> None:
        self.assertEqual(
            {
                "LEGACY_HTTP_SURFACE_ENV",
            },
            set(server_app_module.RETAINED_LEGACY_APP_EXPORTS),
        )
        self.assertEqual(("PUBLIC_TOOL_NAMES", "create_app"), tuple(server_app_module.PUBLIC_APP_EXPORTS))
        self.assertEqual(tuple(server_app_module.PUBLIC_APP_EXPORTS), tuple(server_app_module.__all__))

    def test_legacy_http_registration_is_isolated_from_app_factory_module(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        app_content = (repo_root / "src" / "luxar" / "server" / "app.py").read_text(encoding="utf-8")
        legacy_content = (repo_root / "src" / "luxar" / "server" / "legacy_surface.py").read_text(encoding="utf-8")
        self.assertIn("register_legacy_http_surface", app_content)
        self.assertIn("register_legacy_conversation_surface", app_content)
        self.assertNotIn("def _register_legacy_http_surface", app_content)
        self.assertNotIn("def _register_legacy_conversation_surface", app_content)
        self.assertIn("def register_legacy_http_surface", legacy_content)
        self.assertIn("def register_legacy_conversation_surface", legacy_content)

    def test_chat_context_support_is_isolated_from_app_factory_module(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        app_content = (repo_root / "src" / "luxar" / "server" / "app.py").read_text(encoding="utf-8")
        support_content = (repo_root / "src" / "luxar" / "server" / "chat_support.py").read_text(encoding="utf-8")
        self.assertIn("prepare_agent_context", app_content)
        self.assertIn("repair_messages_for_reasoning_handoff", app_content)
        self.assertIn("retry_after_reasoning_handoff_repair", app_content)
        self.assertNotIn("def _prepare_agent_context", app_content)
        self.assertNotIn("def _repair_messages_for_reasoning_handoff", app_content)
        self.assertNotIn("def _retry_after_reasoning_handoff_repair", app_content)
        self.assertIn("def prepare_agent_context", support_content)
        self.assertIn("def repair_messages_for_reasoning_handoff", support_content)
        self.assertIn("def retry_after_reasoning_handoff_repair", support_content)

    def test_conversation_state_is_isolated_from_app_factory_module(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        app_content = (repo_root / "src" / "luxar" / "server" / "app.py").read_text(encoding="utf-8")
        state_content = (repo_root / "src" / "luxar" / "server" / "conversation_state.py").read_text(encoding="utf-8")
        self.assertIn("ConversationState", app_content)
        self.assertNotIn("def _get_conv", app_content)
        self.assertNotIn("def _save_conv", app_content)
        self.assertNotIn("_conv_store:", app_content)
        self.assertNotIn("_conv_cache:", app_content)
        self.assertIn("class ConversationState", state_content)
        self.assertIn("def get", state_content)
        self.assertIn("def save", state_content)

    def test_agent_loop_support_is_isolated_from_app_factory_module(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        app_content = (repo_root / "src" / "luxar" / "server" / "app.py").read_text(encoding="utf-8")
        support_content = (repo_root / "src" / "luxar" / "server" / "agent_loop_support.py").read_text(encoding="utf-8")
        self.assertNotIn("append_assistant_tool_call_message", app_content)
        self.assertNotIn("append_tool_result_message", app_content)
        self.assertNotIn("build_stream_tool_result_events", app_content)
        self.assertNotIn("update_consecutive_failures", app_content)
        self.assertNotIn("AgentLoopState", app_content)
        self.assertNotIn("append_final_assistant_message", app_content)
        self.assertNotIn("build_reasoning_handoff_retry_events", app_content)
        self.assertNotIn("def append_assistant_tool_call_message", app_content)
        self.assertNotIn("def append_tool_result_message", app_content)
        self.assertNotIn("def build_stream_tool_result_events", app_content)
        self.assertNotIn("def update_consecutive_failures", app_content)
        self.assertNotIn("class AgentLoopState", app_content)
        self.assertNotIn("def append_final_assistant_message", app_content)
        self.assertNotIn("def build_reasoning_handoff_retry_events", app_content)
        self.assertIn("def append_assistant_tool_call_message", support_content)
        self.assertIn("def append_tool_result_message", support_content)
        self.assertIn("def build_stream_tool_result_events", support_content)
        self.assertIn("def update_consecutive_failures", support_content)
        self.assertIn("class AgentLoopState", support_content)
        self.assertIn("def append_final_assistant_message", support_content)
        self.assertIn("def build_reasoning_handoff_retry_events", support_content)

    def test_agent_loop_runner_is_isolated_from_app_factory_module(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        app_content = (repo_root / "src" / "luxar" / "server" / "app.py").read_text(encoding="utf-8")
        runner_content = (repo_root / "src" / "luxar" / "server" / "agent_loop_runner.py").read_text(encoding="utf-8")
        self.assertIn("agent_loop_runner", app_content)
        self.assertNotIn("resp = client.complete_with_tools", app_content)
        self.assertNotIn("for event in client.complete_stream", app_content)
        self.assertIn("def run_agent_loop", runner_content)
        self.assertIn("def run_agent_loop_stream", runner_content)

    def test_tool_execution_support_is_isolated_from_app_factory_module(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        app_content = (repo_root / "src" / "luxar" / "server" / "app.py").read_text(encoding="utf-8")
        tool_content = (repo_root / "src" / "luxar" / "server" / "tool_execution.py").read_text(encoding="utf-8")
        self.assertIn("tool_execution", app_content)
        self.assertNotIn("def _build_tool_envelope", app_content)
        self.assertNotIn("def _format_tool_result_summary", app_content)
        self.assertNotIn("def _is_tool_result_failure", app_content)
        self.assertIn("def _execute_tool(", app_content)
        self.assertIn("return _execute_tool_impl", app_content)
        self.assertIn("class ToolExecutionEnvelope", tool_content)
        self.assertIn("def build_tool_envelope", tool_content)
        self.assertIn("def format_tool_result_summary", tool_content)
        self.assertIn("def is_tool_result_failure", tool_content)
        self.assertIn("def execute_tool", tool_content)

    def test_tool_runtime_support_is_isolated_from_app_factory_module(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        app_content = (repo_root / "src" / "luxar" / "server" / "app.py").read_text(encoding="utf-8")
        runtime_content = (repo_root / "src" / "luxar" / "server" / "tool_runtime.py").read_text(encoding="utf-8")
        self.assertIn("tool_runtime", app_content)
        self.assertIn("def _enforce_tool_call_budget", app_content)
        self.assertIn("return _enforce_tool_call_budget_impl", app_content)
        self.assertIn("def _execute_tool_with_timeout", app_content)
        self.assertIn("return await _execute_tool_with_timeout_impl", app_content)
        self.assertNotIn("asyncio.wait_for(", app_content)
        self.assertIn("class AgentToolLimitError", runtime_content)
        self.assertIn("class AgentToolTimeoutError", runtime_content)
        self.assertIn("def enforce_tool_call_budget", runtime_content)
        self.assertIn("def execute_tool_with_timeout", runtime_content)

    def test_run_task_compat_support_is_isolated_from_run_task_shell(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        shell_content = (repo_root / "src" / "luxar" / "tools" / "run_task.py").read_text(encoding="utf-8")
        compat_content = (repo_root / "src" / "luxar" / "tools" / "run_task_compat.py").read_text(encoding="utf-8")
        self.assertIn("run_task_compat", shell_content)
        self.assertNotIn("def _stream_event", shell_content)
        self.assertNotIn("def _warn_legacy_run_task_entrypoint", shell_content)
        self.assertNotIn("def _build_task_result", shell_content)
        self.assertNotIn("def _consume_stream_result", shell_content)
        self.assertNotIn('yield _stream_event(\n        "workflow_started"', shell_content)
        self.assertIn("def stream_event", compat_content)
        self.assertIn("def warn_legacy_run_task_entrypoint", compat_content)
        self.assertIn("def build_task_result", compat_content)
        self.assertIn("def consume_stream_result", compat_content)
        self.assertIn("def prepare_legacy_task_invocation", compat_content)
        self.assertIn("def build_workflow_started_event", compat_content)
        self.assertIn("def stream_legacy_task_result", compat_content)

    def test_run_task_support_is_isolated_from_run_task_shell(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        shell_content = (repo_root / "src" / "luxar" / "tools" / "run_task.py").read_text(encoding="utf-8")
        support_content = (repo_root / "src" / "luxar" / "tools" / "run_task_support.py").read_text(encoding="utf-8")
        self.assertIn("run_task_support", shell_content)
        self.assertNotIn("def _prepare_task_execution", shell_content)
        self.assertNotIn("def _build_explain_message", shell_content)
        self.assertNotIn("def _build_review_message", shell_content)
        self.assertNotIn("def _infer_driver_request", shell_content)
        self.assertIn("def prepare_task_execution", support_content)
        self.assertIn("def build_explain_message", support_content)
        self.assertIn("def build_review_message", support_content)
        self.assertIn("def infer_driver_request", support_content)

    def test_run_task_workflow_implementations_are_isolated_from_shell(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        shell_content = (repo_root / "src" / "luxar" / "tools" / "run_task.py").read_text(encoding="utf-8")
        workflow_content = (repo_root / "src" / "luxar" / "tools" / "run_task_workflows.py").read_text(encoding="utf-8")
        self.assertNotIn("run_task_workflows", shell_content)
        self.assertIn("def stream_project_status", workflow_content)
        self.assertIn("def stream_review_or_fix", workflow_content)
        self.assertIn("def stream_generate_driver", workflow_content)
        self.assertIn("def stream_debug_project", workflow_content)
        self.assertIn("def stream_forge_project", workflow_content)
        self.assertIn("def stream_task_core", workflow_content)

    def test_run_task_dispatch_is_isolated_from_shell(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        shell_content = (repo_root / "src" / "luxar" / "tools" / "run_task.py").read_text(encoding="utf-8")
        dispatch_content = (repo_root / "src" / "luxar" / "tools" / "run_task_dispatch.py").read_text(encoding="utf-8")
        self.assertIn("run_task_dispatch", shell_content)
        self.assertNotIn("def _stream_project_status", shell_content)
        self.assertNotIn("def _stream_review_or_fix", shell_content)
        self.assertNotIn("def _stream_generate_driver", shell_content)
        self.assertNotIn("def _stream_debug_project", shell_content)
        self.assertNotIn("def _stream_forge_project", shell_content)
        self.assertNotIn("def _stream_task_core", shell_content)
        self.assertIn("def stream_task_core", dispatch_content)

    def test_run_task_entrypoints_are_isolated_from_shell(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        shell_content = (repo_root / "src" / "luxar" / "tools" / "run_task.py").read_text(encoding="utf-8")
        entrypoint_content = (repo_root / "src" / "luxar" / "tools" / "run_task_entrypoints.py").read_text(encoding="utf-8")
        self.assertIn("run_task_entrypoints", shell_content)
        self.assertNotIn("yield _build_workflow_started_event", shell_content)
        self.assertNotIn("yield from _stream_legacy_task_result", shell_content)
        self.assertIn("def run_task_stream_entrypoint", entrypoint_content)
        self.assertIn("def run_task_entrypoint", entrypoint_content)

    def test_run_task_dependency_bundles_are_isolated_from_shell(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        shell_content = (repo_root / "src" / "luxar" / "tools" / "run_task.py").read_text(encoding="utf-8")
        dependency_content = (repo_root / "src" / "luxar" / "tools" / "run_task_dependencies.py").read_text(encoding="utf-8")
        self.assertIn("run_task_dependencies", shell_content)
        self.assertIn("**_build_stream_entrypoint_dependencies(", shell_content)
        self.assertIn("**_build_sync_entrypoint_dependencies(", shell_content)
        self.assertIn("def build_stream_entrypoint_dependencies", dependency_content)
        self.assertIn("def build_sync_entrypoint_dependencies", dependency_content)
        self.assertIn("ProjectManager", dependency_content)
        self.assertIn("runtime_adapters", dependency_content)


if __name__ == "__main__":
    unittest.main()
