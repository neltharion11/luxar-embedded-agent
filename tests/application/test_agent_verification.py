from __future__ import annotations

import hashlib
from pathlib import Path

from luxar.application.agent_graph import build_agent_graph
from luxar.application.agent_state import AgentRuntimeContext
from luxar.domain.agent.changes import CapabilityChange, ChangeSet
from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.agent.project_inspector import ProjectModelExtractor
from luxar.domain.agent.code_changes import (
    ChangeBundle,
    ChangeBundleValidation,
    FileChange,
)
from luxar.domain.agent.runtime_verification import (
    ProtocolProbeEvidence,
    ProtocolProbeSpec,
    RuntimeScenarioEvidence,
    RuntimeScenarioSpec,
)
from luxar.domain.agent.tasks import build_task_graph
from luxar.domain.agent.verification import (
    ComponentTestEvidence,
    ComponentTestSpec,
    DeviceLogAssertion,
    FirmwareMetricAssertion,
    FirmwareResourceEvidence,
    SourceAssertion,
    VerificationPlan,
)
from luxar.domain.devices import (
    DeviceLogDiagnostic,
    FlashEvidence,
    MonitorEvidence,
)
from luxar.domain.evidence import BuildDiagnostic, BuildEvidence
from luxar.domain.repairs import ProjectFile


class _Builder:
    def __init__(self) -> None:
        self.calls = 0

    def build(self, project_path: Path) -> BuildEvidence:
        del project_path
        self.calls += 1
        return BuildEvidence(
            success=True,
            command=["idf.py", "build"],
            return_code=0,
        )


class _FailingBuilder:
    def __init__(self, evidence: BuildEvidence) -> None:
        self.evidence = evidence
        self.calls = 0

    def build(self, project_path: Path) -> BuildEvidence:
        del project_path
        self.calls += 1
        return self.evidence


class _Monitor:
    def __init__(self, evidence: list[MonitorEvidence]) -> None:
        self.evidence = evidence
        self.calls = 0

    def monitor(
        self,
        project_path: Path,
        port: str,
        timeout_seconds: int,
    ) -> MonitorEvidence:
        del project_path
        result = self.evidence[min(self.calls, len(self.evidence) - 1)]
        self.calls += 1
        assert result.port == port
        assert result.capture_timeout_seconds == timeout_seconds
        return result


class _Flasher:
    def __init__(self, monitor_evidence: MonitorEvidence) -> None:
        self.monitor_evidence = monitor_evidence
        self.calls = 0

    def flash_and_monitor(
        self,
        project_path: Path,
        port: str,
        timeout_seconds: int,
    ) -> tuple[FlashEvidence, MonitorEvidence]:
        del project_path
        self.calls += 1
        assert port == self.monitor_evidence.port
        assert timeout_seconds == self.monitor_evidence.capture_timeout_seconds
        return (
            FlashEvidence(
                success=True,
                command=["idf.py", "-p", port, "flash"],
                return_code=0,
                port=port,
            ),
            self.monitor_evidence,
        )


class _ComponentTester:
    def __init__(self, evidence: list[ComponentTestEvidence]) -> None:
        self.evidence = evidence
        self.calls = 0

    def run_component_test(
        self,
        project_path: Path,
        spec: ComponentTestSpec,
    ) -> ComponentTestEvidence:
        del project_path
        result = self.evidence[min(self.calls, len(self.evidence) - 1)]
        self.calls += 1
        assert result.test_id == spec.test_id
        return result


class _FirmwareInspector:
    def __init__(self, evidence: FirmwareResourceEvidence) -> None:
        self.evidence = evidence
        self.calls = 0

    def inspect_firmware(self, project_path: Path) -> FirmwareResourceEvidence:
        del project_path
        self.calls += 1
        return self.evidence


class _ProtocolProbe:
    def __init__(self, evidence: list[ProtocolProbeEvidence]) -> None:
        self.evidence = evidence
        self.calls = 0

    def run_protocol_probe(
        self,
        project_path: Path,
        spec: ProtocolProbeSpec,
    ) -> ProtocolProbeEvidence:
        del project_path
        result = self.evidence[min(self.calls, len(self.evidence) - 1)]
        self.calls += 1
        assert result.probe_id == spec.probe_id
        return result


