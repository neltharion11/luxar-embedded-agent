from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Iterator

from luxar.core.app_generator import AppGenerator
from luxar.core.asset_reuse import AssetReuseAdvisor
from luxar.core.backup_manager import BackupManager
from luxar.core.code_fixer import CodeFixer, parse_build_error_lines
from luxar.core.config_manager import AgentConfig
from luxar.core.document_engineering import DocumentEngineeringAnalyzer
from luxar.core.driver_library import DriverLibrary
from luxar.core.driver_pipeline import DriverPipeline
from luxar.core.project_planner import ProjectPlanner
from luxar.core.review_engine import ReviewEngine
from luxar.models.schemas import (
    CodeFixResult,
    DriverMetadata,
    DriverRequirement,
    EngineeringContext,
    ProjectConfig,
    ProjectPlan,
    ReviewIssue,
    ReviewReport,
    WorkflowRunResult,
    WorkflowStepResult,
)
from luxar.tools.assemble_project import run_assemble_project
from luxar.tools.build_project import run_build_project
from luxar.tools.flash_project import run_flash_project
from luxar.tools.monitor_project import run_monitor_project


def _workflow_event(event_type: str, *, step: str = "", message: str = "", status: str = "", payload: dict | None = None) -> dict:
    return {
        "type": event_type,
        "workflow": "forge",
        "step": step,
        "message": message,
        "status": status,
        "payload": payload or {},
    }


def _syntax_check_generated(
    *,
    project_path: str,
    generated_files: list[str],
    project_root: str,
) -> tuple[bool, list[ReviewIssue]]:
    project_dir = Path(project_path).resolve()
    arm_gcc = shutil.which("arm-none-eabi-gcc")
    if not arm_gcc:
        arm_gcc = str(Path(project_root).resolve() / "workspace" / "toolchains" / "gcc-arm" / "bin" / "arm-none-eabi-gcc.exe")
    if not Path(arm_gcc).exists():
        return True, []

    include_dirs = []
    for candidate in [
        project_dir / "Core" / "Inc",
        project_dir / "App" / "Inc",
        project_dir / "Drivers" / "CMSIS" / "Include",
        project_dir / "Drivers" / "CMSIS" / "Core" / "Include",
    ]:
        if candidate.exists():
            include_dirs.append(str(candidate))

    for hal_driver in (project_dir / "Drivers").glob("STM32*HAL_Driver/Inc"):
        include_dirs.append(str(hal_driver))
    for device_dir in (project_dir / "Drivers" / "CMSIS" / "Device" / "ST").glob("*/Include"):
        include_dirs.append(str(device_dir))
    for app_driver_dir in (project_dir / "App" / "Drivers").glob("*/Inc"):
        include_dirs.append(str(app_driver_dir))

    c_files = [f for f in generated_files if f.endswith(".c")]
    if not c_files:
        return True, []

    cmd = [arm_gcc, "-fsyntax-only", "-std=c11", "-mcpu=cortex-m3", "-mthumb", "-DSTM32F1xx", "-DUSE_HAL_DRIVER"]
    metadata = project_dir / ".agent_project.json"
    if metadata.exists() and "STM32F103" in metadata.read_text(encoding="utf-8", errors="ignore").upper():
        cmd.append("-DSTM32F103xB")
    for inc in include_dirs:
        cmd.extend(["-I", inc])
    cmd.extend(c_files)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=str(project_dir), errors="replace")
    except (subprocess.TimeoutExpired, Exception):
        return True, []

    if result.returncode == 0:
        return True, []

    issues = parse_build_error_lines(build_stderr=result.stderr or "", stdout=result.stdout or "")
    if not issues and (result.stderr or "").strip():
        issues = [ReviewIssue(
            file=c_files[0],
            line=1,
            column=0,
            severity="error",
            rule_id="COMPILE",
            message=result.stderr.strip()[:300],
            suggestion="Fix the compilation error so the file compiles successfully.",
        )]
    return False, issues


def _restore_transaction_snapshot(project_path: str, snapshot_path: str) -> str:
    if not snapshot_path:
        return ""
    try:
        BackupManager(project_path).restore_snapshot(snapshot_path)
        return snapshot_path
    except Exception as exc:
        return f"restore_failed: {exc}"


