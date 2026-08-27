"""第一版项目级 Supervisor Graph。

该图与 legacy ``application.graph`` 并存。它先验证新领域闭环：
inspect → plan → execute → verify → supervisor；实际文件写入、构建、烧录
将在后续阶段通过受限工具注册表接入。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import ValidationError

from luxar.application.agent_state import (
    AgentRuntimeContext,
    AgentState,
    SupervisorDecision,
)
from luxar.domain.agent.acceptance import AcceptanceVerifier
from luxar.domain.agent.approvals import AgentApprovalRequest
from luxar.domain.agent.capabilities import ProjectCapability, ProjectCapabilityExtractor
from luxar.domain.agent.build_recovery import BuildFailureAdvisor
from luxar.domain.agent.sdk_probe import changed_api_names, missing_include_names
from luxar.ports.sdk_probe import SdkProbePort
from luxar.domain.agent.code_changes import (
    AppliedFileChange,
    ChangeBundle,
    ChangeBundleError,
    ChangeBundleValidation,
)
from luxar.domain.agent.changes import ChangeSet, ObjectiveInterpretation, ObjectiveInterpreter
from luxar.domain.agent.failures import (
    AgentFailureRecord,
    decide_failure_status,
    failure_signature,
)
from luxar.domain.agent.hardware import HardwareValidationReport
from luxar.domain.agent.objectives import ProjectObjective
from luxar.domain.agent.project_inspector import ProjectModelExtractor
from luxar.domain.agent.project_model import ProjectModel
from luxar.domain.agent.schema_repair import (
    SchemaRepairExhausted,
    validate_with_one_repair,
)
from luxar.domain.agent.runtime_verification import (
    ProtocolProbeEvidence,
    ProtocolProbeVerifier,
    RuntimeScenarioEvidence,
    RuntimeScenarioVerifier,
)
from luxar.domain.agent.tasks import AgentTask, AgentTaskGraph, build_task_graph
from luxar.domain.agent.verification import (
    ComponentTestEvidence,
    DeviceLogAssertion,
    DeviceLogVerifier,
    FirmwareResourceEvidence,
    FirmwareResourceVerifier,
    SourceAssertionVerifier,
    VerificationPlan,
    VerificationRun,
)
from luxar.domain.devices import FlashEvidence, MonitorEvidence
from luxar.domain.interactions import WorkflowDecision
from luxar.domain.evidence import BuildEvidence
from luxar.domain.repairs import ProjectFile
from luxar.ports.errors import CapabilityError
from luxar.ports.espidf_errors import EspIdfError
from luxar.ports.workspace_errors import WorkspaceError
from luxar.ports.verification import VerificationToolError


_extractor = ProjectCapabilityExtractor()
_project_model_extractor = ProjectModelExtractor()
_interpreter = ObjectiveInterpreter()
_verifier = AcceptanceVerifier()
_source_assertion_verifier = SourceAssertionVerifier()
_device_log_verifier = DeviceLogVerifier()
_firmware_resource_verifier = FirmwareResourceVerifier()
_protocol_probe_verifier = ProtocolProbeVerifier()
_runtime_scenario_verifier = RuntimeScenarioVerifier()
_build_failure_advisor = BuildFailureAdvisor()


def _sdk_include_hints(
    sdk_probe: SdkProbePort | None,
    build_evidence: BuildEvidence | None,
) -> list[str]:
    """把"缺失头文件"的构建失败接地到已安装 ESP-IDF，给出确定性修复提示。"""

    if sdk_probe is None or build_evidence is None:
        return []
    hints: list[str] = []
    for header in missing_include_names(build_evidence):
        resolution = sdk_probe.resolve_include(header, build_evidence.idf_path)
        if resolution.exists:
            hints.append(
                f"头文件 {header} 在本机已安装的 ESP-IDF 中确实存在，"
                "说明是 include 路径或组件依赖缺失，请修正组件依赖/CMake，"
                "而不是替换头文件名。"
            )
        elif resolution.candidates:
            joined = "、".join(resolution.candidates[:6])
            hints.append(
                f"头文件 {header} 在本机 ESP-IDF 中不存在。"
                f"已安装 SDK 中最接近的头文件：{joined}。请改用其中正确的头文件。"
            )
        else:
            hints.append(
                f"头文件 {header} 在本机 ESP-IDF 中不存在且未找到相近头文件，"
                "请改用 SDK 实际提供的 API。"
            )
    return hints


def _sdk_api_hints(
    sdk_probe: SdkProbePort | None,
    build_evidence: BuildEvidence | None,
) -> list[str]:
    """把"API 改名/移除/弃用"的构建诊断接地到已安装 ESP-IDF 的迁移指南。"""

    if sdk_probe is None or build_evidence is None:
        return []
    hints: list[str] = []
    for name in changed_api_names(build_evidence):
        snippets = sdk_probe.search_migration(name, build_evidence.idf_path)
        if not snippets:
            continue
        first = snippets[0]
        hints.append(
            f"符号 {name} 在本机 ESP-IDF 迁移指南中有记录（{first.guide}）："
            f"{first.snippet}"
        )
        for extra in snippets[1:3]:
            hints.append(f"{name}（{extra.guide}）：{extra.snippet}")
    return hints


def _trace(state: AgentState, node: str) -> list[str]:
    return [*state.get("trace", []), node]


def load_project_session(state: AgentState) -> dict[str, object]:
    return {
        "status": "running",
        "step_count": state.get("step_count", 0),
        "max_steps": state.get("max_steps", 40),
        "evidence_ids": list(state.get("evidence_ids", [])),
        "failure_history": list(state.get("failure_history", [])),
        "task_feedback": {
            task_id: list(feedback)
            for task_id, feedback in state.get("task_feedback", {}).items()
        },
        "schema_repairs": list(state.get("schema_repairs", [])),
        "schema_errors": list(state.get("schema_errors", [])),
        "verification_runs": dict(state.get("verification_runs", {})),
        "build_verified": state.get("build_verified", False),
        "hardware_function_verified": state.get(
            "hardware_function_verified",
            False,
        ),
        "trace": _trace(state, "load_project_session"),
    }


def project_inspector(
    state: AgentState,
    runtime: Runtime[AgentRuntimeContext],
) -> dict[str, object]:
    context = runtime.context or AgentRuntimeContext()
    raw_files = state.get("project_files", [])
    files = [
        file if isinstance(file, ProjectFile) else ProjectFile.model_validate(file)
        for file in raw_files
    ]
    capabilities = _extractor.extract(files)
    project_model = _project_model_extractor.extract(
        files,
        project_name=state.get("project_name", "project"),
        target_chip=state.get("target_chip"),
    )
    # 允许调用方带入用户或资料事实；同 ID 的源码事实优先保留，避免重复。
    existing = [
        item
        if isinstance(item, ProjectCapability)
        else ProjectCapability.model_validate(item)
        for item in state.get("capabilities", [])
    ]
    by_id = {item.capability_id: item for item in existing}
    for capability in capabilities:
        by_id[capability.capability_id] = capability
    merged_capabilities = [by_id[key] for key in sorted(by_id)]

    current_objective = state.get("objective")
    if isinstance(current_objective, dict):
        current_objective = ProjectObjective.model_validate(current_objective)
    task_text = state.get("task_text", "").strip()
    if state.get("workflow_action") == "flash":
        current_objective = ProjectObjective(
            objective_id=f"workflow:flash:{state.get('source_message_id', 'user')}",
            title="烧录当前工程固件",
            description="构建并烧录当前工程已有固件，不修改源码。",
            acceptance_criteria=["当前工程固件已成功烧录到目标开发板"],
        )
        interpretation = ObjectiveInterpretation(
            intent="change_objective",
            objective=current_objective,
            change_set=ChangeSet(
                assumptions=["用户明确要求烧录，保留当前源码，不生成代码变更"],
            ),
            objective_changed=False,
        )
    elif not task_text and current_objective is None:
        # 空输入不能伪造目标；交给交互层补充事实。
        interpretation = ObjectiveInterpretation(
            intent="ask_question",
            questions=["请先提供工程目标或要修改的能力"],
            objective_changed=False,
        )
    else:
        interpretation = _interpreter.interpret(
            task_text,
            existing_capabilities=merged_capabilities,
            current_objective=current_objective,
            source_message_id=state.get("source_message_id", "user"),
        )
        actionable = bool(
            interpretation.change_set is not None
            and any(
                change.operation in {"add", "modify", "remove", "replace"}
                for change in interpretation.change_set.changes
            )
        )
        actionable_ids = (
            {
                change.capability_id
                for change in interpretation.change_set.changes
                if change.operation in {"add", "modify", "remove", "replace"}
            }
            if interpretation.change_set is not None
            else set()
        )
        missing_file_scopes = any(
            not interpretation.allowed_paths_by_capability.get(capability_id)
            for capability_id in actionable_ids
        )
        if (
            (not actionable or missing_file_scopes)
            and interpretation.intent == "change_objective"
            and context.objective_planner is not None
        ):
            try:
                interpretation = context.objective_planner.interpret_goal(
                    task_text,
                    project_model,
                    current_objective,
                )
            except CapabilityError:
                interpretation = ObjectiveInterpretation(
                    intent="ask_question",
                    objective=current_objective,
                    questions=["项目规划模型未能生成有效变更，请重试或补充目标"],
                    objective_changed=False,
                )
    update: dict[str, object] = {
        "capabilities": merged_capabilities,
        "project_model": project_model,
        "hardware_report": project_model.hardware_report,
        "hardware_validated": False,
        "hardware_blocked": False,
        "inspection_complete": True,
        "interpretation": interpretation,
        "trace": _trace(state, "project_inspector"),
    }
    if interpretation.objective is not None:
        update["objective"] = interpretation.objective
    if interpretation.change_set is not None:
        update["change_set"] = interpretation.change_set
    if interpretation.allowed_paths_by_capability:
        update["allowed_paths_by_capability"] = {
            capability_id: list(paths)
            for capability_id, paths in (
                interpretation.allowed_paths_by_capability.items()
            )
        }
    return update


def hardware_validator(state: AgentState) -> dict[str, object]:
    report = state.get("hardware_report")
    if report is None:
        project_model = state.get("project_model")
        if isinstance(project_model, ProjectModel):
            report = project_model.hardware_report
        elif isinstance(project_model, dict):
            report = ProjectModel.model_validate(project_model).hardware_report
    if report is None:
        return {
            "hardware_validated": True,
            "hardware_blocked": False,
            "trace": _trace(state, "hardware_validator"),
        }
    if isinstance(report, dict):
        report = HardwareValidationReport.model_validate(report)
    if report.has_blocking_issue:
        first_issue = report.blocking_issues[0]
        return {
            "hardware_validated": False,
            "hardware_blocked": True,
            "status": "blocked",
            "last_error": first_issue.message,
            "trace": _trace(state, "hardware_validator"),
        }
    return {
        "hardware_validated": True,
        "hardware_blocked": False,
        "trace": _trace(state, "hardware_validator"),
    }


def architecture_planner(state: AgentState) -> dict[str, object]:
    objective = state.get("objective")
    if isinstance(objective, dict):
        objective = ProjectObjective.model_validate(objective)
    change_set = state.get("change_set")
    if isinstance(change_set, dict):
        change_set = ChangeSet.model_validate(change_set)
    if objective is None or change_set is None:
        return {
            "planning_blocked": True,
            "status": "awaiting_user",
            "last_error": "缺少项目目标或变更集，无法生成任务图",
            "trace": _trace(state, "architecture_planner"),
        }
    actionable_changes = [
        change
        for change in change_set.changes
        if change.operation in {"add", "modify", "remove", "replace"}
    ]
    if not actionable_changes and state.get("workflow_action") != "flash":
        return {
            "planning_blocked": True,
            "status": "awaiting_user",
            "last_error": (
                "目标尚未形成可执行变更；需要模型规划结果或用户补充具体能力"
            ),
            "trace": _trace(state, "architecture_planner"),
        }
    task_graph = build_task_graph(
        objective,
        change_set,
        current_capability_ids=(
            capability.capability_id for capability in state.get("capabilities", [])
        ),
        allowed_paths_by_capability=state.get("allowed_paths_by_capability", {}),
        verification_plan=state.get("verification_plan"),
    )
    return {
        "planning_blocked": False,
        "task_graph": task_graph,
        "acceptance_criteria": task_graph.acceptance_criteria,
        "trace": _trace(state, "architecture_planner"),
    }


def _failure_update(
    state: AgentState,
    graph: AgentTaskGraph,
    task_id: str,
    *,
    category: Literal["schema", "semantic", "execution"],
    message: str,
    signature: str,
    errors: Sequence[dict[str, object]] = (),
) -> dict[str, object]:
    task = next(task for task in graph.tasks if task.task_id == task_id)
    history = [
        item
        if isinstance(item, AgentFailureRecord)
        else AgentFailureRecord.model_validate(item)
        for item in state.get("failure_history", [])
    ]
    status, attempt, repeated = decide_failure_status(task, history, signature)
    updated_graph = graph.update_task(
        task_id,
        status=status,
        attempts=attempt,
    )
    record = AgentFailureRecord(
        task_id=task_id,
        category=category,
        signature=signature,
        message=message,
        errors=list(errors),
        attempt=attempt,
        repeated=repeated,
    )
    feedback = {
        task_key: list(values)
        for task_key, values in state.get("task_feedback", {}).items()
    }
    feedback.setdefault(task_id, []).append(
        (
            f"{message}；错误签名 {signature} 已重复，任务阻塞"
            if repeated
            else message
        )
    )
    update: dict[str, object] = {
        "task_graph": updated_graph,
        "current_task_id": task_id,
        "last_error": (
            f"{message}；已达到重试边界，当前任务阻塞"
            if status == "blocked"
            else message
        ),
        "failure_history": [*history, record],
        "task_feedback": feedback,
        "trace": _trace(state, "task_executor"),
    }
    if category == "schema":
        update["schema_errors"] = list(errors)
    return update


def _code_task_for_build_repair(
    graph: AgentTaskGraph,
    target_files: Sequence[str],
) -> AgentTask | None:
    """选择能在既有白名单内修复构建失败的最近代码任务。"""

    candidates = [
        task
        for task in graph.tasks
        if task.kind == "code_change" and task.status == "passed"
    ]
    normalized_targets = {
        path.replace("\\", "/").casefold() for path in target_files
    }
    scoped = [
        task
        for task in candidates
        if normalized_targets
        and normalized_targets.intersection(
            path.replace("\\", "/").casefold()
            for path in task.allowed_paths
        )
    ]
    if scoped:
        return scoped[-1]
    return candidates[-1] if candidates else None


def _execute_verification_task(
    state: AgentState,
    graph: AgentTaskGraph,
    task_id: str,
    context: AgentRuntimeContext,
) -> dict[str, object]:
    task = next(task for task in graph.tasks if task.task_id == task_id)
    raw_plan = state.get("verification_plan")
    plan = (
        raw_plan
        if isinstance(raw_plan, VerificationPlan)
        else VerificationPlan.model_validate(raw_plan)
    )
    raw_files = state.get("project_files", [])
    files = [
        item if isinstance(item, ProjectFile) else ProjectFile.model_validate(item)
        for item in raw_files
    ]
    if context.workspace is not None and context.project_path is not None:
        try:
            files = context.workspace.read_project_files(context.project_path)
        except WorkspaceError as error:
            return _failure_update(
                state,
                graph,
                task_id,
                category="execution",
                message=f"验证阶段读取项目失败: {error.category}",
                signature=failure_signature(
                    task_id,
                    "execution",
                    ["verification_workspace", error.category],
                ),
            )

    source_results = _source_assertion_verifier.verify(
        plan.source_assertions,
        files,
    )
    failed_source = [result.assertion_id for result in source_results if not result.passed]
    if failed_source:
        update = _failure_update(
            state,
            graph,
            task_id,
            category="semantic",
            message="源码断言未通过",
            signature=failure_signature(
                task_id,
                "semantic",
                ["source_assertion", *failed_source],
            ),
        )
        update["verification_runs"] = {
            **state.get("verification_runs", {}),
            task_id: VerificationRun(
                task_id=task_id,
                source_results=source_results,
                success=False,
            ),
        }
        return update

    evidence_ids = [
        *state.get("evidence_ids", []),
        *(
            result.evidence_id
            for result in source_results
            if result.evidence_id is not None
        ),
    ]
    component_test_evidence: list[ComponentTestEvidence] = []
    if plan.component_tests:
        if context.component_tester is None or context.project_path is None:
            update = _failure_update(
                state,
                graph,
                task_id,
                category="execution",
                message="验收要求组件测试，但未配置受控测试执行器或项目路径",
                signature=failure_signature(
                    task_id,
                    "execution",
                    ["component_test_unavailable"],
                ),
            )
            update["evidence_ids"] = evidence_ids
            return update
        for spec in plan.component_tests:
            try:
                raw_test = context.component_tester.run_component_test(
                    context.project_path,
                    spec,
                )
                test_evidence = (
                    raw_test
                    if isinstance(raw_test, ComponentTestEvidence)
                    else ComponentTestEvidence.model_validate(raw_test)
                )
            except VerificationToolError as error:
                update = _failure_update(
                    state,
                    graph,
                    task_id,
                    category="execution",
                    message=f"组件测试执行失败: {error.category}",
                    signature=failure_signature(
                        task_id,
                        "execution",
                        ["component_test", spec.test_id, error.category],
                    ),
                )
                update["evidence_ids"] = evidence_ids
                return update
            except ValidationError as error:
                update = _failure_update(
                    state,
                    graph,
                    task_id,
                    category="schema",
                    message="组件测试执行器返回了无效证据",
                    signature=failure_signature(
                        task_id,
                        "schema",
                        ["component_test_evidence", spec.test_id],
                    ),
                    errors=error.errors(include_url=False),
                )
                update["evidence_ids"] = evidence_ids
                return update
            component_test_evidence.append(test_evidence)
            if (
                test_evidence.test_id != spec.test_id
                or test_evidence.runner != spec.runner
            ):
                update = _failure_update(
                    state,
                    graph,
                    task_id,
                    category="semantic",
                    message="组件测试证据与计划规格不匹配",
                    signature=failure_signature(
                        task_id,
                        "semantic",
                        ["component_test_mismatch", spec.test_id],
                    ),
                )
                update["evidence_ids"] = evidence_ids
                return update
            if not test_evidence.success:
                update = _failure_update(
                    state,
                    graph,
                    task_id,
                    category="semantic",
                    message=f"组件测试未通过: {spec.test_id}",
                    signature=failure_signature(
                        task_id,
                        "semantic",
                        ["component_test_failed", spec.test_id],
                    ),
                )
                update["evidence_ids"] = evidence_ids
                update["verification_runs"] = {
                    **state.get("verification_runs", {}),
                    task_id: VerificationRun(
                        task_id=task_id,
                        source_results=source_results,
                        component_test_evidence=component_test_evidence,
                        success=False,
                    ),
                }
                return update
            evidence_ids.append(f"component-test:{spec.test_id}")

    # A verification retry may re-run read-only checks, but must not repeat a
    # build or device flash that already produced successful evidence for this
    # same task.  Failed runs persist these facts below before returning.
    previous_run_raw = state.get("verification_runs", {}).get(task_id)
    previous_run = (
        previous_run_raw
        if isinstance(previous_run_raw, VerificationRun)
        else VerificationRun.model_validate(previous_run_raw)
        if previous_run_raw is not None
        else None
    )
    state_build = state.get("build_evidence")
    state_flash = state.get("flash_evidence")
    if state_build is not None and not isinstance(state_build, BuildEvidence):
        state_build = BuildEvidence.model_validate(state_build)
    if state_flash is not None and not isinstance(state_flash, FlashEvidence):
        state_flash = FlashEvidence.model_validate(state_flash)
    build_evidence: BuildEvidence | None = (
        previous_run.build_evidence
        if task.attempts > 0
        and previous_run is not None
        and previous_run.build_evidence is not None
        and previous_run.build_evidence.success
        else state_build
        if task.attempts > 0
        and isinstance(state_build, BuildEvidence)
        and state_build.success
        else None
    )
    flash_evidence: FlashEvidence | None = (
        previous_run.flash_evidence
        if task.attempts > 0
        and previous_run is not None
        and previous_run.flash_evidence is not None
        and previous_run.flash_evidence.success
        else state_flash
        if task.attempts > 0
        and isinstance(state_flash, FlashEvidence)
        and state_flash.success
        else None
    )
    firmware_resource_evidence: FirmwareResourceEvidence | None = None
    firmware_results = []
    monitor_evidence: MonitorEvidence | None = None
    build_verified = False
    hardware_verified = False

    if plan.require_build:
        if context.build_executor is None or context.project_path is None:
            update = _failure_update(
                state,
                graph,
                task_id,
                category="execution",
                message="验收要求构建，但未配置 ESP-IDF 构建执行器或项目路径",
                signature=failure_signature(
                    task_id,
                    "execution",
                    ["build_unavailable"],
                ),
            )
            update["evidence_ids"] = evidence_ids
            return update
        try:
            if build_evidence is None:
                raw_build = context.build_executor.build(context.project_path)
                build_evidence = (
                    raw_build
                    if isinstance(raw_build, BuildEvidence)
                    else BuildEvidence.model_validate(raw_build)
                )
        except EspIdfError as error:
            update = _failure_update(
                state,
                graph,
                task_id,
                category="execution",
                message=f"ESP-IDF 构建执行失败: {error.category}",
                signature=failure_signature(
                    task_id,
                    "execution",
                    ["build", error.category],
                ),
            )
            update["evidence_ids"] = evidence_ids
            return update
        except ValidationError as error:
            update = _failure_update(
                state,
                graph,
                task_id,
                category="schema",
                message="ESP-IDF 构建执行器返回了无效证据",
                signature=failure_signature(
                    task_id,
                    "schema",
                    ["build_evidence"],
                ),
                errors=error.errors(include_url=False),
            )
            update["evidence_ids"] = evidence_ids
            return update
        if not build_evidence.success:
            recovery = _build_failure_advisor.analyze(build_evidence)
            update = _failure_update(
                state,
                graph,
                task_id,
                category=(
                    "semantic"
                    if recovery.category in {"source", "linker"}
                    else "execution"
                ),
                message=(
                    f"ESP-IDF 构建未通过: {recovery.category}；"
                    f"修复动作: {recovery.action}"
                ),
                signature=failure_signature(
                    task_id,
                    (
                        "semantic"
                        if recovery.category in {"source", "linker"}
                        else "execution"
                    ),
                    [
                        "build",
                        recovery.category,
                        *recovery.target_files,
                        *(
                            (
                                f"{diagnostic.file or 'build'}:"
                                f"{diagnostic.line or 0}:"
                                f"{diagnostic.code or '-'}:"
                                f"{diagnostic.message}"
                            )
                            for diagnostic in build_evidence.diagnostics[:8]
                        ),
                    ],
                ),
            )
            update["build_evidence"] = build_evidence
            update["build_recovery"] = recovery
            update["build_verified"] = False
            update["evidence_ids"] = evidence_ids
            repair_task = (
                _code_task_for_build_repair(graph, recovery.target_files)
                if recovery.action in {"repair_source", "repair_linker"}
                and context.code_engineer is not None
                and context.code_executor is not None
                else None
            )
            if repair_task is not None:
                failed_graph = update["task_graph"]
                assert isinstance(failed_graph, AgentTaskGraph)
                update["task_graph"] = failed_graph.update_task(
                    repair_task.task_id,
                    status="pending",
                )
                feedback = {
                    key: list(items)
                    for key, items in update["task_feedback"].items()
                }
                feedback.setdefault(repair_task.task_id, []).extend(
                    [
                        "上一轮验证发现构建失败。分析诊断后生成不同的最小修复，"
                        "不要原样重复上一变更。",
                        *recovery.feedback,
                        *_sdk_include_hints(context.sdk_probe, build_evidence),
                        *_sdk_api_hints(context.sdk_probe, build_evidence),
                    ]
                )
                update["task_feedback"] = feedback
                bundles = dict(state.get("change_bundles", {}))
                previous_bundle = bundles.pop(repair_task.task_id, None)
                update["change_bundles"] = bundles
                validations = dict(state.get("change_validations", {}))
                validations.pop(repair_task.task_id, None)
                update["change_validations"] = validations
                stale_ids = {f"task:{repair_task.task_id}"}
                if isinstance(previous_bundle, ChangeBundle):
                    stale_ids.add(f"bundle:{previous_bundle.bundle_id}")
                elif isinstance(previous_bundle, dict):
                    bundle_id = previous_bundle.get("bundle_id")
                    if bundle_id:
                        stale_ids.add(f"bundle:{bundle_id}")
                update["evidence_ids"] = [
                    item for item in evidence_ids if item not in stale_ids
                ]
            return update
        build_verified = True
        if f"build:{task_id}" not in evidence_ids:
            evidence_ids.append(f"build:{task_id}")

    if plan.firmware_assertions:
        if context.firmware_inspector is None or context.project_path is None:
            update = _failure_update(
                state,
                graph,
                task_id,
                category="execution",
                message="验收要求固件资源证据，但未配置固件检查器或项目路径",
                signature=failure_signature(
                    task_id,
                    "execution",
                    ["firmware_inspector_unavailable"],
                ),
            )
            update["build_verified"] = build_verified
            update["evidence_ids"] = evidence_ids
            if build_evidence is not None:
                update["build_evidence"] = build_evidence
            return update
        try:
            raw_firmware = context.firmware_inspector.inspect_firmware(
                context.project_path
            )
            firmware_resource_evidence = (
                raw_firmware
                if isinstance(raw_firmware, FirmwareResourceEvidence)
                else FirmwareResourceEvidence.model_validate(raw_firmware)
            )
        except VerificationToolError as error:
            update = _failure_update(
                state,
                graph,
                task_id,
                category="execution",
                message=f"固件资源检查失败: {error.category}",
                signature=failure_signature(
                    task_id,
                    "execution",
                    ["firmware_inspection", error.category],
                ),
            )
            update["build_verified"] = build_verified
            update["evidence_ids"] = evidence_ids
            if build_evidence is not None:
                update["build_evidence"] = build_evidence
            return update
        except ValidationError as error:
            update = _failure_update(
                state,
                graph,
                task_id,
                category="schema",
                message="固件检查器返回了无效证据",
                signature=failure_signature(
                    task_id,
                    "schema",
                    ["firmware_resource_evidence"],
                ),
                errors=error.errors(include_url=False),
            )
            update["build_verified"] = build_verified
            update["evidence_ids"] = evidence_ids
            if build_evidence is not None:
                update["build_evidence"] = build_evidence
            return update
        firmware_results = _firmware_resource_verifier.verify(
            plan.firmware_assertions,
            firmware_resource_evidence,
        )
        failed_firmware = [
            result.assertion_id for result in firmware_results if not result.passed
        ]
        if failed_firmware:
            update = _failure_update(
                state,
                graph,
                task_id,
                category="semantic",
                message="固件资源断言未通过",
                signature=failure_signature(
                    task_id,
                    "semantic",
                    ["firmware_assertion", *failed_firmware],
                ),
            )
            update.update(
                {
                    "build_evidence": build_evidence,
                    "firmware_resource_evidence": firmware_resource_evidence,
                    "build_verified": build_verified,
                    "evidence_ids": evidence_ids,
                    "verification_runs": {
                        **state.get("verification_runs", {}),
                        task_id: VerificationRun(
                            task_id=task_id,
                            source_results=source_results,
                            component_test_evidence=component_test_evidence,
                            build_evidence=build_evidence,
                            firmware_resource_evidence=firmware_resource_evidence,
                            firmware_results=firmware_results,
                            build_verified=build_verified,
                            success=False,
                        ),
                    },
                }
            )
            return update
        evidence_ids.extend(
            result.evidence_id
            for result in firmware_results
            if result.evidence_id is not None
        )

    if plan.require_flash:
        if (
            context.flasher is None
            or context.project_path is None
            or context.serial_port is None
        ):
            update = _failure_update(
                state,
                graph,
                task_id,
                category="execution",
                message="验收要求烧录，但烧录器、工程路径或串口未配置",
                signature=failure_signature(
                    task_id,
                    "execution",
                    ["device_flash_unavailable"],
                ),
            )
            update["build_evidence"] = build_evidence
            update["build_verified"] = build_verified
            update["evidence_ids"] = evidence_ids
            return update
        try:
            combined = getattr(context.flasher, "flash_and_monitor", None)
            if flash_evidence is not None:
                # Successful side-effect evidence from the previous attempt is
                # authoritative; only monitor/read-only verification continues.
                pass
            elif callable(combined):
                # 生产设备适配器按顺序执行有界 flash 和短窗口 monitor，
                # 因此 monitor_evidence 已经包含烧录完成后的启动日志。
                raw_flash, raw_monitor = combined(
                    context.project_path,
                    context.serial_port,
                    context.monitor_timeout_seconds,
                )
                flash_evidence = (
                    raw_flash
                    if isinstance(raw_flash, FlashEvidence)
                    else FlashEvidence.model_validate(raw_flash)
                )
                monitor_evidence = (
                    raw_monitor
                    if isinstance(raw_monitor, MonitorEvidence)
                    else MonitorEvidence.model_validate(raw_monitor)
                )
            else:
                raw_flash = context.flasher.flash(
                    context.project_path,
                    context.serial_port,
                )
                flash_evidence = (
                    raw_flash
                    if isinstance(raw_flash, FlashEvidence)
                    else FlashEvidence.model_validate(raw_flash)
                )
        except EspIdfError as error:
            update = _failure_update(
                state,
                graph,
                task_id,
                category="execution",
                message=f"设备烧录失败: {error.category}",
                signature=failure_signature(
                    task_id,
                    "execution",
                    ["device_flash", error.category],
                ),
            )
            update["build_evidence"] = build_evidence
            update["build_verified"] = build_verified
            update["evidence_ids"] = evidence_ids
            return update
        if not flash_evidence.success:
            update = _failure_update(
                state,
                graph,
                task_id,
                category="execution",
                message=(
                    "设备烧录未通过: "
                    f"{flash_evidence.error_category or 'unknown'}"
                ),
                signature=failure_signature(
                    task_id,
                    "execution",
                    [
                        "device_flash",
                        flash_evidence.error_category or "unknown",
                        str(flash_evidence.return_code),
                    ],
                ),
            )
            update["build_evidence"] = build_evidence
            update["flash_evidence"] = flash_evidence
            update["build_verified"] = build_verified
            update["evidence_ids"] = evidence_ids
            return update
        if f"flash:{task_id}" not in evidence_ids:
            evidence_ids.append(f"flash:{task_id}")

    device_results = []
    if plan.require_device:
        if (
            (monitor_evidence is None and context.monitor is None)
            or context.project_path is None
            or context.serial_port is None
        ):
            update = _failure_update(
                state,
                graph,
                task_id,
                category="execution",
                message="验收要求设备证据，但串口监控能力未配置",
                signature=failure_signature(
                    task_id,
                    "execution",
                    ["device_monitor_unavailable"],
                ),
            )
            if build_evidence is not None:
                update["build_evidence"] = build_evidence
            if flash_evidence is not None:
                update["flash_evidence"] = flash_evidence
            update["build_verified"] = build_verified
            update["hardware_function_verified"] = False
            update["evidence_ids"] = evidence_ids
            return update
        if monitor_evidence is None:
            try:
                raw_monitor = context.monitor.monitor(
                    context.project_path,
                    context.serial_port,
                    context.monitor_timeout_seconds,
                )
                monitor_evidence = (
                    raw_monitor
                    if isinstance(raw_monitor, MonitorEvidence)
                    else MonitorEvidence.model_validate(raw_monitor)
                )
            except EspIdfError as error:
                update = _failure_update(
                    state,
                    graph,
                    task_id,
                    category="execution",
                    message=f"设备日志采集失败: {error.category}",
                    signature=failure_signature(
                        task_id,
                        "execution",
                        ["monitor", error.category],
                    ),
                )
                update["build_evidence"] = build_evidence
                if flash_evidence is not None:
                    update["flash_evidence"] = flash_evidence
                update["build_verified"] = build_verified
                update["hardware_function_verified"] = False
                update["evidence_ids"] = evidence_ids
                return update

        assertions = list(plan.device_assertions)
        if not assertions:
            assertions = [
                DeviceLogAssertion(
                    assertion_id=f"{task_id}:no-fatal",
                    operator="no_fatal_diagnostics",
                    description="设备日志无 panic、assert、watchdog 或错误诊断",
                )
            ]
        device_results = _device_log_verifier.verify(assertions, monitor_evidence)
        failed_device = [
            result.assertion_id for result in device_results if not result.passed
        ]
        if failed_device:
            update = _failure_update(
                state,
                graph,
                task_id,
                category="semantic",
                message="设备日志断言未通过",
                signature=failure_signature(
                    task_id,
                    "semantic",
                    ["device_assertion", *failed_device],
                ),
            )
            run = VerificationRun(
                task_id=task_id,
                source_results=source_results,
                component_test_evidence=component_test_evidence,
                build_evidence=build_evidence,
                flash_evidence=flash_evidence,
                firmware_resource_evidence=firmware_resource_evidence,
                firmware_results=firmware_results,
                monitor_evidence=monitor_evidence,
                device_results=device_results,
                build_verified=build_verified,
                hardware_verified=False,
                success=False,
            )
            update.update(
                {
                    "build_verified": build_verified,
                    "hardware_function_verified": False,
                    "evidence_ids": evidence_ids,
                    "verification_runs": {
                        **state.get("verification_runs", {}),
                        task_id: run,
                    },
                }
            )
            if build_evidence is not None:
                update["build_evidence"] = build_evidence
            if flash_evidence is not None:
                update["flash_evidence"] = flash_evidence
            if monitor_evidence is not None:
                update["monitor_evidence"] = monitor_evidence
            return update
        hardware_verified = True
        if plan.device_assertions:
            evidence_ids.extend(
                result.evidence_id
                for result in device_results
                if result.evidence_id is not None
            )
        else:
            evidence_ids.append(f"device:{task_id}")

    protocol_probe_evidence: list[ProtocolProbeEvidence] = []
    protocol_results = []
    if plan.protocol_probes:
        if context.protocol_probe is None or context.project_path is None:
            update = _failure_update(
                state,
                graph,
                task_id,
                category="execution",
                message="验收要求协议探测，但未配置受控协议探测器或项目路径",
                signature=failure_signature(
                    task_id,
                    "execution",
                    ["protocol_probe_unavailable"],
                ),
            )
            update["build_verified"] = build_verified
            update["hardware_function_verified"] = False
            update["evidence_ids"] = evidence_ids
            return update
        for spec in plan.protocol_probes:
            try:
                raw_probe = context.protocol_probe.run_protocol_probe(
                    context.project_path,
                    spec,
                )
                probe_evidence = (
                    raw_probe
                    if isinstance(raw_probe, ProtocolProbeEvidence)
                    else ProtocolProbeEvidence.model_validate(raw_probe)
                )
            except VerificationToolError as error:
                update = _failure_update(
                    state,
                    graph,
                    task_id,
                    category="execution",
                    message=f"协议探测执行失败: {error.category}",
                    signature=failure_signature(
                        task_id,
                        "execution",
                        ["protocol_probe", spec.probe_id, error.category],
                    ),
                )
                update["build_verified"] = build_verified
                update["hardware_function_verified"] = False
                update["evidence_ids"] = evidence_ids
                return update
            except ValidationError as error:
                update = _failure_update(
                    state,
                    graph,
                    task_id,
                    category="schema",
                    message="协议探测器返回了无效证据",
                    signature=failure_signature(
                        task_id,
                        "schema",
                        ["protocol_probe_evidence", spec.probe_id],
                    ),
                    errors=error.errors(include_url=False),
                )
                update["build_verified"] = build_verified
                update["hardware_function_verified"] = False
                update["evidence_ids"] = evidence_ids
                return update
            protocol_probe_evidence.append(probe_evidence)
            result = _protocol_probe_verifier.verify(spec, probe_evidence)
            protocol_results.append(result)
            if not result.passed:
                update = _failure_update(
                    state,
                    graph,
                    task_id,
                    category="semantic",
                    message=f"协议探测未通过: {spec.probe_id}",
                    signature=failure_signature(
                        task_id,
                        "semantic",
                        ["protocol_probe_failed", spec.probe_id, result.summary],
                    ),
                )
                update.update(
                    {
                        "build_verified": build_verified,
                        "hardware_function_verified": False,
                        "evidence_ids": evidence_ids,
                        "verification_runs": {
                            **state.get("verification_runs", {}),
                            task_id: VerificationRun(
                                task_id=task_id,
                                source_results=source_results,
                                component_test_evidence=component_test_evidence,
                                build_evidence=build_evidence,
                                flash_evidence=flash_evidence,
                                firmware_resource_evidence=firmware_resource_evidence,
                                firmware_results=firmware_results,
                                monitor_evidence=monitor_evidence,
                                device_results=device_results,
                                protocol_probe_evidence=protocol_probe_evidence,
                                protocol_results=protocol_results,
                                build_verified=build_verified,
                                hardware_verified=False,
                                success=False,
                            ),
                        },
                    }
                )
                return update
            if result.evidence_id is not None:
                evidence_ids.append(result.evidence_id)

    runtime_scenario_evidence: list[RuntimeScenarioEvidence] = []
    runtime_results = []
    if plan.runtime_scenarios:
        if context.runtime_scenario_runner is None or context.project_path is None:
            update = _failure_update(
                state,
                graph,
                task_id,
                category="execution",
                message="验收要求运行韧性场景，但未配置受控场景执行器或项目路径",
                signature=failure_signature(
                    task_id,
                    "execution",
                    ["runtime_scenario_unavailable"],
                ),
            )
            update["build_verified"] = build_verified
            update["hardware_function_verified"] = False
            update["evidence_ids"] = evidence_ids
            return update
        for spec in plan.runtime_scenarios:
            try:
                raw_scenario = context.runtime_scenario_runner.run_runtime_scenario(
                    context.project_path,
                    spec,
                )
                scenario_evidence = (
                    raw_scenario
                    if isinstance(raw_scenario, RuntimeScenarioEvidence)
                    else RuntimeScenarioEvidence.model_validate(raw_scenario)
                )
            except VerificationToolError as error:
                update = _failure_update(
                    state,
                    graph,
                    task_id,
                    category="execution",
                    message=f"运行韧性场景执行失败: {error.category}",
                    signature=failure_signature(
                        task_id,
                        "execution",
                        ["runtime_scenario", spec.scenario_id, error.category],
                    ),
                )
                update["build_verified"] = build_verified
                update["hardware_function_verified"] = False
                update["evidence_ids"] = evidence_ids
                return update
            except ValidationError as error:
                update = _failure_update(
                    state,
                    graph,
                    task_id,
                    category="schema",
                    message="运行场景执行器返回了无效证据",
                    signature=failure_signature(
                        task_id,
                        "schema",
                        ["runtime_scenario_evidence", spec.scenario_id],
                    ),
                    errors=error.errors(include_url=False),
                )
                update["build_verified"] = build_verified
                update["hardware_function_verified"] = False
                update["evidence_ids"] = evidence_ids
                return update
            runtime_scenario_evidence.append(scenario_evidence)
            result = _runtime_scenario_verifier.verify(spec, scenario_evidence)
            runtime_results.append(result)
            if not result.passed:
                update = _failure_update(
                    state,
                    graph,
                    task_id,
                    category="semantic",
                    message=f"运行韧性场景未通过: {spec.scenario_id}",
                    signature=failure_signature(
                        task_id,
                        "semantic",
                        ["runtime_scenario_failed", spec.scenario_id, result.summary],
                    ),
                )
                update.update(
                    {
                        "build_verified": build_verified,
                        "hardware_function_verified": False,
                        "evidence_ids": evidence_ids,
                        "verification_runs": {
                            **state.get("verification_runs", {}),
                            task_id: VerificationRun(
                                task_id=task_id,
                                source_results=source_results,
                                component_test_evidence=component_test_evidence,
                                build_evidence=build_evidence,
                                flash_evidence=flash_evidence,
                                firmware_resource_evidence=firmware_resource_evidence,
                                firmware_results=firmware_results,
                                monitor_evidence=monitor_evidence,
                                device_results=device_results,
                                protocol_probe_evidence=protocol_probe_evidence,
                                protocol_results=protocol_results,
                                runtime_scenario_evidence=runtime_scenario_evidence,
                                runtime_results=runtime_results,
                                build_verified=build_verified,
                                hardware_verified=False,
                                success=False,
                            ),
                        },
                    }
                )
                return update
            if result.evidence_id is not None:
                evidence_ids.append(result.evidence_id)

    passed_graph = graph.update_task(
        task_id,
        status="passed",
        attempts=task.attempts + 1,
    )
    evidence_ids.append(f"task:{task_id}")
    run = VerificationRun(
        task_id=task_id,
        source_results=source_results,
        component_test_evidence=component_test_evidence,
        build_evidence=build_evidence,
        flash_evidence=flash_evidence,
        firmware_resource_evidence=firmware_resource_evidence,
        firmware_results=firmware_results,
        monitor_evidence=monitor_evidence,
        device_results=device_results,
        protocol_probe_evidence=protocol_probe_evidence,
        protocol_results=protocol_results,
        runtime_scenario_evidence=runtime_scenario_evidence,
        runtime_results=runtime_results,
        build_verified=build_verified,
        hardware_verified=hardware_verified,
        success=True,
    )
    update = {
        "task_graph": passed_graph,
        "current_task_id": task_id,
        "evidence_ids": list(dict.fromkeys(evidence_ids)),
        "verification_runs": {
            **state.get("verification_runs", {}),
            task_id: run,
        },
        "build_verified": build_verified,
        "hardware_function_verified": hardware_verified,
        "trace": _trace(state, "task_executor"),
    }
    if build_evidence is not None:
        update["build_evidence"] = build_evidence
    if flash_evidence is not None:
        update["flash_evidence"] = flash_evidence
    if firmware_resource_evidence is not None:
        update["firmware_resource_evidence"] = firmware_resource_evidence
    if monitor_evidence is not None:
        update["monitor_evidence"] = monitor_evidence
    return update


def task_executor(
    state: AgentState,
    runtime: Runtime[AgentRuntimeContext],
) -> dict[str, object]:
    context = runtime.context or AgentRuntimeContext()
    graph = state["task_graph"]
    if isinstance(graph, dict):
        graph = AgentTaskGraph.model_validate(graph)
    ready = graph.ready_tasks()
    if not ready:
        return {
            "last_error": "任务图没有就绪任务",
            "trace": _trace(state, "task_executor"),
        }
    task = ready[0]
    if task.requires_approval:
        tool_actions = {
            "workspace.read": "读取当前工程文件并建立非回归基线",
            "project.inspect": "检查项目结构、组件和已有能力",
            "project.plan": "根据目标和现状计算实现边界",
            "workspace.patch": "在允许范围内修改工程文件",
            "source.validate": "校验源码变更及 preserve 约束",
            "source.assert": "检查源码级验收断言",
            "acceptance.verify": "汇总工具证据并逐项判定验收条件",
            "component.test": "运行组件测试",
            "espidf.build": "执行 ESP-IDF 构建",
            "device.flash": "将构建产物烧录到所选开发板",
            "firmware.inspect": "检查固件资源和静态属性",
            "device.monitor": "采集并检查设备运行日志",
            "protocol.probe": "执行协议交互探测",
            "runtime.scenario": "执行运行时韧性场景",
        }
        planned_actions = [
            tool_actions.get(tool, f"调用受控工具 {tool}")
            for tool in task.allowed_tools
        ]
        criteria = (
            graph.acceptance_criteria
            if task.kind == "verify_acceptance"
            else [
                criterion
                for criterion in graph.acceptance_criteria
                if criterion.criterion_id in task.acceptance_criteria
            ]
        )
        preserve_conditions = list(
            dict.fromkeys(
                [
                    preserve
                    for graph_task in graph.tasks
                    for preserve in graph_task.preserves
                ]
            )
        )
        affected_targets = list(task.allowed_paths)
        if "device.flash" in task.allowed_tools:
            affected_targets.append(
                "开发板串口 " + (context.serial_port or "当前所选串口")
            )
        if not affected_targets:
            affected_targets.append(
                f"项目 {state.get('project_name', '当前工程')} 的验证状态"
            )
        includes_flash = "device.flash" in task.allowed_tools
        summary = (
            f"准备执行“{task.title}”。该任务包含设备烧录，批准后会先按计划"
            "完成验证，再改写所选开发板固件。"
            if includes_flash
            else (
                f"准备执行“{task.title}”。该任务会调用受控工具并可能修改"
                "工程或外部状态，因此需要本次明确授权。"
            )
        )
        risks = (
            [
                "烧录会覆盖所选开发板当前固件",
                "串口或目标芯片选择错误会导致烧录失败或设备不可用",
                "只有工具证据全部满足后才会宣告任务完成",
            ]
            if includes_flash
            else [
                "该操作可能修改工程或外部状态",
                "执行范围受任务允许路径和工具白名单限制",
            ]
        )
        request = AgentApprovalRequest(
            task_id=task.task_id,
            title=f"审批任务：{task.title}",
            summary=summary,
            operation=(
                "device.flash"
                if "device.flash" in task.allowed_tools
                else task.allowed_tools[0]
                if task.allowed_tools
                else task.kind
            ),
            risks=risks,
            task_description=task.description,
            planned_actions=planned_actions,
            tools=list(task.allowed_tools),
            affected_targets=affected_targets,
            acceptance_criteria=[item.description for item in criteria],
            preserve_conditions=preserve_conditions,
        )
        decision = WorkflowDecision.model_validate(
            interrupt(request.model_dump(mode="json"))
        )
        if not decision.approved:
            blocked_graph = graph.update_task(
                task.task_id,
                status="blocked",
                attempts=task.attempts + 1,
            )
            feedback = {
                task_id: list(items)
                for task_id, items in state.get("task_feedback", {}).items()
            }
            if decision.feedback:
                feedback.setdefault(task.task_id, []).append(decision.feedback)
            return {
                "task_graph": blocked_graph,
                "current_task_id": task.task_id,
                "approval_request": request,
                "approval_status": "rejected",
                "task_feedback": feedback,
                "last_error": "用户拒绝高风险任务审批",
                "trace": _trace(state, "task_executor"),
            }
        graph = graph.update_task(
            task.task_id,
            status="ready",
            requires_approval=False,
        )
        return {
            "task_graph": graph,
            "current_task_id": task.task_id,
            "approval_request": request,
            "approval_status": "approved",
            "evidence_ids": [
                *state.get("evidence_ids", []),
                f"approval:{task.task_id}",
            ],
            "trace": _trace(state, "task_executor"),
        }

    if task.kind == "code_change" and context.code_executor is None:
        return _failure_update(
            state,
            graph,
            task.task_id,
            category="execution",
            message="code_change 任务缺少受控代码执行器",
            signature=failure_signature(
                task.task_id,
                "execution",
                ["missing_code_executor"],
            ),
        )

    if task.kind == "verify_acceptance" and state.get("verification_plan") is not None:
        return _execute_verification_task(
            state,
            graph,
            task.task_id,
            context,
        )

    if task.kind == "code_change" and context.code_executor is not None:
        raw_bundle = state.get("change_bundles", {}).get(task.task_id)
        if raw_bundle is None and context.code_engineer is not None:
            objective = state.get("objective")
            project_model = state.get("project_model")
            if isinstance(objective, dict):
                objective = ProjectObjective.model_validate(objective)
            if isinstance(project_model, dict):
                project_model = ProjectModel.model_validate(project_model)
            if not isinstance(objective, ProjectObjective) or not isinstance(
                project_model,
                ProjectModel,
            ):
                return _failure_update(
                    state,
                    graph,
                    task.task_id,
                    category="semantic",
                    message="Code Engineer 缺少目标或项目模型",
                    signature=failure_signature(
                        task.task_id,
                        "semantic",
                        ["missing_engineer_context"],
                    ),
                )

            raw_files = state.get("project_files", [])
            files = [
                item
                if isinstance(item, ProjectFile)
                else ProjectFile.model_validate(item)
                for item in raw_files
            ]
            if context.workspace is not None and context.project_path is not None:
                try:
                    files = context.workspace.read_project_files(
                        context.project_path
                    )
                except WorkspaceError as error:
                    return _failure_update(
                        state,
                        graph,
                        task.task_id,
                        category="execution",
                        message=f"Code Engineer 读取工程失败: {error.category}",
                        signature=failure_signature(
                            task.task_id,
                            "execution",
                            ["engineer_workspace", error.category],
                        ),
                    )
            try:
                previous_build = state.get("build_evidence")
                if isinstance(previous_build, dict):
                    previous_build = BuildEvidence.model_validate(previous_build)
                raw_bundle = context.code_engineer.create_bundle(
                    objective,
                    task,
                    project_model,
                    files,
                    build_evidence=previous_build,
                    failure_feedback=list(
                        state.get("task_feedback", {}).get(task.task_id, [])
                    ),
                )
            except CapabilityError as error:
                category = (
                    "schema"
                    if error.category in {
                        "empty_response",
                        "invalid_json",
                        "invalid_schema",
                    }
                    else "execution"
                )
                return _failure_update(
                    state,
                    graph,
                    task.task_id,
                    category=category,
                    message="Code Engineer 未能生成受限变更",
                    signature=failure_signature(
                        task.task_id,
                        category,
                        ["code_engineer", error.category],
                    ),
                )
        if raw_bundle is None:
            return _failure_update(
                state,
                graph,
                task.task_id,
                category="semantic",
                message="code_change 任务缺少受限 ChangeBundle",
                signature=failure_signature(
                    task.task_id,
                    "semantic",
                    ["missing_bundle"],
                ),
            )

        schema_errors: list[dict[str, object]] = []

        def repair_bundle(
            payload: object,
            errors: list[dict[str, object]],
        ) -> object:
            schema_errors.extend(errors)
            if context.schema_repair is None:
                return payload
            return context.schema_repair("ChangeBundle", payload, errors)

        try:
            bundle = (
                raw_bundle
                if isinstance(raw_bundle, ChangeBundle)
                else validate_with_one_repair(
                    ChangeBundle,
                    raw_bundle,
                    repair=(repair_bundle if context.schema_repair else None),
                )
            )
        except SchemaRepairExhausted as error:
            signature_details = [
                f"{item.get('type', 'validation_error')}:{item.get('loc', [])}"
                for item in error.errors
            ]
            return _failure_update(
                state,
                graph,
                task.task_id,
                category="schema",
                message="code_change 任务的 ChangeBundle Schema 无法修复",
                signature=failure_signature(
                    task.task_id,
                    "schema",
                    ["ChangeBundle", *signature_details],
                ),
                errors=error.errors,
            )

        if bundle.task_id != task.task_id:
            return _failure_update(
                state,
                graph,
                task.task_id,
                category="semantic",
                message="code_change 任务与 ChangeBundle 归属不一致",
                signature=failure_signature(
                    task.task_id,
                    "semantic",
                    ["bundle_task_mismatch"],
                ),
            )

        try:
            # The task graph is authoritative.  A model cannot broaden its file
            # scope or drop preserve invariants by returning weaker bundle data.
            bundle = ChangeBundle(
                bundle_id=bundle.bundle_id,
                task_id=bundle.task_id,
                description=bundle.description,
                changes=bundle.changes,
                allowed_paths=(task.allowed_paths or bundle.allowed_paths),
                preserves=list(
                    dict.fromkeys([*task.preserves, *bundle.preserves])
                ),
            )
        except ValidationError:
            return _failure_update(
                state,
                graph,
                task.task_id,
                category="semantic",
                message="ChangeBundle 超出 code_change 任务允许路径",
                signature=failure_signature(
                    task.task_id,
                    "semantic",
                    ["allowed_paths"],
                ),
            )

        if context.project_path is None:
            return _failure_update(
                state,
                graph,
                task.task_id,
                category="execution",
                message="code_change 任务缺少项目路径",
                signature=failure_signature(
                    task.task_id,
                    "execution",
                    ["missing_project_path"],
                ),
            )

        try:
            validation = context.code_executor.execute(
                context.project_path,
                bundle,
            )
        except ChangeBundleError as error:
            detail = "；".join(error.details[:8])
            return _failure_update(
                state,
                graph,
                task.task_id,
                category="semantic",
                message=(
                    f"代码变更未通过 {error.category} 校验：{error}"
                    + (f"；详情：{detail}" if detail else "")
                ),
                signature=failure_signature(
                    task.task_id,
                    "semantic",
                    [error.category, *error.details],
                ),
            )
        except WorkspaceError as error:
            return _failure_update(
                state,
                graph,
                task.task_id,
                category="execution",
                message=f"代码变更执行失败: {error.category}",
                signature=failure_signature(
                    task.task_id,
                    "execution",
                    [error.category],
                ),
            )

        passed_graph = graph.update_task(
            task.task_id,
            status="passed",
            attempts=task.attempts + 1,
        )
        evidence_ids = [
            *state.get("evidence_ids", []),
            f"task:{task.task_id}",
            f"bundle:{bundle.bundle_id}",
        ]
        validations = dict(state.get("change_validations", {}))
        validations[task.task_id] = validation
        update: dict[str, object] = {
            "task_graph": passed_graph,
            "current_task_id": task.task_id,
            "evidence_ids": evidence_ids,
            "change_validations": validations,
            "applied_changes": [
                *state.get("applied_changes", []),
                *[
                    AppliedFileChange(
                        task_id=task.task_id,
                        path=change.path,
                        operation=change.operation,
                        summary=(
                            change.summary.strip()
                            or bundle.description.strip()
                            or f"{change.operation} {change.path}"
                        )[:500],
                    )
                    for change in bundle.changes
                ],
            ],
            "trace": _trace(state, "task_executor"),
        }
        if schema_errors:
            repairs = list(state.get("schema_repairs", []))
            repairs.append(
                {
                    "task_id": task.task_id,
                    "model": "ChangeBundle",
                    "attempt": task.attempts + 1,
                    "errors": schema_errors,
                }
            )
            update["schema_repairs"] = repairs
        return update

    passed_graph = graph.update_task(
        task.task_id,
        status="passed",
        attempts=task.attempts + 1,
    )
    evidence_ids = [*state.get("evidence_ids", []), f"task:{task.task_id}"]
    return {
        "task_graph": passed_graph,
        "current_task_id": task.task_id,
        "evidence_ids": evidence_ids,
        "trace": _trace(state, "task_executor"),
    }


def acceptance_verifier(state: AgentState) -> dict[str, object]:
    graph = state["task_graph"]
    if isinstance(graph, dict):
        graph = AgentTaskGraph.model_validate(graph)
    result = _verifier.verify(graph.acceptance_criteria, state.get("evidence_ids", []))
    raw_plan = state.get("verification_plan")
    verification_plan = (
        raw_plan
        if isinstance(raw_plan, VerificationPlan)
        else VerificationPlan.model_validate(raw_plan)
        if raw_plan is not None
        else None
    )
    all_passed = result.all_passed and not (
        verification_plan is not None
        and verification_plan.require_device
        and not state.get("hardware_function_verified", False)
    )
    updated_graph = graph.model_copy(update={"acceptance_criteria": result.criteria})
    return {
        "task_graph": updated_graph,
        "acceptance_criteria": result.criteria,
        "acceptance_passed": all_passed,
        "trace": _trace(state, "acceptance_verifier"),
    }


def supervisor(state: AgentState) -> dict[str, object]:
    step_count = state.get("step_count", 0) + 1
    max_steps = state.get("max_steps", 40)
    graph = state.get("task_graph")
    if isinstance(graph, dict):
        graph = AgentTaskGraph.model_validate(graph)
    interpretation = state.get("interpretation")
    if isinstance(interpretation, dict):
        from luxar.domain.agent.changes import ObjectiveInterpretation

        interpretation = ObjectiveInterpretation.model_validate(interpretation)

    action: str
    target_id: str | None = None
    rationale: str
    required_inputs: list[str] = []
    if step_count > max_steps:
        action = "fail_objective"
        rationale = "达到 Supervisor 最大步骤预算，停止继续执行"
    elif interpretation is not None and interpretation.intent == "ask_question":
        action = "answer_user"
        rationale = "最新消息是知识问题，不改变当前项目目标"
    elif not state.get("inspection_complete", False):
        action = "inspect_project"
        rationale = "尚未建立源码和项目能力基线"
        required_inputs = ["project_files"]
    elif state.get("hardware_blocked"):
        action = "degrade_capability"
        rationale = "硬件规则发现阻塞问题，保留项目状态并等待引脚或总线调整"
        target_id = "hardware"
    elif state.get("planning_blocked"):
        action = "answer_user"
        rationale = "当前目标尚未形成可执行变更，等待补充工程输入"
    elif not state.get("hardware_validated", False):
        action = "validate_hardware"
        rationale = "在生成代码任务前验证芯片、引脚和总线资源约束"
        required_inputs = ["project_model", "hardware_report"]
    elif graph is None:
        action = "plan_tasks"
        rationale = "已有项目能力和目标，生成分层任务图"
        required_inputs = ["objective", "change_set", "capabilities"]
    elif graph.has_blocking_task:
        action = "degrade_capability"
        rationale = "当前任务无法安全继续，保留项目状态并降级该能力"
        target_id = next(
            task.task_id
            for task in graph.tasks
            if task.status in {"blocked", "failed"}
        )
    elif state.get("acceptance_passed"):
        action = "complete_objective"
        rationale = "所有强制验收条件均已取得工具证据"
    elif graph.all_tasks_passed:
        action = "verify_acceptance"
        rationale = "实现任务完成，进入证据验收"
        required_inputs = ["task_graph", "evidence_ids"]
    else:
        ready = graph.ready_tasks()
        if ready:
            action = "execute_task"
            target_id = ready[0].task_id
            rationale = "选择依赖已满足的最小就绪任务"
        else:
            action = "fail_objective"
            rationale = "任务图没有就绪任务且无法安全继续"

    decision = SupervisorDecision(
        action=action,
        target_id=target_id,
        rationale=rationale,
        required_inputs=required_inputs,
    )
    return {
        "decision": decision,
        "step_count": step_count,
        "trace": _trace(state, "supervisor"),
    }


def answer_user(state: AgentState) -> dict[str, object]:
    return {
        "status": "awaiting_user",
        "trace": _trace(state, "answer_user"),
    }


def complete_objective(state: AgentState) -> dict[str, object]:
    return {
        "status": "completed",
        "trace": _trace(state, "complete_objective"),
    }


def fail_objective(state: AgentState) -> dict[str, object]:
    return {
        "status": "failed",
        "trace": _trace(state, "fail_objective"),
    }


def degrade_capability(state: AgentState) -> dict[str, object]:
    return {
        "status": "blocked",
        "trace": _trace(state, "degrade_capability"),
    }


def route_after_supervisor(
    state: AgentState,
) -> Literal[
    "project_inspector",
    "hardware_validator",
    "architecture_planner",
    "task_executor",
    "acceptance_verifier",
    "answer_user",
    "complete_objective",
    "fail_objective",
    "degrade_capability",
]:
    decision = state["decision"]
    if isinstance(decision, dict):
        decision = SupervisorDecision.model_validate(decision)
    action = decision.action
    return {
        "inspect_project": "project_inspector",
        "validate_hardware": "hardware_validator",
        "plan_tasks": "architecture_planner",
        "execute_task": "task_executor",
        "verify_acceptance": "acceptance_verifier",
        "answer_user": "answer_user",
        "complete_objective": "complete_objective",
        "fail_objective": "fail_objective",
        "degrade_capability": "degrade_capability",
    }.get(action, "fail_objective")  # type: ignore[return-value]


def build_agent_graph(
    *,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    builder = StateGraph(
        AgentState,
        context_schema=AgentRuntimeContext,
    )
    builder.add_node("load_project_session", load_project_session)
    builder.add_node("supervisor", supervisor)
    builder.add_node("project_inspector", project_inspector)
    builder.add_node("hardware_validator", hardware_validator)
    builder.add_node("architecture_planner", architecture_planner)
    builder.add_node("task_executor", task_executor)
    builder.add_node("acceptance_verifier", acceptance_verifier)
    builder.add_node("answer_user", answer_user)
    builder.add_node("complete_objective", complete_objective)
    builder.add_node("fail_objective", fail_objective)
    builder.add_node("degrade_capability", degrade_capability)

    builder.add_edge(START, "load_project_session")
    builder.add_edge("load_project_session", "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {
            "project_inspector": "project_inspector",
            "hardware_validator": "hardware_validator",
            "architecture_planner": "architecture_planner",
            "task_executor": "task_executor",
            "acceptance_verifier": "acceptance_verifier",
            "answer_user": "answer_user",
            "complete_objective": "complete_objective",
            "fail_objective": "fail_objective",
            "degrade_capability": "degrade_capability",
        },
    )
    for worker in (
        "project_inspector",
        "hardware_validator",
        "architecture_planner",
        "task_executor",
        "acceptance_verifier",
    ):
        builder.add_edge(worker, "supervisor")
    builder.add_edge("answer_user", END)
    builder.add_edge("complete_objective", END)
    builder.add_edge("fail_objective", END)
    builder.add_edge("degrade_capability", END)
    return builder.compile(checkpointer=checkpointer)