class _RuntimeScenarioRunner:
    def __init__(self, evidence: dict[str, RuntimeScenarioEvidence]) -> None:
        self.evidence = evidence
        self.calls = 0

    def run_runtime_scenario(
        self,
        project_path: Path,
        spec: RuntimeScenarioSpec,
    ) -> RuntimeScenarioEvidence:
        del project_path
        self.calls += 1
        return self.evidence[spec.scenario_id]


def _component_evidence(*, success: bool) -> ComponentTestEvidence:
    return ComponentTestEvidence(
        test_id="gpio-driver-host",
        success=success,
        runner="pytest",
        command=["pytest", "tests/host/test_gpio_driver.py"],
        return_code=0 if success else 1,
        passed=3 if success else 2,
        failed=0 if success else 1,
    )


def _firmware_evidence(*, app_size_bytes: int = 700_000) -> FirmwareResourceEvidence:
    return FirmwareResourceEvidence(
        command=["idf.py", "size"],
        app_size_bytes=app_size_bytes,
        app_partition_size_bytes=1_000_000,
        flash_size_bytes=4_000_000,
        dram_static_bytes=80_000,
        iram_static_bytes=50_000,
        minimum_task_stack_headroom_bytes=2_048,
        partition_table_valid=True,
    )


def _monitor_evidence(
    log: str,
    *,
    diagnostics: list[DeviceLogDiagnostic] | None = None,
) -> MonitorEvidence:
    return MonitorEvidence(
        command=["idf.py", "-p", "COM3", "monitor"],
        port="COM3",
        capture_timeout_seconds=10,
        captured_log=log,
        terminated_by_timeout=True,
        diagnostics=diagnostics or [],
    )


def _state_for_verification(
    plan: VerificationPlan,
) -> tuple[dict[str, object], str]:
    objective = ProjectObjective(
        objective_id="verification-objective",
        title="验证 GPIO33 输出",
        description="验证源码、构建和设备运行证据",
    )
    change_set = ChangeSet(
        changes=[
            CapabilityChange(
                operation="add",
                capability_id="gpio.output:P33",
                desired_state={"pin": 33, "level": 1},
            )
        ]
    )
    graph = build_task_graph(objective, change_set, verification_plan=plan)
    for task in graph.tasks:
        if task.kind != "verify_acceptance":
            graph = graph.update_task(task.task_id, status="passed", attempts=1)
    verify_task = next(
        task for task in graph.tasks if task.kind == "verify_acceptance"
    )
    project_files = [
        ProjectFile(
            path="main/main.c",
            content="void app_main(void) { gpio_set_level(GPIO_NUM_33, 1); }\n",
        )
    ]
    return (
        {
            "objective": objective,
            "change_set": change_set,
            "project_files": project_files,
            "inspection_complete": True,
            "hardware_validated": True,
            "task_graph": graph,
            "verification_plan": plan,
            "evidence_ids": [
                f"task:{task.task_id}"
                for task in graph.tasks
                if task.kind == "code_change"
            ],
            "trace": [],
            "max_steps": 20,
        },
        verify_task.task_id,
    )


def test_source_and_build_evidence_complete_without_claiming_hardware() -> None:
    plan = VerificationPlan(
        source_assertions=[
            SourceAssertion(
                assertion_id="gpio33-source",
                operator="contains",
                pattern="GPIO_NUM_33",
                path="main/main.c",
                description="源码包含 GPIO33",
            )
        ],
        require_build=True,
    )
    state, verify_task_id = _state_for_verification(plan)
    builder = _Builder()

    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            build_executor=builder,
            project_path=Path("F:/LUXAR"),
        ),
    )

    assert result["status"] == "completed"
    assert result["acceptance_passed"] is True
    assert result["build_verified"] is True
    assert result["hardware_function_verified"] is False
    assert f"build:{verify_task_id}" in result["evidence_ids"]
    assert result["verification_runs"][verify_task_id].success is True
    assert builder.calls == 1


def test_build_success_cannot_replace_required_device_evidence() -> None:
    plan = VerificationPlan(require_build=True, require_device=True)
    state, verify_task_id = _state_for_verification(plan)
    builder = _Builder()

    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            build_executor=builder,
            project_path=Path("F:/LUXAR"),
        ),
    )

    verify_task = next(
        task for task in result["task_graph"].tasks
        if task.task_id == verify_task_id
    )
    assert result["status"] == "blocked"
    assert result.get("acceptance_passed") is not True
    assert result["build_verified"] is True
    assert result["hardware_function_verified"] is False
    assert f"build:{verify_task_id}" in result["evidence_ids"]
    assert verify_task.status == "blocked"
    assert builder.calls == 1


