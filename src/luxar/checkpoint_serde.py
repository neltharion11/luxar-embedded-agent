"""Strict, explicit serialization policy for LangGraph checkpoints.

Checkpoint payloads may outlive an application process and are therefore an
untrusted deserialization boundary. LangGraph's permissive compatibility mode
imports unregistered Python types while emitting a warning. LUXAR instead
allows only application-owned models that are part of workflow state.
"""

from __future__ import annotations

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


# Keep this list explicit. Broad package-level allowlisting would weaken the
# auditability of this deserialization boundary.
LUXAR_ALLOWED_MSGPACK_MODULES: tuple[tuple[str, str], ...] = (
    # Shared and legacy workflow state.
    ("luxar.domain.devices", "ApprovalRequest"),
    ("luxar.domain.devices", "DeviceDiagnosis"),
    ("luxar.domain.devices", "DeviceLogDiagnostic"),
    ("luxar.domain.devices", "FlashEvidence"),
    ("luxar.domain.devices", "MonitorEvidence"),
    ("luxar.domain.errors", "WorkflowError"),
    ("luxar.domain.evidence", "BuildDiagnostic"),
    ("luxar.domain.evidence", "BuildEvidence"),
    ("luxar.domain.idf_examples", "EspIdfExampleReference"),
    ("luxar.domain.interactions", "WorkflowInteraction"),
    ("luxar.domain.knowledge_tasks", "KnowledgeTask"),
    ("luxar.domain.plans", "ExecutionPlan"),
    ("luxar.domain.plans", "PlanClarification"),
    ("luxar.domain.plans", "PlanStep"),
    ("luxar.domain.project_analysis", "ProjectAnalysis"),
    ("luxar.domain.projects", "ProjectEvidence"),
    ("luxar.domain.repairs", "FileReplacement"),
    ("luxar.domain.repairs", "ProjectFile"),
    ("luxar.domain.repairs", "RepairPlan"),
    ("luxar.domain.requirements", "FirmwareRequirement"),
    ("luxar.domain.requirements", "PeripheralRequirement"),
    # Supervisor state and task planning.
    ("luxar.application.agent_state", "SupervisorDecision"),
    ("luxar.domain.agent.acceptance", "AcceptanceCriterion"),
    ("luxar.domain.agent.acceptance", "AcceptanceVerification"),
    ("luxar.domain.agent.approvals", "AgentApprovalRequest"),
    ("luxar.domain.agent.build_recovery", "BuildRecoveryDecision"),
    ("luxar.domain.agent.capabilities", "ProjectCapability"),
    ("luxar.domain.agent.changes", "CapabilityChange"),
    ("luxar.domain.agent.changes", "ChangeSet"),
    ("luxar.domain.agent.changes", "ObjectiveInterpretation"),
    ("luxar.domain.agent.code_changes", "ChangeBundle"),
    ("luxar.domain.agent.code_changes", "ChangeBundleValidation"),
    ("luxar.domain.agent.code_changes", "FileChange"),
    ("luxar.domain.agent.code_changes", "AppliedFileChange"),
    ("luxar.domain.agent.failures", "AgentFailureRecord"),
    ("luxar.domain.agent.objectives", "ProjectObjective"),
    ("luxar.domain.agent.tasks", "AgentTask"),
    ("luxar.domain.agent.tasks", "AgentTaskGraph"),
    # Conversation-first continuous Agent state.
    ("luxar.domain.continuous_agent.events", "ConversationEvent"),
    ("luxar.domain.continuous_agent.failures", "ContinuousAgentFailure"),
    ("luxar.domain.continuous_agent.requests", "MissingInputRequest"),
    ("luxar.domain.continuous_agent.requests", "ToolApprovalRequest"),
    ("luxar.domain.continuous_agent.tools", "ToolCallState"),
    ("luxar.domain.continuous_agent.tools", "ToolResult"),
    ("luxar.domain.continuous_agent.steps", "AgentToolDescriptor"),
    ("luxar.domain.continuous_agent.steps", "ToolCall"),
    ("luxar.domain.continuous_agent.steps", "AssistantReply"),
    ("luxar.domain.continuous_agent.steps", "ToolCallBatch"),
    ("luxar.domain.continuous_agent.steps", "DomainWorkflowCall"),
    ("luxar.domain.continuous_agent.steps", "AskUser"),
    ("luxar.domain.continuous_agent.steps", "FinishObjective"),
    ("luxar.domain.continuous_agent.steps", "AgentStepEnvelope"),
    ("luxar.domain.continuous_agent.steps", "AgentStepContext"),
    # Hardware and structured project model nested in supervisor state.
    ("luxar.domain.agent.hardware", "BoardProfile"),
    ("luxar.domain.agent.hardware", "ChipProfile"),
    ("luxar.domain.agent.hardware", "DeviceSpec"),
    ("luxar.domain.agent.hardware", "HardwareAssignment"),
    ("luxar.domain.agent.hardware", "HardwareValidationIssue"),
    ("luxar.domain.agent.hardware", "HardwareValidationReport"),
    ("luxar.domain.agent.hardware", "ModuleProfile"),
    ("luxar.domain.agent.hardware", "ProtocolPackage"),
    ("luxar.domain.agent.project_model", "ComponentDependency"),
    ("luxar.domain.agent.project_model", "ComponentGraph"),
    ("luxar.domain.agent.project_model", "ComponentNode"),
    ("luxar.domain.agent.project_model", "DataFlow"),
    ("luxar.domain.agent.project_model", "DataFlowEdge"),
    ("luxar.domain.agent.project_model", "DataFlowNode"),
    ("luxar.domain.agent.project_model", "ProjectConfiguration"),
    ("luxar.domain.agent.project_model", "ProjectFact"),
    ("luxar.domain.agent.project_model", "ProjectModel"),
    ("luxar.domain.agent.project_model", "ResourceAllocation"),
    ("luxar.domain.agent.project_model", "ResourceConflict"),
    ("luxar.domain.agent.project_model", "ResourceGraph"),
    # Verification plans and evidence nested in supervisor state.
    ("luxar.domain.agent.runtime_verification", "EvidenceCheckResult"),
    ("luxar.domain.agent.runtime_verification", "ProtocolProbeEvidence"),
    ("luxar.domain.agent.runtime_verification", "ProtocolProbeSpec"),
    ("luxar.domain.agent.runtime_verification", "RuntimeScenarioEvidence"),
    ("luxar.domain.agent.runtime_verification", "RuntimeScenarioSpec"),
    ("luxar.domain.agent.verification", "AssertionResult"),
    ("luxar.domain.agent.verification", "ComponentTestEvidence"),
    ("luxar.domain.agent.verification", "ComponentTestSpec"),
    ("luxar.domain.agent.verification", "DeviceLogAssertion"),
    ("luxar.domain.agent.verification", "FirmwareMetricAssertion"),
    ("luxar.domain.agent.verification", "FirmwareResourceEvidence"),
    ("luxar.domain.agent.verification", "SourceAssertion"),
    ("luxar.domain.agent.verification", "VerificationPlan"),
    ("luxar.domain.agent.verification", "VerificationRun"),
)


def create_checkpoint_serializer() -> JsonPlusSerializer:
    """Return LUXAR's strict serializer for every checkpoint backend."""

    return JsonPlusSerializer(
        allowed_msgpack_modules=LUXAR_ALLOWED_MSGPACK_MODULES,
    )


__all__ = [
    "LUXAR_ALLOWED_MSGPACK_MODULES",
    "create_checkpoint_serializer",
]
