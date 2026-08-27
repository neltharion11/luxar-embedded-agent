from luxar.application.results import (
    live_message_for_state,
    state_to_result,
    user_message_for_state,
)
from luxar.application.state import WorkflowState
from luxar.domain.evidence import BuildEvidence
from luxar.domain.errors import WorkflowError
from luxar.domain.plans import ExecutionPlan, PlanStep
from luxar.domain.project_analysis import ProjectAnalysis
from luxar.domain.requirements import FirmwareRequirement, PeripheralRequirement


def test_clarification_message_names_missing_fields_without_internal_status() -> None:
    state = WorkflowState(
        status="needs_clarification",
        requirement=FirmwareRequirement(
            target="esp32s3",
            project_type="application",
            goal="",
            missing_fields=["goal"],
        ),
    )

    message = user_message_for_state(state)
    result = state_to_result(state)

    assert message == (
        "还需要你补充：项目需要实现的功能。"
        "如果只需要基础空项目，请直接回复“创建基础空项目”。"
    )
    assert "needs_clarification" not in message
    assert result["message"] == message


def test_failed_message_uses_sanitized_error_and_suggestion() -> None:
    state = WorkflowState(
        status="failed",
        error=WorkflowError(
            stage="build",
            category="environment",
            message="ESP-IDF 构建环境不可用",
            retryable=False,
            user_suggestion="请配置工具链后重试",
        ),
    )

    assert user_message_for_state(state) == (
        "任务执行失败：ESP-IDF 构建环境不可用。"
        "建议：请配置工具链后重试。"
    )


def test_clarification_names_only_the_requested_peripheral_parameter() -> None:
    state = WorkflowState(
        status="needs_clarification",
        requirement=FirmwareRequirement(
            target="esp32",
            goal="read_i2c_sensor",
            peripherals=[
                PeripheralRequirement(
                    kind="i2c",
                    missing_fields=["device_address"],
                )
            ],
        ),
    )

    assert user_message_for_state(state).startswith(
        "还需要你补充：I2C 外设的 device_address 参数。"
    )


def test_completed_message_reports_analysis_files_and_build_evidence() -> None:
    state = WorkflowState(
        status="completed",
        attempts=1,
        project_analysis=ProjectAnalysis(
            project_exists=True,
            has_source_code=True,
            fingerprint="current",
            summary="项目入口为空，尚未实现业务功能。",
            gaps=["app_main 仍为空"],
        ),
        plan=ExecutionPlan(
            steps=[
                PlanStep(
                    kind="implement_change",
                    description="实现需求",
                ),
                PlanStep(
                    kind="build_project",
                    description="验证构建",
                ),
            ]
        ),
        changed_files=["main/main.c", "main/main.c"],
        build_evidence=BuildEvidence(
            success=True,
            command=["idf.py", "build"],
            return_code=0,
        ),
    )

    message = user_message_for_state(state)

    assert message == (
        "处理完成。\n\n"
        "项目判断\n"
        "- 项目入口为空，尚未实现业务功能。\n"
        "- 尚未完成：app_main 仍为空。\n\n"
        "执行内容\n"
        "- 根据当前代码实现需求 → 构建并验证固件\n\n"
        "代码改动\n"
        "- main/main.c\n\n"
        "验证结果\n"
        "- 构建通过：共执行 1 次，最终返回码 0。"
    )


def test_empty_project_report_explains_why_no_source_was_changed() -> None:
    state = WorkflowState(
        status="completed",
        attempts=1,
        requirement=FirmwareRequirement(
            target="esp32",
            project_type="empty",
            goal="empty_project",
        ),
        project_analysis=ProjectAnalysis(
            project_exists=True,
            has_source_code=True,
            fingerprint="current",
            summary="项目是可构建的 ESP-IDF 空框架，app_main 为空。",
            gaps=["尚未实现业务功能。"],
        ),
        plan=ExecutionPlan(
            steps=[
                PlanStep(
                    kind="build_project",
                    description="验证空框架",
                )
            ]
        ),
        changed_files=[],
        build_evidence=BuildEvidence(
            success=True,
            command=["idf.py", "build"],
            return_code=0,
        ),
    )

    message = user_message_for_state(state)

    assert "尚未完成" not in message
    assert "继续加入业务代码反而会偏离本次需求" in message
    assert "构建通过：共执行 1 次，最终返回码 0" in message
    assert live_message_for_state(state) == (
        "处理完成。现有项目已经是符合需求的空框架，因此没有修改源码；"
        "构建验证通过，最终返回码为 0。"
    )


def test_live_completed_message_summarizes_changed_files_without_full_report() -> None:
    state = WorkflowState(
        status="completed",
        changed_files=["main/main.c", "main/main.c", "main/CMakeLists.txt"],
        build_evidence=BuildEvidence(
            success=True,
            command=["idf.py", "build"],
            return_code=0,
        ),
    )

    message = live_message_for_state(state)

    assert message == (
        "处理完成。已修改 main/main.c、main/CMakeLists.txt；"
        "构建验证通过，最终返回码为 0。"
    )
    assert "项目判断" not in message


def test_live_pdf_message_returns_extracted_content_instead_of_firmware_summary() -> None:
    state = WorkflowState(
        status="completed",
        knowledge_result={
            "read_pdf": True,
            "title": "OLED 规格书",
            "total_pages": 37,
            "batches": 4,
            "characters": 121249,
            "preview": "## 第 1 页\nOLED Product Specification",
        },
    )

    message = live_message_for_state(state)

    assert "PDF 已完整分批读取：共 37 页" in message
    assert "OLED Product Specification" in message
    assert "本次没有修改源码" not in message