def test_device_log_failure_is_retried_then_can_pass() -> None:
    plan = VerificationPlan(
        require_build=True,
        require_device=True,
        device_assertions=[
            DeviceLogAssertion(
                assertion_id="runtime-healthy",
                operator="no_fatal_diagnostics",
                description="设备运行无致命诊断",
            ),
            DeviceLogAssertion(
                assertion_id="boot-ok",
                operator="contains",
                pattern="boot ok",
                description="设备输出启动成功标志",
            ),
        ],
    )
    state, verify_task_id = _state_for_verification(plan)
    builder = _Builder()
    monitor = _Monitor(
        [
            _monitor_evidence(
                "boot ok\nTask watchdog got triggered",
                diagnostics=[
                    DeviceLogDiagnostic(
                        kind="watchdog",
                        summary="任务看门狗触发",
                    )
                ],
            ),
            _monitor_evidence("boot ok\napplication ready"),
        ]
    )

    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            build_executor=builder,
            project_path=Path("F:/LUXAR"),
            monitor=monitor,
            serial_port="COM3",
        ),
    )

    assert result["status"] == "completed"
    assert result["acceptance_passed"] is True
    assert result["build_verified"] is True
    assert result["hardware_function_verified"] is True
    assert result["verification_runs"][verify_task_id].success is True
    assert len(result["failure_history"]) == 1
    assert builder.calls == 1
    assert monitor.calls == 2


def test_repeated_device_log_failure_blocks_verification() -> None:
    plan = VerificationPlan(
        require_build=True,
        require_device=True,
        device_assertions=[
            DeviceLogAssertion(
                assertion_id="runtime-healthy",
                operator="no_fatal_diagnostics",
                description="设备运行无致命诊断",
            )
        ],
    )
    state, verify_task_id = _state_for_verification(plan)
    builder = _Builder()
    unhealthy = _monitor_evidence(
        "Guru Meditation Error",
        diagnostics=[
            DeviceLogDiagnostic(
                kind="panic",
                summary="设备 panic",
            )
        ],
    )
    monitor = _Monitor([unhealthy])

    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            build_executor=builder,
            project_path=Path("F:/LUXAR"),
            monitor=monitor,
            serial_port="COM3",
        ),
    )

    verify_task = next(
        task for task in result["task_graph"].tasks
        if task.task_id == verify_task_id
    )
    assert result["status"] == "blocked"
    assert result["hardware_function_verified"] is False
    assert result["verification_runs"][verify_task_id].success is False
    assert verify_task.status == "blocked"
    assert len(result["failure_history"]) == 2
    assert result["failure_history"][1].repeated is True
    assert monitor.calls == 2


def test_device_retry_reuses_successful_flash_instead_of_flashing_again() -> None:
    plan = VerificationPlan(
        require_build=True,
        require_flash=True,
        require_device=True,
        device_assertions=[
            DeviceLogAssertion(
                assertion_id="boot-ok",
                operator="contains",
                pattern="boot ok",
                description="设备输出启动成功标志",
            )
        ],
    )
    state, verify_task_id = _state_for_verification(plan)
    state["task_graph"] = state["task_graph"].update_task(
        verify_task_id,
        requires_approval=False,
    )
    builder = _Builder()
    flasher = _Flasher(_monitor_evidence("boot incomplete"))
    monitor = _Monitor([_monitor_evidence("boot ok\napplication ready")])

    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            build_executor=builder,
            flasher=flasher,
            monitor=monitor,
            project_path=Path("F:/LUXAR"),
            serial_port="COM3",
        ),
    )

    assert result["status"] == "completed"
    assert result["verification_runs"][verify_task_id].success is True
    assert builder.calls == 1
    assert flasher.calls == 1
    assert monitor.calls == 1


