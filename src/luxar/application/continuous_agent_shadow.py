"""Read-only comparison contract for continuous-Agent shadow decisions."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from luxar.domain.continuous_agent.steps import (
    AgentStep,
    AskUser,
    AssistantReply,
    DomainWorkflowCall,
    FinishObjective,
    ToolCallBatch,
)


class ContinuousAgentShadowDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    legacy_intent: str = Field(min_length=1, max_length=120)
    v2_step_type: str = Field(min_length=1, max_length=120)
    v2_actions: list[str] = Field(default_factory=list, max_length=20)
    broadly_compatible: bool


class ContinuousAgentShadowSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    total: int = Field(ge=0)
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)
    broadly_compatible: int = Field(ge=0)
    broad_disagreements: int = Field(ge=0)
    disagreement_rate: float = Field(ge=0.0, le=1.0)


def compare_shadow_decision(
    legacy_intent: str,
    step: AgentStep,
) -> ContinuousAgentShadowDecision:
    actions: list[str] = []
    if isinstance(step, ToolCallBatch):
        actions = [call.tool_name for call in step.calls]
    elif isinstance(step, DomainWorkflowCall):
        actions = [step.workflow_name]
    elif isinstance(step, AskUser):
        actions = list(step.request.fields)
    elif isinstance(step, FinishObjective):
        actions = [step.outcome]

    if isinstance(step, AssistantReply):
        compatible = legacy_intent in {
            "casual_chat",
            "workflow_status",
            "knowledge_task",
            "project_inspection",
        }
    else:
        compatible = legacy_intent in {
            "firmware_task",
            "knowledge_task",
            "project_inspection",
        }
    return ContinuousAgentShadowDecision(
        legacy_intent=legacy_intent,
        v2_step_type=step.type,
        v2_actions=actions,
        broadly_compatible=compatible,
    )


def summarize_shadow_decisions(
    payloads: list[dict[str, object]],
) -> ContinuousAgentShadowSummary:
    completed = sum(item.get("status") == "completed" for item in payloads)
    failed = sum(item.get("status") == "failed" for item in payloads)
    compatible = sum(
        item.get("status") == "completed"
        and item.get("broadly_compatible") is True
        for item in payloads
    )
    disagreements = sum(
        item.get("status") == "completed"
        and item.get("broadly_compatible") is False
        for item in payloads
    )
    return ContinuousAgentShadowSummary(
        total=len(payloads),
        completed=completed,
        failed=failed,
        broadly_compatible=compatible,
        broad_disagreements=disagreements,
        disagreement_rate=(disagreements / completed if completed else 0.0),
    )


__all__ = [
    "ContinuousAgentShadowDecision",
    "ContinuousAgentShadowSummary",
    "compare_shadow_decision",
    "summarize_shadow_decisions",
]