def run_forge_project(
    *,
    config: AgentConfig,
    project_root: str,
    project: ProjectConfig,
    requirement: str,
    driver_library_root: str,
    drivers: list[str] | None = None,
    clean: bool = False,
    build: bool = True,
    plan_only: bool = False,
    no_flash: bool = False,
    no_monitor: bool = False,
    docs: list[str] | None = None,
    doc_query: str = "",
    probe: str | None = None,
    port: str = "",
    baudrate: int | None = None,
    ) -> WorkflowRunResult:
    final_result: dict | None = None
    for event in run_forge_project_stream(
        config=config,
        project_root=project_root,
        project=project,
        requirement=requirement,
        driver_library_root=driver_library_root,
        drivers=drivers,
        clean=clean,
        build=build,
        plan_only=plan_only,
        no_flash=no_flash,
        no_monitor=no_monitor,
        docs=docs,
        doc_query=doc_query,
        probe=probe,
        port=port,
        baudrate=baudrate,
    ):
        if event.get("type") in {"workflow_finished", "workflow_failed"}:
            payload = event.get("payload", {}) or {}
            if isinstance(payload.get("result"), dict):
                final_result = payload["result"]
    if final_result is None:
        raise RuntimeError("Forge workflow ended without a final result.")
    return WorkflowRunResult.model_validate(final_result)