def test_component_test_and_firmware_limits_produce_independent_evidence() -> None:
    plan = VerificationPlan(
        component_tests=[
            ComponentTestSpec(
                test_id="gpio-driver-host",
                component_id="gpio_driver",
                runner="pytest",
                target="tests/host/test_gpio_driver.py",
                description="GPIO 驱动 Host 测试通过",
            )
        ],
        require_build=True,
        firmware_assertions=[
            FirmwareMetricAssertion(
                assertion_id="app-fits",
                metric="app_partition_free_bytes",
                operator="gte",
                expected=200_000,
                description="应用分区至少保留 200 KB",
            ),
            FirmwareMetricAssertion(
                assertion_id="stack-safe",
                metric="minimum_task_stack_headroom_bytes",
                operator="gte",
                expected=1_024,
                description="任务栈余量至少 1 KB",
            ),
        ],
    )
    state, verify_task_id = _state_for_verification(plan)
    tester = _ComponentTester([_component_evidence(success=True)])
    inspector = _FirmwareInspector(_firmware_evidence())

    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            project_path=Path("F:/LUXAR"),
            component_tester=tester,
            build_executor=_Builder(),
            firmware_inspector=inspector,
        ),
    )

    assert result["status"] == "completed"
    assert "component-test:gpio-driver-host" in result["evidence_ids"]
    assert "firmware-assert:app-fits" in result["evidence_ids"]
    assert "firmware-assert:stack-safe" in result["evidence_ids"]
    run = result["verification_runs"][verify_task_id]
    assert run.component_test_evidence[0].passed == 3
    assert run.firmware_resource_evidence.app_partition_free_bytes == 300_000
    assert tester.calls == 1
    assert inspector.calls == 1


def test_repeated_component_test_failure_blocks_before_build() -> None:
    plan = VerificationPlan(
        component_tests=[
            ComponentTestSpec(
                test_id="gpio-driver-host",
                component_id="gpio_driver",
                runner="pytest",
                target="tests/host/test_gpio_driver.py",
                description="GPIO 驱动 Host 测试通过",
            )
        ],
        require_build=True,
    )
    state, verify_task_id = _state_for_verification(plan)
    tester = _ComponentTester([_component_evidence(success=False)])
    builder = _Builder()

    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            project_path=Path("F:/LUXAR"),
            component_tester=tester,
            build_executor=builder,
        ),
    )

    verify_task = next(
        task for task in result["task_graph"].tasks
        if task.task_id == verify_task_id
    )
    assert result["status"] == "blocked"
    assert verify_task.status == "blocked"
    assert tester.calls == 2
    assert builder.calls == 0
    assert result["failure_history"][1].repeated is True


def test_firmware_size_over_limit_retries_then_blocks() -> None:
    plan = VerificationPlan(
        require_build=True,
        firmware_assertions=[
            FirmwareMetricAssertion(
                assertion_id="app-fits",
                metric="app_size_bytes",
                operator="lte",
                expected=900_000,
                description="应用镜像不能超过 900 KB",
            )
        ],
    )
    state, verify_task_id = _state_for_verification(plan)
    builder = _Builder()
    inspector = _FirmwareInspector(_firmware_evidence(app_size_bytes=950_000))

    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            project_path=Path("F:/LUXAR"),
            build_executor=builder,
            firmware_inspector=inspector,
        ),
    )

    verify_task = next(
        task for task in result["task_graph"].tasks
        if task.task_id == verify_task_id
    )
    assert result["status"] == "blocked"
    assert verify_task.status == "blocked"
    assert result["build_verified"] is True
    assert result["verification_runs"][verify_task_id].success is False
    assert builder.calls == 1
    assert inspector.calls == 2


