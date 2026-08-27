"""Conversation-first continuous Agent domain contracts."""

from luxar.domain.continuous_agent.sessions import (
    AgentSession,
    AgentSessionStatus,
    AgentTurn,
    AgentTurnStatus,
)
from luxar.domain.continuous_agent.events import (
    ConversationEvent,
    ConversationEventKind,
    merge_conversation_events,
)
from luxar.domain.continuous_agent.failures import (
    ContinuousAgentFailure,
    ContinuousAgentFailureCategory,
)
from luxar.domain.continuous_agent.requests import (
    MissingInputRequest,
    PendingRequest,
    ToolApprovalRequest,
)
from luxar.domain.continuous_agent.tools import (
    ToolCallState,
    ToolCallStatus,
    ToolExecutionLedgerStatus,
    ToolExecutionRecord,
    ToolResult,
)
from luxar.domain.continuous_agent.steps import (
    AgentStep,
    AgentStepContext,
    AgentStepEnvelope,
    AgentToolDescriptor,
    AskUser,
    AssistantReply,
    FinishObjective,
    ToolCall,
    ToolCallBatch,
)

__all__ = [
    "AgentSession",
    "AgentSessionStatus",
    "AgentTurn",
    "AgentTurnStatus",
    "ContinuousAgentFailure",
    "ContinuousAgentFailureCategory",
    "ConversationEvent",
    "ConversationEventKind",
    "MissingInputRequest",
    "PendingRequest",
    "ToolApprovalRequest",
    "ToolCallState",
    "ToolCallStatus",
    "ToolResult",
    "ToolExecutionLedgerStatus",
    "ToolExecutionRecord",
    "AgentStep",
    "AgentStepContext",
    "AgentStepEnvelope",
    "AgentToolDescriptor",
    "AskUser",
    "AssistantReply",
    "FinishObjective",
    "ToolCall",
    "ToolCallBatch",
    "merge_conversation_events",
]
