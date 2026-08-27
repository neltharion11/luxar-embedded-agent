from luxar.application.agent_results import (
    agent_exit_code_for_state,
    agent_state_to_result,
    agent_user_message_for_state,
)
from luxar.domain.agent.code_changes import AppliedFileChange, ChangeBundleValidation
from luxar.domain.agent.acceptance import AcceptanceCriterion
from luxar.domain.agent.code_changes import ChangeBundle, FileChange
from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.evidence import BuildEvidence
from luxar.domain.agent.build_recovery import BuildRecoveryDecision
from luxar.domain.agent.failures import AgentFailureRecord


def test_agent_result_contract_exposes_only_bounded_summary() -> None:
    state = {
        "status": "completed",
        "change_validations": {
            "task-1": ChangeBundleValidation(
                before_fingerprint="a" * 64,
                after_fingerprint="b" * 64,
                changed_files=["main/main.c"],
                diff_summary=["modify: main/main.c"],
            )
        },
        "evidence_ids": ["bundle:b-1", "build:e-1"],
        "build_evidence": BuildEvidence(
            success=True,
            command=["idf.py", "build"],
            return_code=0,
            stdout_summary="secret raw output",
        ),
        "build_verified": True,
        "acceptance_passed": True,
        "task_text": "must not escape",
        "applied_changes": [
            AppliedFileChange(
                task_id="task-1",
                path="main/main.c",
                operation="modify",
                summary="将 OLED 水平起始列右移两个像素",
            )
        ],
    }

    result = agent_state_to_result(state)

    assert result["status"] == "completed"
    assert result["changed_files"] == ["main/main.c"]
    assert result["changes"] == [
        {
            "task_id": "task-1",
            "path": "main/main.c",
            "operation": "modify",
            "summary": "将 OLED 水平起始列右移两个像素",
        }
    ]
    assert result["approval_status"] == "not_requested"
    assert "task_text" not in result
    assert "stdout_summary" in result["build_evidence"]
    assert agent_exit_code_for_state(state) == 0
    assert "构建验证通过" in agent_user_message_for_state(state)


def test_agent_result_contract_maps_user_and_failure_terminals() -> None:
    assert agent_exit_code_for_state({"status": "awaiting_user"}) == 3
    assert agent_exit_code_for_state({"status": "blocked"}) == 4
    assert "需要补充" in agent_user_message_for_state(
        {"status": "awaiting_user"}
    )
    assert "拒绝继续" in agent_user_message_for_state(
        {"status": "failed", "last_error": "拒绝继续"}
    )


def test_agent_result_exposes_bounded_failure_facts_to_parent_agent() -> None:
    state = {
        "status": "blocked",
        "current_task_id": "verify-1",
        "failure_history": [
            AgentFailureRecord(
                task_id="verify-1",
                category="semantic",
                signature="semantic:abc",
                message="main/main.c:12 unknown identifier gpio_state",
                errors=[{"loc": ["main/main.c", 12], "msg": "unknown identifier"}],
                attempt=1,
            )
        ],
        "task_feedback": {
            "verify-1": ["根据编译器诊断修改对应源码后重新构建"]
        },
        "build_recovery": BuildRecoveryDecision(
            category="source",
            action="repair_source",
            retryable_after_action=True,
            target_files=["main/main.c"],
            feedback=["main/main.c:12: unknown identifier gpio_state"],
        ),
    }

    context = agent_state_to_result(state)["failure_context"]

    assert context["current_task_id"] == "verify-1"
    assert context["recent_failures"][0]["message"].startswith("main/main.c:12")
    assert context["build_recovery"]["target_files"] == ["main/main.c"]
    assert "signature" not in context["recent_failures"][0]


def test_completed_message_explains_scope_changes_diagnosis_and_verification() -> None:
    state = {
        "status": "completed",
        "task_text": "屏幕没有任何反应",
        "objective": ProjectObjective(
            objective_id="obj-1",
            title="修复 SSD1306 空白显示",
            description="让屏幕显示测试内容",
        ),
        "change_bundles": {
            "task-1": ChangeBundle(
                bundle_id="bundle-1",
                task_id="task-1",
                description="修正 SSD1306 的 I2C 初始化和引脚配置",
                changes=[
                    FileChange(
                        operation="modify",
                        path="main/pin_config.h",
                        content="#define OLED_SCL 22\n",
                    )
                ],
                allowed_paths=["main/pin_config.h"],
            )
        },
        "change_validations": {
            "task-1": ChangeBundleValidation(
                before_fingerprint="a" * 64,
                after_fingerprint="b" * 64,
                changed_files=["main/pin_config.h"],
                diff_summary=["modify: main/pin_config.h"],
            )
        },
        "build_evidence": BuildEvidence(
            success=True,
            command=["idf.py", "build"],
            return_code=0,
        ),
        "acceptance_criteria": [
            AcceptanceCriterion(
                criterion_id="build",
                description="构建成功",
                verification_kind="build",
                status="passed",
            )
        ],
        "build_verified": True,
        "hardware_function_verified": False,
    }

    message = agent_user_message_for_state(state)

    assert "修复 SSD1306 空白显示" in message
    assert "计划：检查现有工程，完成所需修改，并验证目标和非回归条件" in message
    assert "完成情况：项目目标已完成（源码与构建部分）" in message
    assert "问题判断：" in message
    assert "优先排查供电、SDA/SCL 接线" in message
    assert "修改目的：修正 SSD1306 的 I2C 初始化和引脚配置" in message
    assert "modify: main/pin_config.h" in message
    assert "构建验证通过" in message
    assert "设备功能未验证" in message
    assert "验收条件 1/1 项通过" in message
    assert "设备功能已验证" not in message