def test_protocol_and_all_runtime_scenario_types_complete_with_evidence() -> None:
    probe_spec = ProtocolProbeSpec(
        probe_id="mqtt-loop",
        protocol="mqtt",
        operation="publish_subscribe",
        target_ref="mqtt.primary",
        requires_device=True,
        minimum_successful_exchanges=2,
        maximum_latency_ms=100,
        description="MQTT 发布订阅回环",
    )
    scenarios = [
        RuntimeScenarioSpec(
            scenario_id="wifi-reconnect",
            kind="reconnect",
            target_ref="wifi.primary",
            duration_seconds=30,
            maximum_recovery_time_ms=2_000,
            description="Wi-Fi 断线重连",
        ),
        RuntimeScenarioSpec(
            scenario_id="mqtt-error-injection",
            kind="error_injection",
            target_ref="mqtt.primary",
            duration_seconds=10,
            maximum_error_count=0,
            description="MQTT 错误注入恢复",
        ),
        RuntimeScenarioSpec(
            scenario_id="mqtt-soak",
            kind="soak",
            target_ref="mqtt.primary",
            duration_seconds=60,
            minimum_heap_headroom_bytes=64_000,
            description="MQTT 长期运行",
        ),
    ]
    plan = VerificationPlan(
        require_build=True,
        require_device=True,
        protocol_probes=[probe_spec],
        runtime_scenarios=scenarios,
    )
    state, verify_task_id = _state_for_verification(plan)
    builder = _Builder()
    monitor = _Monitor([_monitor_evidence("boot ok\napplication ready")])
    probe = _ProtocolProbe(
        [
            ProtocolProbeEvidence(
                probe_id="mqtt-loop",
                protocol="mqtt",
                operation="publish_subscribe",
                success=True,
                attempts=2,
                successful_exchanges=2,
                maximum_latency_ms=50,
            )
        ]
    )
    scenario_runner = _RuntimeScenarioRunner(
        {
            "wifi-reconnect": RuntimeScenarioEvidence(
                scenario_id="wifi-reconnect",
                kind="reconnect",
                success=True,
                observed_duration_seconds=30,
                disconnect_count=2,
                recovery_count=2,
                maximum_recovery_time_ms=500,
            ),
            "mqtt-error-injection": RuntimeScenarioEvidence(
                scenario_id="mqtt-error-injection",
                kind="error_injection",
                success=True,
                observed_duration_seconds=10,
                injected_fault_count=3,
                recovered_fault_count=3,
            ),
            "mqtt-soak": RuntimeScenarioEvidence(
                scenario_id="mqtt-soak",
                kind="soak",
                success=True,
                observed_duration_seconds=60,
                minimum_free_heap_bytes=80_000,
            ),
        }
    )

    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            project_path=Path("F:/LUXAR"),
            build_executor=builder,
            monitor=monitor,
            serial_port="COM3",
            protocol_probe=probe,
            runtime_scenario_runner=scenario_runner,
        ),
    )

    assert result["status"] == "completed"
    assert result["hardware_function_verified"] is True
    assert "protocol-probe:mqtt-loop" in result["evidence_ids"]
    assert "runtime-scenario:wifi-reconnect" in result["evidence_ids"]
    assert "runtime-scenario:mqtt-error-injection" in result["evidence_ids"]
    assert "runtime-scenario:mqtt-soak" in result["evidence_ids"]
    run = result["verification_runs"][verify_task_id]
    assert len(run.protocol_probe_evidence) == 1
    assert len(run.runtime_scenario_evidence) == 3
    assert builder.calls == 1
    assert monitor.calls == 1
    assert probe.calls == 1
    assert scenario_runner.calls == 3


def test_repeated_protocol_timeout_blocks_verification() -> None:
    plan = VerificationPlan(
        require_build=False,
        protocol_probes=[
            ProtocolProbeSpec(
                probe_id="http-loop",
                protocol="http",
                operation="request_response",
                target_ref="http.loopback",
                description="HTTP 回环",
            )
        ],
    )
    state, verify_task_id = _state_for_verification(plan)
    probe = _ProtocolProbe(
        [
            ProtocolProbeEvidence(
                probe_id="http-loop",
                protocol="http",
                operation="request_response",
                success=False,
                attempts=1,
                failure_reason="timeout",
            )
        ]
    )

    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            project_path=Path("F:/LUXAR"),
            protocol_probe=probe,
        ),
    )

    verify_task = next(
        task for task in result["task_graph"].tasks
        if task.task_id == verify_task_id
    )
    assert result["status"] == "blocked"
    assert verify_task.status == "blocked"
    assert probe.calls == 2
    assert result["failure_history"][1].repeated is True


def test_soak_heap_threshold_failure_retries_then_blocks() -> None:
    scenario = RuntimeScenarioSpec(
        scenario_id="mqtt-soak",
        kind="soak",
        target_ref="mqtt.primary",
        duration_seconds=60,
        minimum_heap_headroom_bytes=64_000,
        description="MQTT 长期运行",
    )
    plan = VerificationPlan(
        require_build=True,
        require_device=True,
        runtime_scenarios=[scenario],
    )
    state, verify_task_id = _state_for_verification(plan)
    runner = _RuntimeScenarioRunner(
        {
            "mqtt-soak": RuntimeScenarioEvidence(
                scenario_id="mqtt-soak",
                kind="soak",
                success=True,
                observed_duration_seconds=60,
                minimum_free_heap_bytes=32_000,
            )
        }
    )

    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            project_path=Path("F:/LUXAR"),
            build_executor=_Builder(),
            monitor=_Monitor([_monitor_evidence("boot ok")]),
            serial_port="COM3",
            runtime_scenario_runner=runner,
        ),
    )

    verify_task = next(
        task for task in result["task_graph"].tasks
        if task.task_id == verify_task_id
    )
    assert result["status"] == "blocked"
    assert result["hardware_function_verified"] is False
    assert verify_task.status == "blocked"
    assert runner.calls == 2


