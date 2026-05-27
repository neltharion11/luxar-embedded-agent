from __future__ import annotations

import re
from pathlib import Path

from luxar.agent.workers.repair import RepairWorker
from luxar.core.config_manager import AgentConfig
from luxar.agent.workers.generator import GeneratorWorker
from luxar.core.asset_reuse import AssetReuseAdvisor
from luxar.core.driver_library import DriverLibrary
from luxar.core.review_engine import ReviewEngine
from luxar.models.schemas import (
    DriverGenerationResult,
    DriverMetadata,
    DriverPipelineResult,
    ReviewIssue,
    ReviewReport,
)


class DriverPipeline:
    def __init__(self, config: AgentConfig, project_root: str):
        self.config = config
        self.project_root = Path(project_root).resolve()
        self.generator = GeneratorWorker(config)
        self.reuse_advisor = AssetReuseAdvisor(
            project_root=self.project_root,
            driver_library_root=self.project_root / self.config.agent.driver_library,
            skill_library_root=self.project_root / self.config.agent.skills_root,
            legacy_skill_library_root=self.project_root / self.config.agent.skill_library,
        )
        self.fixer = RepairWorker(config)
        self.driver_library = DriverLibrary(self.project_root / self.config.agent.driver_library)

    def generate_review_fix(
        self,
        chip: str,
        interface: str,
        protocol_summary: str,
        register_summary: str = "",
        vendor: str = "",
        device: str = "",
        output_dir: str = "",
        max_fix_iterations: int | None = None,
    ) -> DriverPipelineResult:
        resolved_output = self._resolve_output_dir(
            interface=interface,
            chip=chip,
            vendor=vendor,
            device=device,
            output_dir=output_dir,
        )
        chip_name = chip.strip()
        interface_name = interface.strip().upper()
        reuse_context = self.reuse_advisor.build_context(
            chip=chip_name,
            interface=interface_name,
            vendor=vendor,
            device=device,
            register_summary=register_summary,
        )
        reuse_candidate_payload = reuse_context.get("reuse_candidate")
        reuse_candidate = reuse_candidate_payload if isinstance(reuse_candidate_payload, dict) else None
        confidence = reuse_context.get("confidence", 0.0)
        stem = self._resolve_stem(chip_name, vendor=vendor, device=device)

        if reuse_candidate:
            reused_result = self._reuse_existing_driver(
                chip=chip_name,
                interface=interface_name,
                output_dir=str(resolved_output),
                reuse_context=reuse_context,
                vendor=vendor,
                device=device,
                stem=stem,
            )
            if reused_result is not None:
                generation_result = reused_result

        if not reuse_candidate or 'generation_result' not in locals():
            if confidence < 0.5:
                reuse_qualifier = "Low confidence reuse candidate found, but falling back to generation."
            else:
                reuse_qualifier = f"Reuse candidate available with confidence {confidence:.2f}, but was not materialized."
            
            enriched_reuse_summary = (
                f"{reuse_context['summary']}\n\nconfidence: {confidence:.2f}\nreuse_qualifier: {reuse_qualifier}"
                if reuse_context.get("summary")
                else f"confidence: {confidence:.2f}\nreuse_qualifier: {reuse_qualifier}"
            )
            
            context_data = {
                "chip_name": chip_name,
                "interface": interface_name,
                "protocol_summary": protocol_summary.strip(),
                "register_summary": register_summary.strip(),
                "reuse_context": enriched_reuse_summary or "No relevant local assets were found.",
            }
            
            skill_path = self.project_root / "workspace" / "skills" / "workflows" / "generate_c_driver" / "SKILL.md"
            skill_instructions = skill_path.read_text(encoding="utf-8") if skill_path.exists() else "Generate C Driver header and source."
            
            gen_out = self.generator.generate_files(
                context_data=context_data,
                skill_instructions=skill_instructions,
                output_dir=str(resolved_output),
                stem=stem,
            )
            
            generation_result = DriverGenerationResult(
                success=gen_out.get("success", False),
                chip=chip_name,
                interface=interface_name,
                output_dir=str(resolved_output),
                header_path=gen_out.get("header_path", ""),
                source_path=gen_out.get("source_path", ""),
                reuse_summary=reuse_context.get("summary", ""),
                reuse_sources=reuse_context.get("sources", []),
                error=gen_out.get("error", ""),
                raw_response=gen_out.get("raw_response", ""),
            )

        if not generation_result.success:
            return DriverPipelineResult(
                success=False,
                chip=chip,
                interface=interface,
                generated_files=[],
                generation_result=generation_result,
                error=generation_result.error or "Driver generation failed.",
            )

        generated_files = [
            generation_result.header_path,
            generation_result.source_path,
        ]
        review_engine = ReviewEngine(str(resolved_output))
        report = review_engine.review_files(generated_files)
        limit = max_fix_iterations if max_fix_iterations is not None else self.config.review.max_fix_iterations
        fix_iterations = 0
        fixed_files: list[str] = []

        while not report.passed and fix_iterations < limit:
            target_files = self._files_needing_fix(report)
            if not target_files:
                break
            for file_path in target_files:
                scoped_report = self._report_for_file(report, file_path)
                import json
                report_json = json.dumps({
                    "passed": False,
                    "total_issues": scoped_report.total_issues,
                    "critical_count": scoped_report.critical_count,
                    "error_count": scoped_report.error_count,
                    "warning_count": scoped_report.warning_count,
                    "issues": [issue.model_dump(mode="json") if hasattr(issue, "model_dump") else dict(issue) for issue in scoped_report.issues],
                    "raw_logs": {},
                }, ensure_ascii=False)
                
                fix_result = self.fixer.repair_file(
                    file_path=file_path,
                    context_report=report_json,
                    skill_instructions="Fix the EMB review issues in the generated driver. Provide the complete source file.",
                    apply_changes=True,
                )
                if fix_result.get("success"):
                    fixed_files.append(file_path)
            fix_iterations += 1
            report = review_engine.review_files(generated_files)

        success = generation_result.success and report.passed
        error = ""
        stored_records: list[DriverMetadata] = []
        if not success:
            if fix_iterations >= limit and not report.passed:
                error = "Driver pipeline reached the maximum fix iterations without passing review."
            else:
                error = "Driver pipeline did not pass review."
        else:
            stored_records = self._store_generated_driver(
                chip=chip,
                interface=interface,
                vendor=vendor,
                device=device,
                source_doc=protocol_summary,
                generation_result=generation_result,
                review_report=report,
            )

        return DriverPipelineResult(
            success=success,
            chip=chip,
            interface=interface,
            generated_files=generated_files,
            generation_result=generation_result,
            review_report=report,
            fix_iterations=fix_iterations,
            fixed_files=sorted(set(fixed_files)),
            stored=bool(stored_records),
            stored_records=stored_records,
            error=error,
        )

    def _build_review_engine(self, project_path: str) -> ReviewEngine:
        return ReviewEngine(project_path)

    def _generation_result_from_state(self, payload: dict) -> DriverGenerationResult | None:
        if not payload:
            return None
        return DriverGenerationResult.model_validate(payload)

    def _resolve_output_dir(
        self,
        interface: str,
        chip: str,
        vendor: str = "",
        device: str = "",
        output_dir: str = "",
    ) -> Path:
        if output_dir:
            resolved = Path(output_dir)
            if not resolved.is_absolute():
                resolved = self.project_root / resolved
            return resolved.resolve()
        return (
            self.project_root
            / self.config.agent.driver_library
            / "generated"
            / self._safe_path_component(interface, fallback="generic")
            / self._safe_path_component(vendor, fallback="generic")
            / self._safe_path_component(device or chip, fallback="generated_driver")
        ).resolve()

    def _safe_path_component(self, value: str, fallback: str) -> str:
        text = re.sub(r"[\\/:*?\"<>|]+", " ", (value or "").strip().lower())
        text = re.sub(r"\s+", " ", text).strip(" .")
        return text or fallback

    def _files_needing_fix(self, report: ReviewReport) -> list[str]:
        target_files: list[str] = []
        for issue in report.issues:
            if issue.severity not in {"critical", "error"}:
                continue
            if issue.file not in target_files:
                target_files.append(issue.file)
        return target_files

    def _report_for_file(self, report: ReviewReport, file_path: str) -> ReviewReport:
        issues = [issue for issue in report.issues if Path(issue.file).resolve() == Path(file_path).resolve()]
        critical_count = sum(1 for issue in issues if issue.severity == "critical")
        error_count = sum(1 for issue in issues if issue.severity == "error")
        warning_count = sum(1 for issue in issues if issue.severity == "warning")
        return ReviewReport(
            passed=(critical_count == 0 and error_count == 0),
            total_issues=len(issues),
            critical_count=critical_count,
            error_count=error_count,
            warning_count=warning_count,
            issues=[ReviewIssue.model_validate(issue.model_dump(mode="json")) for issue in issues],
            raw_logs=report.raw_logs,
        )

    def _store_generated_driver(
        self,
        chip: str,
        interface: str,
        vendor: str,
        device: str,
        source_doc: str,
        generation_result,
        review_report: ReviewReport,
    ) -> list[DriverMetadata]:
        base_name = Path(generation_result.source_path).stem
        issue_count = len(review_report.issues)
        kb_score = generation_result.raw_response.count("kb:") * 0.1 if generation_result.raw_response else 0.0
        stored_records = [
            self.driver_library.store_driver(
                DriverMetadata(
                    name=base_name,
                    protocol=interface.upper(),
                    chip=chip.strip(),
                    vendor=vendor.strip().lower(),
                    device=device.strip().lower() or chip.strip().lower(),
                    path=generation_result.source_path,
                    header_path=generation_result.header_path,
                    source_path=generation_result.source_path,
                    review_passed=review_report.passed,
                    source_doc=source_doc,
                    review_issue_count=issue_count,
                    kb_score=kb_score,
                )
            )
        ]
        return stored_records

    def _resolve_stem(self, chip: str, vendor: str = "", device: str = "") -> str:
        base = device.strip() or chip.strip() or vendor.strip() or "generated_driver"
        stem = re.sub(r"[^A-Za-z0-9_]+", "_", base).strip("_").lower()
        return stem or "generated_driver"

    def _metadata_from_payload(self, payload: dict):
        from luxar.models.schemas import DriverMetadata
        return DriverMetadata.model_validate(payload)

    def _reuse_existing_driver(
        self,
        chip: str,
        interface: str,
        output_dir: str,
        reuse_context: dict,
        vendor: str = "",
        device: str = "",
        stem: str = "",
    ) -> DriverGenerationResult | None:
        reuse_candidate_payload = reuse_context.get("reuse_candidate")
        if not isinstance(reuse_candidate_payload, dict):
            return None
        candidate_path = str(reuse_candidate_payload.get("path", ""))
        resolved_output = Path(output_dir).resolve()
        stem = stem or self._resolve_stem(chip, vendor=vendor, device=device)
        try:
            header_path, source_path = self.reuse_advisor.materialize_reused_driver(
                candidate=self._metadata_from_payload(reuse_candidate_payload),
                output_dir=resolved_output,
                target_stem=stem,
            )
            self.reuse_advisor.driver_library.record_reuse(candidate_path)
        except FileNotFoundError:
            return None
        return DriverGenerationResult(
            success=True,
            chip=chip.strip(),
            interface=interface.strip().upper(),
            output_dir=str(resolved_output),
            header_path=str(header_path),
            source_path=str(source_path),
            reused_existing=True,
            reused_driver_path=candidate_path,
            reuse_summary=str(reuse_context.get("summary", "")),
            reuse_sources=list(reuse_context.get("sources", [])),
            raw_response=f"Reused existing reviewed driver from {candidate_path}",
        )
