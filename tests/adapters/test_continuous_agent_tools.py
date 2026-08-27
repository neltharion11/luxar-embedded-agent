from __future__ import annotations

from pathlib import Path

from luxar.adapters.continuous_agent_tools import create_core_tool_registry
from luxar.database.persistence import KnowledgeMatch
from luxar.domain.agent.code_changes import ChangeBundle, ChangeBundleValidation
from luxar.domain.continuous_agent.steps import ToolCall
from luxar.domain.devices import FlashEvidence, MonitorEvidence, SerialPortInfo
from luxar.domain.evidence import BuildEvidence
from luxar.domain.repairs import ProjectFile
from luxar.ports.agent_tool import AgentToolExecutionContext


class _Workspace:
    def read_project_files(self, project_path: Path) -> list[ProjectFile]:
        assert project_path.name == "test4"
        return [
            ProjectFile(
                path="main/main.c",
                content="void app_main(void) {}\n",
            )
        ]


class _Builder:
    def build(self, project_path: Path) -> BuildEvidence:
        assert project_path.name == "test4"
        return BuildEvidence(
            success=True,
            command=["idf.py", "build"],
            return_code=0,
        )


class _Device:
    def __init__(self) -> None:
        self.flash_calls = 0
        self.monitor_calls = 0

    def discover_serial_ports(self) -> list[SerialPortInfo]:
        return [SerialPortInfo(name="COM4", description="ESP32")]

    def flash(self, project_path: Path, port: str) -> FlashEvidence:
        self.flash_calls += 1
        return FlashEvidence(
            success=True,
            command=["idf.py", "-p", port, "flash"],
            return_code=0,
            port=port,
        )

    def monitor(
        self,
        project_path: Path,
        port: str,
        timeout_seconds: int,
    ) -> MonitorEvidence:
        self.monitor_calls += 1
        return MonitorEvidence(
            command=["idf.py", "-p", port, "monitor"],
            port=port,
            capture_timeout_seconds=timeout_seconds,
            captured_log="I (10) app: ready",
            terminated_by_timeout=True,
        )


class _CodeExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(
        self,
        project_path: Path,
        bundle: ChangeBundle,
    ) -> ChangeBundleValidation:
        self.calls += 1
        assert project_path.name == "test4"
        return ChangeBundleValidation(
            before_fingerprint="a" * 64,
            after_fingerprint="b" * 64,
            changed_files=[bundle.changes[0].path],
            diff_summary=[f"create: {bundle.changes[0].path}"],
        )


class _Knowledge:
    def search(
        self,
        *,
        project_key: str,
        query: str,
        limit: int = 6,
    ) -> list[KnowledgeMatch]:
        assert project_key == "0:test4"
        assert query == "TWAI 终端电阻"
        assert limit == 3
        return [
            KnowledgeMatch(
                document_id="doc-1",
                title="TWAI 指南",
                source_uri="docs/twai.md",
                ordinal=2,
                content="总线两端应各有一个终端电阻。",
                score=0.91,
            )
        ]

def _context(project_path: Path) -> AgentToolExecutionContext:
    return AgentToolExecutionContext(
        session_id="session-1",
        turn_id="turn-1",
        project_key="0:test4",
        project_path=project_path,
    )


def test_core_registry_wraps_existing_read_build_and_device_ports(
    tmp_path: Path,
) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    device = _Device()
    registry = create_core_tool_registry(
        workspace=_Workspace(),  # type: ignore[arg-type]
        builder=_Builder(),  # type: ignore[arg-type]
        flasher=device,
        monitor=device,
    )

    assert [item.name for item in registry.descriptors()] == [
        "device.discover",
        "device.flash",
        "device.monitor",
        "espidf.build",
        "project.inspect",
        "workspace.read_project",
    ]

    read = registry.dispatch(
        ToolCall(
            call_id="read",
            tool_name="workspace.read_project",
            arguments={},
        ),
        _context(project),
    )
    build = registry.dispatch(
        ToolCall(call_id="build", tool_name="espidf.build", arguments={}),
        _context(project),
    )
    discover = registry.dispatch(
        ToolCall(
            call_id="discover",
            tool_name="device.discover",
            arguments={},
        ),
        _context(project),
    )
    monitor = registry.dispatch(
        ToolCall(
            call_id="monitor",
            tool_name="device.monitor",
            arguments={"serial_port": "COM4", "timeout_seconds": 5},
        ),
        _context(project),
    )

    assert read.call.result["file_count"] == 1  # type: ignore[index]
    assert build.call.status == "succeeded"
    assert discover.call.result["ports"][0]["name"] == "COM4"  # type: ignore[index]
    assert monitor.call.result["captured_log"] == "I (10) app: ready"  # type: ignore[index]
    assert device.monitor_calls == 1