def test_build_failure_exposes_structured_source_recovery_decision() -> None:
    plan = VerificationPlan(require_build=True)
    state, verify_task_id = _state_for_verification(plan)
    builder = _FailingBuilder(
        BuildEvidence(
            success=False,
            command=["idf.py", "build"],
            return_code=1,
            error_category="source",
            diagnostics=[
                BuildDiagnostic(
                    file="main/main.c",
                    line=12,
                    severity="error",
                    code="E001",
                    message="unknown identifier",
                )
            ],
        )
    )

    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            project_path=Path("F:/LUXAR"),
            build_executor=builder,
        ),
    )

    verify_task = next(
        task for task in result["task_graph"].tasks
        if task.task_id == verify_task_id
    )
    assert result["status"] == "blocked"
    assert verify_task.status == "blocked"
    assert result["build_recovery"].action == "repair_source"
    assert result["build_recovery"].target_files == ["main/main.c"]
    assert result["failure_history"][0].category == "semantic"
    assert builder.calls == 2


def test_source_build_failure_is_reflected_into_code_repair_before_retry() -> None:
    plan = VerificationPlan(require_build=True)
    state, verify_task_id = _state_for_verification(plan)
    project_files = state["project_files"]
    assert isinstance(project_files, list)
    state["project_model"] = ProjectModelExtractor().extract(project_files)
    code_task = next(
        task for task in state["task_graph"].tasks
        if task.kind == "code_change"
    )

    class RecoveringBuilder:
        def __init__(self) -> None:
            self.calls = 0

        def build(self, project_path: Path) -> BuildEvidence:
            del project_path
            self.calls += 1
            if self.calls == 1:
                return BuildEvidence(
                    success=False,
                    command=["idf.py", "build"],
                    return_code=1,
                    error_category="source",
                    diagnostics=[
                        BuildDiagnostic(
                            file="main/main.c",
                            line=12,
                            severity="error",
                            code="E001",
                            message="unknown identifier gpio_state",
                        )
                    ],
                )
            return BuildEvidence(
                success=True,
                command=["idf.py", "build"],
                return_code=0,
            )

    class ReflectiveEngineer:
        def __init__(self) -> None:
            self.calls = 0
            self.feedback: list[str] = []
            self.build_evidence: BuildEvidence | None = None

        def create_bundle(
            self,
            objective,
            task,
            project_model,
            files,
            build_evidence=None,
            failure_feedback=None,
            reuse_candidates=None,
        ):
            del objective, project_model, files, reuse_candidates
            self.calls += 1
            self.feedback = list(failure_feedback or [])
            self.build_evidence = build_evidence
            return {
                "bundle_id": "reflection-repair",
                "task_id": task.task_id,
                "description": "repair compiler diagnostic",
                "allowed_paths": ["main/main.c"],
                "changes": [
                    {
                        "operation": "modify",
                        "path": "main/main.c",
                        "content": (
                            "void app_main(void) { "
                            "gpio_set_level(GPIO_NUM_33, 1); }\n"
                        ),
                        "expected_sha256": None,
                    }
                ],
            }

    class AcceptingExecutor:
        calls = 0

        def execute(self, project_path: Path, bundle) -> ChangeBundleValidation:
            del project_path, bundle
            self.calls += 1
            return ChangeBundleValidation(
                before_fingerprint="a" * 64,
                after_fingerprint="b" * 64,
                changed_files=["main/main.c"],
                diff_summary=["modify: main/main.c"],
            )

    builder = RecoveringBuilder()
    engineer = ReflectiveEngineer()
    executor = AcceptingExecutor()
    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            project_path=Path("F:/LUXAR"),
            build_executor=builder,
            code_engineer=engineer,
            code_executor=executor,
        ),
    )

    assert result["status"] == "completed"
    assert result["verification_runs"][verify_task_id].success is True
    assert builder.calls == 2
    assert engineer.calls == 1
    assert executor.calls == 1
    assert engineer.build_evidence is not None
    assert engineer.build_evidence.diagnostics[0].line == 12
    assert any("unknown identifier gpio_state" in item for item in engineer.feedback)
    repaired_task = next(
        task for task in result["task_graph"].tasks
        if task.task_id == code_task.task_id
    )
    assert repaired_task.attempts == 2