def run_forge_project_stream(
    *,
    config: AgentConfig,
    project_root: str,
    project: ProjectConfig,
    requirement: str,
    driver_library_root: str,
    drivers: list[str] | None = None,
    clean: bool = False,
    build: bool = True,
    plan_only: bool = False,
    no_flash: bool = False,
    no_monitor: bool = False,
    docs: list[str] | None = None,
    doc_query: str = "",
    probe: str | None = None,
    port: str = "",
    baudrate: int | None = None,
) -> Iterator[dict]:
    docs = docs or []
    steps: list[WorkflowStepResult] = []
    yield _workflow_event(
        "workflow_started",
        message=f"开始执行 forge 工作流：{project.name}",
        payload={"project": project.model_dump(mode="json"), "requirement": requirement},
    )

    planner = ProjectPlanner(config)
    yield _workflow_event("workflow_step_started", step="parse_docs", message="准备文档上下文")
    engineering_context, doc_payload = _prepare_document_context(
        driver_library_root=driver_library_root,
        docs=docs,
        query=doc_query or requirement,
    )
    if docs:
        parse_step = WorkflowStepResult(
            name="parse_docs",
            status="completed",
            message=f"Parsed {len(docs)} document(s) and loaded forge document context.",
            payload=doc_payload,
        )
        steps.append(parse_step)
        yield _workflow_event("workflow_step_finished", step="parse_docs", status="completed", message=parse_step.message, payload=parse_step.payload)
    else:
        yield _workflow_event("workflow_step_finished", step="parse_docs", status="skipped", message="未附加文档，跳过文档解析。")

    yield _workflow_event("workflow_step_started", step="plan", message="生成项目计划")
    project_plan = planner.build_plan(
        project=project,
        requirement=requirement,
        document_context=engineering_context.document_summary,
        engineering_context=engineering_context,
    )
    plan_step = WorkflowStepResult(
        name="plan",
        status="completed",
        message="Derived a structured project plan from the natural-language requirement.",
        payload=project_plan.model_dump(mode="json"),
    )
    steps.append(plan_step)
    yield _workflow_event("workflow_step_finished", step="plan", status="completed", message=plan_step.message, payload=plan_step.payload)

    planned_driver_requirements = _merge_manual_driver_overrides(
        planned=project_plan.needed_drivers,
        manual_queries=drivers or [],
    )
    project_plan = planner.sanitize_plan(
        project=project,
        requirement=requirement,
        plan=project_plan.model_copy(update={"needed_drivers": planned_driver_requirements}),
    )
    planned_driver_requirements = project_plan.needed_drivers
    if plan_only:
        resolve_step = WorkflowStepResult(
            name="resolve_drivers",
            status="skipped",
            message="Plan-only mode skipped driver resolution and project assembly.",
            payload={"planned_drivers": [item.model_dump(mode="json") for item in planned_driver_requirements]},
        )
        steps.append(resolve_step)
        result = WorkflowRunResult(
            success=True,
            workflow="forge",
            steps=steps,
            summary="Project planning completed in plan-only mode.",
            output={
                "project": project.model_dump(mode="json"),
                "project_plan": project_plan.model_dump(mode="json"),
                "document_context": doc_payload,
                "planned_drivers": [item.model_dump(mode="json") for item in planned_driver_requirements],
            },
        )
        yield _workflow_event("workflow_step_finished", step="resolve_drivers", status="skipped", message=resolve_step.message, payload=resolve_step.payload)
        yield _workflow_event("workflow_finished", status="completed", message=result.summary, payload={"result": result.model_dump(mode="json")})
        return

    advisor = AssetReuseAdvisor(
        project_root=project_root,
        driver_library_root=driver_library_root,
        skill_library_root=str(Path(project_root).resolve() / config.agent.skill_library),
    )
    driver_library = DriverLibrary(driver_library_root)
    pipeline = DriverPipeline(config=config, project_root=project_root)

    resolved_driver_records: list[DriverMetadata] = []
    generated_driver_records: list[DriverMetadata] = []
    unresolved_requirements: list[DriverRequirement] = []
    reused_driver_payloads: list[dict] = []
    generated_driver_payloads: list[dict] = []

    yield _workflow_event("workflow_step_started", step="resolve_drivers", message="解析驱动需求")
    for item in planned_driver_requirements:
        candidate = advisor.select_reuse_candidate(
            chip=item.chip,
            interface=item.interface,
            vendor=item.vendor,
            device=item.device,
        )
        if candidate is None:
            unresolved_requirements.append(item)
            continue
        resolved_driver_records.append(candidate)
        reused_driver_payloads.append(candidate.model_dump(mode="json"))
        driver_library.record_reuse(candidate.path)
    resolve_step = WorkflowStepResult(
        name="resolve_drivers",
        status="completed",
        message="Resolved the driver requirements from project planning.",
        payload={
            "planned_drivers": [item.model_dump(mode="json") for item in planned_driver_requirements],
            "reused_count": len(reused_driver_payloads),
            "generate_count": len(unresolved_requirements),
        },
    )
    steps.append(resolve_step)
    yield _workflow_event("workflow_step_finished", step="resolve_drivers", status="completed", message=resolve_step.message, payload=resolve_step.payload)

    reuse_step = WorkflowStepResult(
        name="reuse_drivers",
        status="completed" if reused_driver_payloads else "skipped",
        message=(
            f"Reused {len(reused_driver_payloads)} reviewed driver(s) from the local library."
            if reused_driver_payloads
            else "No reviewed local drivers matched the plan strongly enough to reuse."
        ),
        payload={"drivers": reused_driver_payloads},
    )
    steps.append(reuse_step)
    yield _workflow_event("workflow_step_started", step="reuse_drivers", message="复用本地驱动资产")
    yield _workflow_event("workflow_step_finished", step="reuse_drivers", status=reuse_step.status, message=reuse_step.message, payload=reuse_step.payload)

    yield _workflow_event("workflow_step_started", step="generate_drivers", message="生成缺失驱动")
    if unresolved_requirements:
        for item in unresolved_requirements:
            pipeline_result = pipeline.generate_review_fix(
                chip=item.chip,
                interface=item.interface,
                protocol_summary=_driver_protocol_summary(project_plan, item),
                vendor=item.vendor,
                device=item.device,
            )
            generated_driver_payloads.append(pipeline_result.model_dump(mode="json"))
            if not pipeline_result.success:
                fail_step = WorkflowStepResult(
                    name="generate_drivers",
                    status="failed",
                    message=f"Failed to generate driver for {item.device or item.chip}.",
                    payload={"result": pipeline_result.model_dump(mode="json")},
                )
                steps.append(fail_step)
                result = WorkflowRunResult(
                    success=False,
                    workflow="forge",
                    steps=steps,
                    summary=f"Driver generation failed for {item.device or item.chip}.",
                    output={
                        "project": project.model_dump(mode="json"),
                        "project_plan": project_plan.model_dump(mode="json"),
                        "document_context": doc_payload,
                        "driver_result": pipeline_result.model_dump(mode="json"),
                    },
                )
                yield _workflow_event("workflow_step_finished", step="generate_drivers", status="failed", message=fail_step.message, payload=fail_step.payload)
                yield _workflow_event("workflow_failed", step="generate_drivers", status="failed", message=result.summary, payload={"result": result.model_dump(mode="json")})
                return
            generated_driver_records.extend(pipeline_result.stored_records)
            resolved_driver_records.extend(pipeline_result.stored_records)
        gen_step = WorkflowStepResult(
            name="generate_drivers",
            status="completed",
            message=f"Generated {len(unresolved_requirements)} driver(s) required by the project plan.",
            payload={"results": generated_driver_payloads},
        )
    else:
        gen_step = WorkflowStepResult(
            name="generate_drivers",
            status="skipped",
            message="No new drivers were required after reuse resolution.",
        )
    steps.append(gen_step)
    yield _workflow_event("workflow_step_finished", step="generate_drivers", status=gen_step.status, message=gen_step.message, payload=gen_step.payload)

    snapshot_path = ""
    yield _workflow_event("workflow_step_started", step="transaction", message="创建 forge 事务快照")
    try:
        snapshot = BackupManager(project.path).create_snapshot("forge")
        snapshot_path = str(snapshot)
        transaction_step = WorkflowStepResult(
            name="transaction",
            status="completed",
            message="Created a project snapshot before mutating generated files.",
            payload={"snapshot_path": snapshot_path},
        )
    except Exception as exc:
        transaction_step = WorkflowStepResult(
            name="transaction",
            status="failed",
            message=f"Could not create forge transaction snapshot: {exc}",
            payload={},
        )
    steps.append(transaction_step)
    yield _workflow_event("workflow_step_finished", step="transaction", status=transaction_step.status, message=transaction_step.message, payload=transaction_step.payload)
    if transaction_step.status == "failed":
        result = WorkflowRunResult(
            success=False,
            workflow="forge",
            steps=steps,
            summary=transaction_step.message,
            output={"project": project.model_dump(mode="json"), "project_plan": project_plan.model_dump(mode="json")},
        )
        yield _workflow_event("workflow_failed", step="transaction", status="failed", message=result.summary, payload={"result": result.model_dump(mode="json")})
        return

    yield _workflow_event("workflow_step_started", step="assemble", message="组装工程骨架")
    try:
        assemble_result = run_assemble_project(
            project,
            firmware_library_root=str(Path(project_root).resolve() / config.agent.firmware_library),
            driver_library_root=driver_library_root,
            drivers=[item.name for item in resolved_driver_records],
            project_plan=project_plan,
        )
        assemble_step = WorkflowStepResult(
            name="assemble",
            status="completed",
            message="Prepared the project layout and installed resolved drivers.",
            payload=assemble_result,
        )
    except Exception as exc:
        restored_snapshot = _restore_transaction_snapshot(project.path, snapshot_path)
        assemble_step = WorkflowStepResult(
            name="assemble",
            status="failed",
            message=f"Project assembly failed: {exc}",
            payload={"restored_snapshot": restored_snapshot},
        )
        steps.append(assemble_step)
        result = WorkflowRunResult(
            success=False,
            workflow="forge",
            steps=steps,
            summary=assemble_step.message,
            output={
                "project": project.model_dump(mode="json"),
                "project_plan": project_plan.model_dump(mode="json"),
                "document_context": doc_payload,
                "restored_snapshot": restored_snapshot,
            },
        )
        yield _workflow_event("workflow_step_finished", step="assemble", status="failed", message=assemble_step.message, payload=assemble_step.payload)
        yield _workflow_event("workflow_failed", step="assemble", status="failed", message=result.summary, payload={"result": result.model_dump(mode="json")})
        return
    installed_driver_names = [item.name for item in resolved_driver_records]
    steps.append(assemble_step)
    yield _workflow_event("workflow_step_finished", step="assemble", status="completed", message=assemble_step.message, payload=assemble_step.payload)

    yield _workflow_event("workflow_step_started", step="generate_app", message="生成应用层代码")
    app_generator = AppGenerator(config)
    app_result = app_generator.generate_app(
        project=project,
        project_plan=project_plan,
        installed_drivers=installed_driver_names,
    )
    app_step = WorkflowStepResult(
        name="generate_app",
        status="completed" if app_result.success else "failed",
        message="Generated application layer from the structured project plan." if app_result.success else app_result.error,
        payload=app_result.model_dump(mode="json"),
    )
    steps.append(app_step)
    yield _workflow_event("workflow_step_finished", step="generate_app", status=app_step.status, message=app_step.message, payload=app_step.payload)
    if not app_result.success:
        restored_snapshot = _restore_transaction_snapshot(project.path, snapshot_path)
        result = WorkflowRunResult(
            success=False,
            workflow="forge",
            steps=steps,
            summary=app_result.error or "Failed to generate application layer.",
            output={
                "project": project.model_dump(mode="json"),
                "project_plan": project_plan.model_dump(mode="json"),
                "document_context": doc_payload,
                "app_generation": app_result.model_dump(mode="json"),
                "restored_snapshot": restored_snapshot,
            },
        )
        yield _workflow_event("workflow_failed", step="generate_app", status="failed", message=result.summary, payload={"result": result.model_dump(mode="json")})
        return

    review_engine = ReviewEngine(project.path)
    review_engine.config.review.layers.semantic_review = False
    generated_files = [app_result.header_path, app_result.source_path]
    yield _workflow_event("workflow_step_started", step="review", message="审查生成的应用代码")
    report = review_engine.review_files(generated_files)
    review_step = WorkflowStepResult(
        name="review",
        status="completed" if report.passed else "failed",
        message="Application review passed." if report.passed else "Application review reported issues.",
        payload=report.model_dump(mode="json"),
    )
    steps.append(review_step)
    yield _workflow_event("workflow_step_finished", step="review", status=review_step.status, message=review_step.message, payload=review_step.payload)

    fixed_files: list[str] = []
    fix_iterations = 0
    fixer = CodeFixer(config)
    yield _workflow_event("workflow_step_started", step="fix", message="自动修复审查问题")
    while not report.passed and fix_iterations < config.review.max_fix_iterations:
        target_files: list[str] = []
        for issue in report.issues:
            if issue.severity in {"critical", "error"} and issue.file not in target_files:
                target_files.append(issue.file)
        if not target_files:
            break
        yield _workflow_event(
            "workflow_step_progress",
            step="fix",
            message=(
                f"Fix iteration {fix_iterations + 1}/{config.review.max_fix_iterations}: "
                f"{len(target_files)} file(s) need bounded automatic repair."
            ),
            payload={
                "iteration": fix_iterations + 1,
                "max_iterations": config.review.max_fix_iterations,
                "target_files": target_files,
                "timeout_sec": config.review.fix_timeout_sec,
                "retry_attempts": config.review.fix_retry_attempts,
            },
        )
        for index, file_path in enumerate(target_files, start=1):
            yield _workflow_event(
                "workflow_step_progress",
                step="fix",
                message=f"Attempting bounded fix for {Path(file_path).name} ({index}/{len(target_files)}).",
                payload={"file": file_path, "index": index, "total": len(target_files)},
            )
            scoped_issues = [issue for issue in report.issues if issue.file == file_path]
            critical_count = sum(1 for issue in scoped_issues if issue.severity == "critical")
            error_count = sum(1 for issue in scoped_issues if issue.severity == "error")
            warning_count = sum(1 for issue in scoped_issues if issue.severity == "warning")
            scoped_report = ReviewReport(
                passed=(critical_count == 0 and error_count == 0),
                total_issues=len(scoped_issues),
                critical_count=critical_count,
                error_count=error_count,
                warning_count=warning_count,
                issues=scoped_issues,
                raw_logs=report.raw_logs,
            )
            try:
                fix_result = fixer.fix_file(
                    project_path=project.path,
                    file_path=file_path,
                    review_report=scoped_report,
                    apply_changes=True,
                )
            except Exception as exc:
                fix_result = CodeFixResult(
                    success=False,
                    file_path=file_path,
                    applied=False,
                    error=str(exc)[:500],
                )
            if fix_result.success:
                fixed_files.append(file_path)
            yield _workflow_event(
                "workflow_step_progress",
                step="fix",
                message=(
                    f"Bounded fix {'succeeded' if fix_result.success else 'did not apply'} "
                    f"for {Path(file_path).name}."
                ),
                payload={
                    "file": file_path,
                    "success": fix_result.success,
                    "applied": fix_result.applied,
                    "error": fix_result.error,
                },
            )
        fix_iterations += 1
        report = review_engine.review_files(generated_files)
        if report.passed and fix_iterations < config.review.max_fix_iterations:
            compile_ok, compile_issues = _syntax_check_generated(
                project_path=project.path,
                generated_files=generated_files,
                project_root=project_root,
            )
            if not compile_ok and compile_issues:
                report = ReviewReport(
                    passed=False,
                    total_issues=len(compile_issues),
                    critical_count=0,
                    error_count=len(compile_issues),
                    warning_count=0,
                    issues=compile_issues,
                    raw_logs=report.raw_logs,
                )

    fix_step = WorkflowStepResult(
        name="fix",
        status="completed" if report.passed else ("skipped" if fix_iterations == 0 else "failed"),
        message=(f"Applied {fix_iterations} fix iteration(s)." if fix_iterations else "No automatic fix iteration was needed."),
        payload={
            "fix_iterations": fix_iterations,
            "fixed_files": sorted(set(fixed_files)),
            "review_passed": report.passed,
        },
    )
    steps.append(fix_step)
    yield _workflow_event("workflow_step_finished", step="fix", status=fix_step.status, message=fix_step.message, payload=fix_step.payload)

    # Run a syntax check; if it fails and there are fix iterations remaining,
    # feed the syntax errors back into the fix loop rather than failing outright.
    syntax_fix_attempts = 0
    while syntax_fix_attempts < config.review.max_fix_iterations:
        compile_ok, compile_issues = _syntax_check_generated(
            project_path=project.path,
            generated_files=generated_files,
            project_root=project_root,
        )
        if compile_ok:
            break
        if not compile_issues:
            break
        syntax_fix_attempts += 1
        yield _workflow_event(
            "workflow_step_progress",
            step="fix",
            message=f"Syntax error detected, fix attempt {syntax_fix_attempts}/{config.review.max_fix_iterations}.",
        )
        report = ReviewReport(
            passed=False,
            total_issues=len(compile_issues),
            critical_count=0,
            error_count=len(compile_issues),
            warning_count=0,
            issues=compile_issues,
            raw_logs={},
        )
        for issue in compile_issues:
            file_path = issue.file
            scoped_report = ReviewReport(
                passed=False,
                total_issues=1,
                critical_count=0,
                error_count=1,
                warning_count=0,
                issues=[issue],
                raw_logs={},
            )
            try:
                fix_result = fixer.fix_file(
                    project_path=project.path,
                    file_path=file_path,
                    review_report=scoped_report,
                    apply_changes=True,
                )
            except Exception as exc:
                fix_result = CodeFixResult(
                    success=False,
                    file_path=file_path,
                    applied=False,
                    error=str(exc)[:500],
                )
            if fix_result.success:
                fixed_files.append(file_path)
        if compile_ok:
            break

    if not compile_ok:
        syntax_step = WorkflowStepResult(
            name="syntax_check",
            status="failed",
            message="Generated application syntax check failed after fix attempts.",
            payload={"issues": [item.model_dump(mode="json") for item in compile_issues]},
        )
        steps.append(syntax_step)
        yield _workflow_event("workflow_step_finished", step="syntax_check", status="failed", message=syntax_step.message, payload=syntax_step.payload)
        restored_snapshot = _restore_transaction_snapshot(project.path, snapshot_path)
        result = WorkflowRunResult(
            success=False,
            workflow="forge",
            steps=steps,
            summary="Generated application failed syntax validation before build.",
            output={
                "project": project.model_dump(mode="json"),
                "project_plan": project_plan.model_dump(mode="json"),
                "document_context": doc_payload,
                "syntax_issues": [item.model_dump(mode="json") for item in compile_issues],
                "restored_snapshot": restored_snapshot,
            },
        )
        yield _workflow_event("workflow_failed", step="syntax_check", status="failed", message=result.summary, payload={"result": result.model_dump(mode="json")})
        return

    syntax_step = WorkflowStepResult(
        name="syntax_check",
        status="completed",
        message="Generated application syntax check passed." + (f" (applied {syntax_fix_attempts} syntax fix attempt(s))." if syntax_fix_attempts else ""),
        payload={"issues": []},
    )
    steps.append(syntax_step)
    yield _workflow_event("workflow_step_finished", step="syntax_check", status="completed", message=syntax_step.message, payload=syntax_step.payload)

    build_result = None
    yield _workflow_event("workflow_step_started", step="build", message="构建工程")
    if build:
        build_result = run_build_project(
            project.path,
            config=config,
            project_root=project_root,
            clean=clean,
            skip_review=True,
        )
        build_step = WorkflowStepResult(
            name="build",
            status="completed" if build_result.success else "failed",
            message="Project build completed." if build_result.success else "Project build failed after application generation.",
            payload=build_result.model_dump(mode="json"),
        )
        steps.append(build_step)
        yield _workflow_event("workflow_step_finished", step="build", status=build_step.status, message=build_step.message, payload=build_step.payload)
        if not build_result.success:
            restored_snapshot = _restore_transaction_snapshot(project.path, snapshot_path)
            error_detail = getattr(build_result, "stderr", "") or getattr(build_result, "stdout", "") or ""
            summary = "Project build failed after application generation." + (f" Build output: {error_detail[:500]}" if error_detail else "")
            result = WorkflowRunResult(
                success=False,
                workflow="forge",
                steps=steps,
                summary=summary,
                output={
                    "project": project.model_dump(mode="json"),
                    "project_plan": project_plan.model_dump(mode="json"),
                    "document_context": doc_payload,
                    "build_result": build_result.model_dump(mode="json"),
                    "restored_snapshot": restored_snapshot,
                },
            )
            yield _workflow_event("workflow_failed", step="build", status="failed", message=result.summary, payload={"result": result.model_dump(mode="json")})
            return
    else:
        build_step = WorkflowStepResult(name="build", status="skipped", message="Build was explicitly disabled.")
        steps.append(build_step)
        yield _workflow_event("workflow_step_finished", step="build", status="skipped", message=build_step.message)

    flash_result = None
    yield _workflow_event("workflow_step_started", step="flash", message="烧录固件")
    if no_flash:
        flash_step = WorkflowStepResult(name="flash", status="skipped", message="Flash was explicitly disabled.")
        steps.append(flash_step)
        yield _workflow_event("workflow_step_finished", step="flash", status="skipped", message=flash_step.message)
    elif not build:
        flash_step = WorkflowStepResult(name="flash", status="skipped", message="Flash was skipped because build was disabled.")
        steps.append(flash_step)
        yield _workflow_event("workflow_step_finished", step="flash", status="skipped", message=flash_step.message)
    else:
        flash_result = run_flash_project(
            project.path,
            config=config,
            project_root=project_root,
            probe=probe,
        )
        flash_step = WorkflowStepResult(
            name="flash",
            status="completed" if flash_result.success else "failed",
            message="Project flash completed." if flash_result.success else "Project flash failed.",
            payload=flash_result.model_dump(mode="json"),
        )
        steps.append(flash_step)
        yield _workflow_event("workflow_step_finished", step="flash", status=flash_step.status, message=flash_step.message, payload=flash_step.payload)
        if not flash_result.success:
            result = WorkflowRunResult(
                success=False,
                workflow="forge",
                steps=steps,
                summary="Project flash failed.",
                output={"project": project.model_dump(mode="json"), "project_plan": project_plan.model_dump(mode="json"), "document_context": doc_payload, "flash_result": flash_result.model_dump(mode="json")},
            )
            yield _workflow_event("workflow_failed", step="flash", status="failed", message=result.summary, payload={"result": result.model_dump(mode="json")})
            return

    monitor_result = None
    yield _workflow_event("workflow_step_started", step="monitor", message="监视串口输出")
    if no_monitor:
        monitor_step = WorkflowStepResult(name="monitor", status="skipped", message="Monitor was explicitly disabled.")
        steps.append(monitor_step)
        yield _workflow_event("workflow_step_finished", step="monitor", status="skipped", message=monitor_step.message)
    elif not build:
        monitor_step = WorkflowStepResult(name="monitor", status="skipped", message="Monitor was skipped because build was disabled.")
        steps.append(monitor_step)
        yield _workflow_event("workflow_step_finished", step="monitor", status="skipped", message=monitor_step.message)
    else:
        monitor_result = run_monitor_project(
            project.path,
            port=port,
            baudrate=baudrate or config.monitor.default_baudrate,
        )
        monitor_step = WorkflowStepResult(
            name="monitor",
            status="completed" if monitor_result.success else "failed",
            message="UART monitor captured output." if monitor_result.success else monitor_result.error,
            payload=monitor_result.model_dump(mode="json"),
        )
        steps.append(monitor_step)
        yield _workflow_event("workflow_step_finished", step="monitor", status=monitor_step.status, message=monitor_step.message, payload=monitor_step.payload)

    result = WorkflowRunResult(
        success=True,
        workflow="forge",
        steps=steps,
        summary="Natural-language project assembly completed successfully.",
        output={
            "project": project.model_dump(mode="json"),
            "project_plan": project_plan.model_dump(mode="json"),
            "document_context": doc_payload,
            "resolved_drivers": [item.model_dump(mode="json") for item in resolved_driver_records],
            "generated_driver_records": [item.model_dump(mode="json") for item in generated_driver_records],
            "app_generation": app_result.model_dump(mode="json"),
            "review_report": report.model_dump(mode="json"),
            "build_result": build_result.model_dump(mode="json") if build_result else {},
            "flash_result": flash_result.model_dump(mode="json") if flash_result else {},
            "monitor_result": monitor_result.model_dump(mode="json") if monitor_result else {},
        },
    )
    yield _workflow_event("workflow_finished", status="completed", message=result.summary, payload={"result": result.model_dump(mode="json")})