def test_flash_wrapper_requires_approval_before_touching_device(
    tmp_path: Path,
) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    device = _Device()
    registry = create_core_tool_registry(flasher=device)
    call = ToolCall(
        call_id="flash",
        tool_name="device.flash",
        arguments={"serial_port": "COM4"},
    )

    pending = registry.dispatch(call, _context(project))
    assert pending.pending_approval is not None
    assert device.flash_calls == 0

    completed = registry.dispatch(call, _context(project), approved=True)
    assert completed.call.status == "succeeded"
    assert device.flash_calls == 1


def test_device_tools_reject_serial_port_outside_discovery_before_approval(
    tmp_path: Path,
) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    device = _Device()
    registry = create_core_tool_registry(flasher=device, monitor=device)

    flash = registry.dispatch(
        ToolCall(
            call_id="unsafe-flash",
            tool_name="device.flash",
            arguments={"serial_port": "COM99"},
        ),
        _context(project),
    )
    monitor = registry.dispatch(
        ToolCall(
            call_id="unsafe-monitor",
            tool_name="device.monitor",
            arguments={"serial_port": "COM99", "timeout_seconds": 5},
        ),
        _context(project),
    )

    assert flash.pending_approval is None
    assert flash.call.failure is not None
    assert flash.call.failure.code == "serial_port_not_discovered"
    assert monitor.call.failure is not None
    assert monitor.call.failure.category == "policy"
    assert device.flash_calls == 0
    assert device.monitor_calls == 0


def test_build_failure_is_normalized_as_tool_failure(tmp_path: Path) -> None:
    class FailedBuilder:
        def build(self, project_path: Path) -> BuildEvidence:
            return BuildEvidence(
                success=False,
                command=["idf.py", "build"],
                return_code=2,
                error_category="source",
                stderr_summary="compile failed",
            )

    project = tmp_path / "test4"
    project.mkdir()
    registry = create_core_tool_registry(builder=FailedBuilder())

    outcome = registry.dispatch(
        ToolCall(call_id="build", tool_name="espidf.build", arguments={}),
        _context(project),
    )

    assert outcome.call.status == "failed"
    assert outcome.call.failure is not None
    assert outcome.call.failure.category == "tool"
    assert outcome.call.failure.code == "source"


def test_change_bundle_requires_approval_and_uses_typed_executor(
    tmp_path: Path,
) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    executor = _CodeExecutor()
    registry = create_core_tool_registry(code_executor=executor)
    call = ToolCall(
        call_id="apply-main",
        tool_name="workspace.apply_change_bundle",
        arguments={
            "bundle": {
                "bundle_id": "bundle-1",
                "task_id": "task-1",
                "description": "新增入口",
                "changes": [
                    {
                        "operation": "create",
                        "path": "main/main.c",
                        "content": "void app_main(void) {}\n",
                        "expected_sha256": None,
                    }
                ],
                "allowed_paths": ["main/**"],
                "preserves": [],
            }
        },
    )

    waiting = registry.dispatch(call, _context(project))
    completed = registry.dispatch(call, _context(project), approved=True)

    assert waiting.pending_approval is not None
    assert executor.calls == 1
    assert completed.call.status == "succeeded"
    assert completed.call.result["changed_files"] == ["main/main.c"]  # type: ignore[index]


def test_knowledge_search_is_project_scoped_and_returns_evidence(
    tmp_path: Path,
) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    registry = create_core_tool_registry(knowledge=_Knowledge())

    result = registry.dispatch(
        ToolCall(
            call_id="knowledge",
            tool_name="knowledge.search",
            arguments={"query": "TWAI 终端电阻", "limit": 3},
        ),
        _context(project),
    )

    assert result.call.status == "succeeded"
    assert result.call.result["count"] == 1  # type: ignore[index]
    assert result.call.result["matches"][0]["source_uri"] == "docs/twai.md"  # type: ignore[index]