_OLED_CMAKE = (
    'idf_component_register(SRCS "pdf1.c"\n'
    '                    INCLUDE_DIRS ".")\n'
)
_OLED_SOURCE = (
    "#include \"driver/i2c.h\"\n"
    "void app_main(void) {\n"
    "    i2c_config_t conf = {0};\n"
    "    i2c_master_start(NULL);\n"
    "}\n"
)
_OLED_ASSERTION = DeviceLogAssertion(
    assertion_id="oled-displayed",
    operator="contains",
    pattern="Displayed helloworld",
    description="设备日志出现显示成功标志",
)


def _oled_verification_state() -> tuple[dict[str, object], str, str]:
    """构造 pdf1 场景：构建依赖修复 + preserve I2C，代码任务已有变更包。"""

    project_files = [
        ProjectFile(path="main/pdf1.c", content=_OLED_SOURCE),
        ProjectFile(path="main/CMakeLists.txt", content=_OLED_CMAKE),
    ]
    model = ProjectModelExtractor().extract(project_files)
    i2c_capability = next(
        capability
        for capability in model.capabilities
        if capability.capability_id == "bus.i2c"
    )
    assert "main/pdf1.c" in i2c_capability.source_paths
    objective = ProjectObjective(
        objective_id="oled-helloworld",
        title="OLED 显示 helloworld",
        description="修复构建并验证 OLED 显示",
        acceptance_criteria=["OLED 显示 helloworld 需实际验证"],
    )
    change_set = ChangeSet(
        changes=[
            CapabilityChange(
                operation="modify",
                capability_id="build.main_dependencies",
                desired_state={"dependencies": ["ssd1306", "driver"]},
                rationale="修复构建依赖",
            ),
            CapabilityChange(
                operation="preserve",
                capability_id="bus.i2c",
            ),
        ]
    )
    plan = VerificationPlan(
        require_build=True,
        require_device=True,
        device_assertions=[_OLED_ASSERTION],
    )
    graph = build_task_graph(
        objective,
        change_set,
        allowed_paths_by_capability={
            "build.main_dependencies": ["main/CMakeLists.txt"]
        },
        verification_plan=plan,
    )
    for task in graph.tasks:
        if task.kind in {"inspect_project", "architecture_plan"}:
            graph = graph.update_task(task.task_id, status="passed", attempts=1)
    code_task = next(task for task in graph.tasks if task.kind == "code_change")
    verify_task = next(
        task for task in graph.tasks if task.kind == "verify_acceptance"
    )
    fixed_cmake = _OLED_CMAKE.replace(
        'INCLUDE_DIRS ".")',
        'INCLUDE_DIRS "."\n                    REQUIRES ssd1306 driver)',
    )
    bundle = ChangeBundle(
        bundle_id="bundle-cmake-fix",
        task_id=code_task.task_id,
        description="添加 driver 依赖修复构建",
        allowed_paths=["main/CMakeLists.txt"],
        preserves=["bus.i2c"],
        changes=[
            FileChange(
                operation="modify",
                path="main/CMakeLists.txt",
                content=fixed_cmake,
                expected_sha256=hashlib.sha256(
                    _OLED_CMAKE.encode("utf-8")
                ).hexdigest(),
            )
        ],
    )
    state: dict[str, object] = {
        "objective": objective,
        "change_set": change_set,
        "capabilities": list(model.capabilities),
        "project_files": project_files,
        "project_model": model,
        "hardware_report": model.hardware_report,
        "inspection_complete": True,
        "hardware_validated": True,
        "task_graph": graph,
        "verification_plan": plan,
        "change_bundles": {code_task.task_id: bundle},
        "evidence_ids": [
            f"task:{task.task_id}"
            for task in graph.tasks
            if task.kind in {"inspect_project", "architecture_plan"}
        ],
        "trace": [],
        "max_steps": 40,
    }
    return state, code_task.task_id, verify_task.task_id


