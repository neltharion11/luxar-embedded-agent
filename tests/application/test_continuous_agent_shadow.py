from luxar.application.continuous_agent_shadow import (
    compare_shadow_decision,
    summarize_shadow_decisions,
)
from luxar.domain.continuous_agent.steps import (
    AssistantReply,
    ToolCall,
    ToolCallBatch,
)


def test_shadow_comparison_records_actions_without_executing_them() -> None:
    comparison = compare_shadow_decision(
        "firmware_task",
        ToolCallBatch(
            calls=[
                ToolCall(
                    call_id="build-1",
                    tool_name="espidf.build",
                    arguments={},
                )
            ]
        ),
    )

    assert comparison.v2_step_type == "tool_calls"
    assert comparison.v2_actions == ["espidf.build"]
    assert comparison.broadly_compatible is True


def test_shadow_comparison_flags_broad_route_disagreement() -> None:
    comparison = compare_shadow_decision(
        "firmware_task",
        AssistantReply(content="只回答，不执行。"),
    )

    assert comparison.broadly_compatible is False


def test_shadow_summary_reports_failures_and_disagreement_rate() -> None:
    summary = summarize_shadow_decisions(
        [
            {"status": "completed", "broadly_compatible": True},
            {"status": "completed", "broadly_compatible": False},
            {"status": "failed"},
        ]
    )

    assert summary.total == 3
    assert summary.completed == 2
    assert summary.failed == 1
    assert summary.broad_disagreements == 1
    assert summary.disagreement_rate == 0.5
