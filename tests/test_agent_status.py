from types import SimpleNamespace

from luxar.agent_status import AgentStatusTool
from luxar.model_config import EmbeddingConfig, ModelEndpoint, RuntimeModelConfig


def test_agent_status_is_structured_current_and_secret_free() -> None:
    config = RuntimeModelConfig(
        conversation=ModelEndpoint(
            provider="local",
            base_url="http://127.0.0.1:9000/v1",
            model="chat-test",
        ),
        vision_mode="separate",
        vision=ModelEndpoint(
            provider="local",
            base_url="http://127.0.0.1:9001/v1",
            model="vision-test",
        ),
        embedding=EmbeddingConfig(
            mode="api",
            provider="local",
            base_url="http://127.0.0.1:9002/v1",
            model="embed-test",
            dimensions=768,
        ),
    )
    status = AgentStatusTool(
        config_loader=lambda: config,
        toolchain_status_loader=lambda: SimpleNamespace(
            available=False,
            source="none",
            version=None,
            message="not configured",
            idf_path="must-not-leak",
        ),
        workflow_status_loader=lambda: {
            "status": "busy",
            "active_workflows": 1,
            "pending_approvals": 0,
            "capacity": 2,
        },
    ).inspect(
        user_input="检查你现在的状态并告诉我下一步",
        knowledge_status="项目外部知识库已启用，但当前项目没有任何知识文档（为空）。",
        previous_run={
            "status": "completed",
            "trace": ["analyze_project", "completed"],
            "build_evidence": {"success": True, "stdout": "secret output"},
            "approval_status": "not_requested",
        },
    )

    assert status["conversation_model"] == {
        "provider": "local",
        "model": "chat-test",
        "configured": True,
        "context_window_tokens": 32_768,
        "context_compaction_threshold": 0.95,
    }
    assert status["pdf_reader"]["vision_model"] == "vision-test"
    assert status["rag"]["project"]["available"] is True
    assert status["embedding"]["model"] == "embed-test"
    assert status["tools"]["count"] == 17
    assert status["workflow"]["node_count"] == 22
    assert status["workflow"]["runtime"]["active_workflows"] == 1
    assert status["workflow"]["latest_run"]["last_node"] == "completed"
    assert status["toolchain"]["available"] is False
    assert status["tools"]["items"][5] == {
        "name": "create_project",
        "available": False,
    }
    serialized = str(status)
    assert "secret output" not in serialized
    assert "127.0.0.1" not in serialized
    assert "must-not-leak" not in serialized
