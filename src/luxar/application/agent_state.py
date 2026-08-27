"""Supervisor 运行时的可 checkpoint 状态。"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from luxar.domain.agent.acceptance import AcceptanceCriterion
from luxar.domain.agent.approvals import AgentApprovalRequest
from luxar.domain.agent.capabilities import ProjectCapability
from luxar.domain.agent.build_recovery import BuildRecoveryDecision
from luxar.domain.agent.changes import ChangeSet, ObjectiveInterpretation
from luxar.domain.agent.code_changes import (
    AppliedFileChange,
    ChangeBundle,
    ChangeBundleValidation,
)
from luxar.domain.agent.failures import AgentFailureRecord
from luxar.domain.agent.hardware import HardwareValidationReport
from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.agent.project_model import ProjectModel
from luxar.domain.agent.tasks import AgentTaskGraph
from luxar.domain.agent.verification import (
    FirmwareResourceEvidence,
    VerificationPlan,
    VerificationRun,
)
from luxar.domain.devices import FlashEvidence, MonitorEvidence
from luxar.domain.evidence import BuildEvidence
from luxar.domain.repairs import ProjectFile
from luxar.ports.code_executor import CodeExecutorPort
from luxar.ports.code_engineer import CodeEngineerPort
from luxar.ports.sdk_probe import SdkProbePort
from luxar.ports.agent_planner import AgentPlannerPort
from luxar.ports.espidf import EspIdfPort
from luxar.ports.espidf_device import EspIdfFlashPort, EspIdfMonitorPort
from luxar.ports.workspace import WorkspacePort
from luxar.ports.verification import (
    ComponentTestPort,
    FirmwareInspectorPort,
    ProtocolProbePort,
    RuntimeScenarioPort,
)


AgentStatus = Literal[
    "running",
    "awaiting_user",
    "blocked",
    "completed",
    "failed",
]


class SupervisorDecision(BaseModel):
    """Supervisor 每轮唯一的结构化动作决策。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal[
        "inspect_project",
        "update_project_model",
        "plan_tasks",
        "revise_plan",
        "validate_hardware",
        "execute_task",
        "verify_acceptance",
        "answer_user",
        "ask_user",
        "request_approval",
        "degrade_capability",
        "complete_objective",
        "fail_objective",
    ]
    target_id: str | None = None
    rationale: str = Field(min_length=1, max_length=2000)
    required_inputs: list[str] = Field(default_factory=list, max_length=40)


@dataclass(frozen=True)
class AgentRuntimeContext:
    """不进入 checkpoint 的外部执行依赖。"""

    code_executor: CodeExecutorPort | None = None
    code_engineer: CodeEngineerPort | None = None
    sdk_probe: SdkProbePort | None = None
    objective_planner: AgentPlannerPort | None = None
    project_path: Path | None = None
    schema_repair: Callable[
        [str, object, list[dict[str, Any]]], object
    ] | None = None
    build_executor: EspIdfPort | None = None
    flasher: EspIdfFlashPort | None = None
    workspace: WorkspacePort | None = None
    monitor: EspIdfMonitorPort | None = None
    serial_port: str | None = None
    monitor_timeout_seconds: int = 10
    component_tester: ComponentTestPort | None = None
    firmware_inspector: FirmwareInspectorPort | None = None
    protocol_probe: ProtocolProbePort | None = None
    runtime_scenario_runner: RuntimeScenarioPort | None = None


class AgentState(TypedDict, total=False):
    task_text: str
    workflow_action: Literal["flash", "build", "monitor"]
    source_message_id: str
    project_name: str
    target_chip: str | None
    project_files: list[ProjectFile]
    objective: ProjectObjective
    change_set: ChangeSet
    interpretation: ObjectiveInterpretation
    capabilities: list[ProjectCapability]
    project_model: ProjectModel
    hardware_report: HardwareValidationReport
    hardware_validated: bool
    hardware_blocked: bool
    allowed_paths_by_capability: dict[str, list[str]]
    inspection_complete: bool
    planning_blocked: bool
    task_graph: AgentTaskGraph
    acceptance_criteria: list[AcceptanceCriterion]
    change_bundles: dict[str, ChangeBundle]
    change_validations: dict[str, ChangeBundleValidation]
    applied_changes: list[AppliedFileChange]
    failure_history: list[AgentFailureRecord]
    task_feedback: dict[str, list[str]]
    schema_repairs: list[dict[str, object]]
    schema_errors: list[dict[str, object]]
    verification_plan: VerificationPlan
    verification_runs: dict[str, VerificationRun]
    build_evidence: BuildEvidence
    flash_evidence: FlashEvidence
    build_recovery: BuildRecoveryDecision
    monitor_evidence: MonitorEvidence
    firmware_resource_evidence: FirmwareResourceEvidence
    build_verified: bool
    hardware_function_verified: bool
    evidence_ids: list[str]
    approval_request: AgentApprovalRequest
    approval_status: Literal["not_requested", "pending", "approved", "rejected"]
    decision: SupervisorDecision
    current_task_id: str | None
    acceptance_passed: bool
    status: AgentStatus
    trace: list[str]
    step_count: int
    max_steps: int
    last_error: str