def _merge_manual_driver_overrides(
    *,
    planned: list[DriverRequirement],
    manual_queries: list[str],
) -> list[DriverRequirement]:
    merged = list(planned)
    existing_keys = {
        (
            item.chip.strip().lower(),
            item.interface.strip().upper(),
            item.vendor.strip().lower(),
            item.device.strip().lower(),
        )
        for item in planned
    }
    for query in manual_queries:
        normalized = query.strip()
        if not normalized:
            continue
        override = _parse_manual_driver_override(normalized)
        key = (
            override.chip.strip().lower(),
            override.interface.strip().upper(),
            override.vendor.strip().lower(),
            override.device.strip().lower(),
        )
        if key not in existing_keys:
            existing_keys.add(key)
            merged.append(override)
    return merged


def _driver_protocol_summary(plan: ProjectPlan, requirement: DriverRequirement) -> str:
    lines = [
        f"Project requirement summary: {plan.requirement_summary}",
        f"Application behavior summary: {plan.app_behavior_summary}",
        f"Driver target: {requirement.device or requirement.chip} over {requirement.interface}",
    ]
    if plan.features:
        lines.append("Planned features: " + "; ".join(plan.features))
    if plan.peripheral_hints:
        lines.append("Peripheral hints: " + "; ".join(plan.peripheral_hints))
    if plan.cubemx_or_firmware_actions:
        lines.append("Configuration actions: " + "; ".join(plan.cubemx_or_firmware_actions))
    if plan.document_context_summary:
        lines.append("Document context: " + plan.document_context_summary)
    return "\n".join(lines)