class _ScopedRepairEngineer:
    def __init__(self, repaired_source: str) -> None:
        self.repaired_source = repaired_source
        self.calls = 0
        self.feedback: list[str] = []
        self.seen_allowed_paths: list[str] = []

    def create_bundle(
        self,
        objective,
        task,
        project_model,
        files,
        build_evidence=None,
        failure_feedback=None,
        reuse_candidates=None,
    ):
        del objective, project_model, files, build_evidence, reuse_candidates
        self.calls += 1
        self.feedback = list(failure_feedback or [])
        self.seen_allowed_paths = list(task.allowed_paths)
        return {
            "bundle_id": f"bundle-oled-repair-{self.calls}",
            "task_id": task.task_id,
            "description": "修复 OLED 初始化地址",
            "allowed_paths": list(task.allowed_paths),
            "preserves": ["bus.i2c"],
            "changes": [
                {
                    "operation": "modify",
                    "path": "main/pdf1.c",
                    "content": self.repaired_source,
                    "expected_sha256": hashlib.sha256(
                        _OLED_SOURCE.encode("utf-8")
                    ).hexdigest(),
                }
            ],
        }


class _AcceptingExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, project_path: Path, bundle) -> ChangeBundleValidation:
        del project_path
        self.calls += 1
        return ChangeBundleValidation(
            before_fingerprint="a" * 64,
            after_fingerprint="b" * 64,
            changed_files=[change.path for change in bundle.changes],
            diff_summary=[
                f"{change.operation}: {change.path}"
                for change in bundle.changes
            ],
        )


def test_device_log_failure_triggers_capability_scoped_repair_and_completes() -> None:
    """设备验证失败后，允许路径扩大到能力实现文件，修复并重新验证直至完成。"""

    state, code_task_id, _ = _oled_verification_state()
    failing = _monitor_evidence(
        "boot ok\nOLED init failed: ESP_FAIL",
        diagnostics=[
            DeviceLogDiagnostic(kind="error", summary="OLED init failed")
        ],
    )
    healthy = _monitor_evidence("boot ok\nDisplayed helloworld")
    monitor = _Monitor([failing, failing, healthy])
    builder = _Builder()
    engineer = _ScopedRepairEngineer(
        _OLED_SOURCE.replace("i2c_master_start(NULL);", "i2c_master_start(NULL);\n    (void)0;")
    )
    executor = _AcceptingExecutor()

    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            project_path=Path("F:/LUXAR"),
            build_executor=builder,
            monitor=monitor,
            serial_port="COM3",
            code_engineer=engineer,
            code_executor=executor,
        ),
    )

    assert result["status"] == "completed"
    assert result["acceptance_passed"] is True
    assert result["hardware_function_verified"] is True
    assert result["verification_repairs"] == 1
    assert engineer.calls == 1
    assert "OLED init failed" in "\n".join(engineer.feedback)
    assert "main/pdf1.c" in engineer.seen_allowed_paths
    assert "main/CMakeLists.txt" in engineer.seen_allowed_paths
    repaired_task = next(
        task
        for task in result["task_graph"].tasks
        if task.task_id == code_task_id
    )
    assert "main/pdf1.c" in repaired_task.allowed_paths


def test_repeated_device_failure_stops_after_repair_budget() -> None:
    """修复预算耗尽后停止，避免无上限的烧录-验证循环。"""

    state, code_task_id, _ = _oled_verification_state()
    failing = _monitor_evidence(
        "boot ok\nOLED init failed: ESP_FAIL",
        diagnostics=[
            DeviceLogDiagnostic(kind="error", summary="OLED init failed")
        ],
    )
    monitor = _Monitor([failing])
    builder = _Builder()
    engineer = _ScopedRepairEngineer(_OLED_SOURCE)
    executor = _AcceptingExecutor()

    result = build_agent_graph().invoke(
        state,
        context=AgentRuntimeContext(
            project_path=Path("F:/LUXAR"),
            build_executor=builder,
            monitor=monitor,
            serial_port="COM3",
            code_engineer=engineer,
            code_executor=executor,
        ),
    )

    assert result["status"] == "blocked"
    assert result["verification_repairs"] == 2
    assert engineer.calls == 2
    assert result["hardware_function_verified"] is False
    verify_task = next(
        task
        for task in result["task_graph"].tasks
        if task.kind == "verify_acceptance"
    )
    assert verify_task.status == "blocked"
