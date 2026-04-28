from __future__ import annotations

from pathlib import Path

from luxar.core.app_generator import AppGenerator
from luxar.core.asset_reuse import AssetReuseAdvisor
from luxar.core.code_fixer import CodeFixer
from luxar.core.config_manager import AgentConfig
from luxar.core.document_engineering import DocumentEngineeringAnalyzer
from luxar.core.driver_library import DriverLibrary
from luxar.core.driver_pipeline import DriverPipeline
from luxar.core.project_planner import ProjectPlanner
from luxar.core.review_engine import ReviewEngine
from luxar.models.schemas import (
    DriverMetadata,
    DriverRequirement,
    EngineeringContext,
    ProjectConfig,
    ProjectPlan,
    ReviewReport,
    WorkflowRunResult,
    WorkflowStepResult,
)
from luxar.tools.assemble_project import run_assemble_project
from luxar.tools.build_project import run_build_project
from luxar.tools.flash_project import run_flash_project
from luxar.tools.monitor_project import run_monitor_project


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
    steps: list[WorkflowStepResult] = []
    planner = ProjectPlanner(config)
    engineering_context, doc_payload = _prepare_document_context(
        driver_library_root=driver_library_root,
        docs=docs or [],
        query=doc_query or requirement,
    )
    if docs:
        steps.append(
            WorkflowStepResult(
                name="parse_docs",
                status="completed",
                message=f"Parsed {len(docs)} document(s) and loaded forge document context.",
                payload=doc_payload,
            )
        )
    project_plan = planner.build_plan(
        project=project,
        requirement=requirement,
        document_context=engineering_context.document_summary,
        engineering_context=engineering_context,
    )
    steps.append(
        WorkflowStepResult(
            name="plan",
            status="completed",
            message="Derived a structured project plan from the natural-language requirement.",
            payload=project_plan.model_dump(mode="json"),
        )
    )

    planned_driver_requirements = _merge_manual_driver_overrides(
        planned=project_plan.needed_drivers,
        manual_queries=drivers or [],
    )
    if plan_only:
        steps.append(
            WorkflowStepResult(
                name="resolve_drivers",
                status="skipped",
                message="Plan-only mode skipped driver resolution and project assembly.",
                payload={"planned_drivers": [item.model_dump(mode="json") for item in planned_driver_requirements]},
            )
        )
        return WorkflowRunResult(
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

    steps.append(
        WorkflowStepResult(
            name="resolve_drivers",
            status="completed",
            message="Resolved the driver requirements from project planning.",
            payload={
                "planned_drivers": [item.model_dump(mode="json") for item in planned_driver_requirements],
                "reused_count": len(reused_driver_payloads),
                "generate_count": len(unresolved_requirements),
            },
        )
    )

    steps.append(
        WorkflowStepResult(
            name="reuse_drivers",
            status="completed" if reused_driver_payloads else "skipped",
            message=(
                f"Reused {len(reused_driver_payloads)} reviewed driver(s) from the local library."
                if reused_driver_payloads
                else "No reviewed local drivers matched the plan strongly enough to reuse."
            ),
            payload={"drivers": reused_driver_payloads},
        )
    )

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
                steps.append(
                    WorkflowStepResult(
                        name="generate_drivers",
                        status="failed",
                        message=f"Failed to generate driver for {item.device or item.chip}.",
                        payload={"result": pipeline_result.model_dump(mode="json")},
                    )
                )
                return WorkflowRunResult(
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
            generated_driver_records.extend(pipeline_result.stored_records)
            resolved_driver_records.extend(pipeline_result.stored_records)
        steps.append(
            WorkflowStepResult(
                name="generate_drivers",
                status="completed",
                message=f"Generated {len(unresolved_requirements)} driver(s) required by the project plan.",
                payload={"results": generated_driver_payloads},
            )
        )
    else:
        steps.append(
            WorkflowStepResult(
                name="generate_drivers",
                status="skipped",
                message="No new drivers were required after reuse resolution.",
            )
        )

    assemble_result = run_assemble_project(
        project,
        firmware_library_root=str(Path(project_root).resolve() / config.agent.firmware_library),
        driver_library_root=driver_library_root,
        drivers=[item.name for item in resolved_driver_records],
    )
    installed_driver_names = [item.name for item in resolved_driver_records]
    steps.append(
        WorkflowStepResult(
            name="assemble",
            status="completed",
            message="Prepared the project layout and installed resolved drivers.",
            payload=assemble_result,
        )
    )

    app_generator = AppGenerator(config)
    app_result = app_generator.generate_app(
        project=project,
        project_plan=project_plan,
        installed_drivers=installed_driver_names,
    )
    steps.append(
        WorkflowStepResult(
            name="generate_app",
            status="completed" if app_result.success else "failed",
            message="Generated application layer from the structured project plan." if app_result.success else app_result.error,
            payload=app_result.model_dump(mode="json"),
        )
    )
    if not app_result.success:
        return WorkflowRunResult(
            success=False,
            workflow="forge",
            steps=steps,
            summary=app_result.error or "Failed to generate application layer.",
            output={"project": project.model_dump(mode="json"), "project_plan": project_plan.model_dump(mode="json"), "document_context": doc_payload, "app_generation": app_result.model_dump(mode="json")},
        )

    review_engine = ReviewEngine(project.path)
    generated_files = [app_result.header_path, app_result.source_path]
    report = review_engine.review_files(generated_files)
    steps.append(
        WorkflowStepResult(
            name="review",
            status="completed" if report.passed else "failed",
            message="Application review passed." if report.passed else "Application review reported issues.",
            payload=report.model_dump(mode="json"),
        )
    )

    fixed_files: list[str] = []
    fix_iterations = 0
    fixer = CodeFixer(config)
    while not report.passed and fix_iterations < config.review.max_fix_iterations:
        target_files: list[str] = []
        for issue in report.issues:
            if issue.severity in {"critical", "error"} and issue.file not in target_files:
                target_files.append(issue.file)
        if not target_files:
            break
        for file_path in target_files:
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
            fix_result = fixer.fix_file(
                project_path=project.path,
                file_path=file_path,
                review_report=scoped_report,
                apply_changes=True,
            )
            if fix_result.success:
                fixed_files.append(file_path)
        fix_iterations += 1
        report = review_engine.review_files(generated_files)

    steps.append(
        WorkflowStepResult(
            name="fix",
            status="completed" if report.passed else ("skipped" if fix_iterations == 0 else "failed"),
            message=(
                f"Applied {fix_iterations} fix iteration(s)."
                if fix_iterations
                else "No automatic fix iteration was needed."
            ),
            payload={
                "fix_iterations": fix_iterations,
                "fixed_files": sorted(set(fixed_files)),
                "review_passed": report.passed,
            },
        )
    )
    if not report.passed:
        return WorkflowRunResult(
            success=False,
            workflow="forge",
            steps=steps,
            summary="Application generation did not pass review.",
            output={"project": project.model_dump(mode="json"), "project_plan": project_plan.model_dump(mode="json"), "document_context": doc_payload, "review_report": report.model_dump(mode="json")},
        )

    build_result = None
    if build:
        build_result = run_build_project(
            project.path,
            config=config,
            project_root=project_root,
            clean=clean,
            skip_review=True,
        )
        steps.append(
            WorkflowStepResult(
                name="build",
                status="completed" if build_result.success else "failed",
                message="Project build completed." if build_result.success else "Project build failed after application generation.",
                payload=build_result.model_dump(mode="json"),
            )
        )
        if not build_result.success:
            error_detail = getattr(build_result, 'stderr', '') or getattr(build_result, 'stdout', '') or ''
            summary = "Project build failed after application generation." + (f" Build output: {error_detail[:500]}" if error_detail else "")
            return WorkflowRunResult(
                success=False,
                workflow="forge",
                steps=steps,
                summary=summary,
                output={"project": project.model_dump(mode="json"), "project_plan": project_plan.model_dump(mode="json"), "document_context": doc_payload, "build_result": build_result.model_dump(mode="json")},
            )
    else:
        steps.append(
            WorkflowStepResult(
                name="build",
                status="skipped",
                message="Build was explicitly disabled.",
            )
        )

    flash_result = None
    if no_flash:
        steps.append(
            WorkflowStepResult(
                name="flash",
                status="skipped",
                message="Flash was explicitly disabled.",
            )
        )
    elif not build:
        steps.append(
            WorkflowStepResult(
                name="flash",
                status="skipped",
                message="Flash was skipped because build was disabled.",
            )
        )
    elif probe:
        flash_result = run_flash_project(
            project.path,
            config=config,
            project_root=project_root,
            probe=probe,
        )
        steps.append(
            WorkflowStepResult(
                name="flash",
                status="completed" if flash_result.success else "failed",
                message="Project flash completed." if flash_result.success else "Project flash failed.",
                payload=flash_result.model_dump(mode="json"),
            )
        )
        if not flash_result.success:
            return WorkflowRunResult(
                success=False,
                workflow="forge",
                steps=steps,
                summary="Project flash failed.",
                output={"project": project.model_dump(mode="json"), "project_plan": project_plan.model_dump(mode="json"), "document_context": doc_payload, "flash_result": flash_result.model_dump(mode="json")},
            )
    else:
        steps.append(
            WorkflowStepResult(
                name="flash",
                status="skipped",
                message="No probe was provided, so flash was skipped gracefully.",
            )
        )

    monitor_result = None
    if no_monitor:
        steps.append(
            WorkflowStepResult(
                name="monitor",
                status="skipped",
                message="Monitor was explicitly disabled.",
            )
        )
    elif not build:
        steps.append(
            WorkflowStepResult(
                name="monitor",
                status="skipped",
                message="Monitor was skipped because build was disabled.",
            )
        )
    elif port:
        monitor_result = run_monitor_project(
            project.path,
            port=port,
            baudrate=baudrate or config.monitor.default_baudrate,
        )
        steps.append(
            WorkflowStepResult(
                name="monitor",
                status="completed" if monitor_result.success else "failed",
                message="UART monitor captured output." if monitor_result.success else monitor_result.error,
                payload=monitor_result.model_dump(mode="json"),
            )
        )
    else:
        steps.append(
            WorkflowStepResult(
                name="monitor",
                status="skipped",
                message="No serial port was provided, so monitor was skipped gracefully.",
            )
        )

    return WorkflowRunResult(
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
        analyzer = DocumentEngineeringAnalyzer(Path(driver_library_root).resolve() / "knowledge_base")
        empty = analyzer.analyze(docs=[], query=query)
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