def _parse_manual_driver_override(query: str) -> DriverRequirement:
    normalized = query.strip()
    vendor = ""
    interface = "SPI"
    chip = normalized
    device = normalized.lower()

    if "@" in normalized:
        left, right = normalized.split("@", 1)
        chip = left.strip() or chip
        interface = right.strip().upper() or interface
    elif ":" in normalized:
        left, right = normalized.split(":", 1)
        left_upper = left.strip().upper()
        right_upper = right.strip().upper()
        if left_upper in {"SPI", "I2C", "UART"}:
            interface = left_upper
            chip = right.strip() or chip
        elif right_upper in {"SPI", "I2C", "UART"}:
            chip = left.strip() or chip
            interface = right_upper
        else:
            vendor = left.strip().lower()
            chip = right.strip() or chip

    if "/" in chip:
        vendor_part, chip_part = chip.split("/", 1)
        vendor = vendor or vendor_part.strip().lower()
        chip = chip_part.strip() or chip

    device = chip.strip().lower()
    return DriverRequirement(
        chip=chip.strip(),
        interface=interface,
        vendor=vendor,
        device=device,
        confidence=0.4,
        rationale="Manual driver override from forge CLI.",
    )


def _prepare_document_context(
    *,
    driver_library_root: str,
    docs: list[str],
    query: str,
) -> tuple[EngineeringContext, dict]:
    if not docs:
        empty = EngineeringContext(
            source_documents=[],
            document_summary="",
            pin_requirements=[],
            bus_requirements=[],
            protocol_frames=[],
            register_hints=[],
            bringup_sequence=[],
            timing_constraints=[],
            integration_notes=[],
            risk_notes=[],
            raw_matches=[],
            parse_errors=[],
        )
        return empty, {"docs": [], "query": query, "summary": "", "matches": [], "engineering_context": empty.model_dump(mode="json")}

    analyzer = DocumentEngineeringAnalyzer(Path(driver_library_root).resolve() / "knowledge_base")
    context = analyzer.analyze(docs=docs, query=query)
    payload = {
        "docs": context.source_documents,
        "query": query.strip(),
        "summary": context.document_summary,
        "matches": [chunk.model_dump(mode="json") for chunk in context.raw_matches],
        "engineering_context": context.model_dump(mode="json"),
    }
    return context, payload
