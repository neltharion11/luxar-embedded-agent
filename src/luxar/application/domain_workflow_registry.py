"""Registry for explicit top-Agent to domain-workflow delegation."""

from __future__ import annotations

from luxar.domain.continuous_agent.failures import ContinuousAgentFailure
from luxar.domain.continuous_agent.steps import DomainWorkflowCall
from luxar.ports.domain_workflow import (
    DomainWorkflowDescriptor,
    DomainWorkflowExecutionContext,
    DomainWorkflowOutcome,
    DomainWorkflowPort,
)


class DomainWorkflowRegistry:
    def __init__(self, workflows: list[DomainWorkflowPort] | None = None) -> None:
        self._workflows: dict[str, DomainWorkflowPort] = {}
        for workflow in workflows or []:
            self.register(workflow)

    def register(self, workflow: DomainWorkflowPort) -> None:
        name = workflow.descriptor.name
        if name in self._workflows:
            raise ValueError(f"Domain workflow already registered: {name}")
        self._workflows[name] = workflow

    def descriptors(self) -> list[DomainWorkflowDescriptor]:
        return [
            self._workflows[name].descriptor for name in sorted(self._workflows)
        ]

    def dispatch(
        self,
        call: DomainWorkflowCall,
        context: DomainWorkflowExecutionContext,
        *,
        approved: bool | None = None,
        feedback: str = "",
    ) -> DomainWorkflowOutcome:
        workflow = self._workflows.get(call.workflow_name)
        if workflow is None:
            failure = ContinuousAgentFailure(
                category="policy",
                code="unknown_domain_workflow",
                message="模型请求了未注册的领域工作流",
                retryable=False,
            )
            return DomainWorkflowOutcome(
                status="failed",
                summary=failure.message,
                result={"failure": failure.model_dump(mode="json")},
            )
        if approved is None:
            return workflow.start(call, context)
        return workflow.resume(
            call,
            context,
            approved=approved,
            feedback=feedback,
        )


__all__ = ["DomainWorkflowRegistry"]
