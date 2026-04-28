from __future__ import annotations

from pathlib import Path

from luxar.core.code_fixer import CodeFixer
from luxar.core.config_manager import AgentConfig
from luxar.core.driver_generator import DriverGenerator
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
        self.generator = DriverGenerator(config, project_root=self.project_root)
        self.fixer = CodeFixer(config)
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
        generation_result = self.generator.generate_driver(
            chip=chip,
            interface=interface,
            protocol_summary=protocol_summary,
            register_summary=register_summary,
            output_dir=str(resolved_output),
            vendor=vendor,
            device=device,
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
                fix_result = self.fixer.fix_file(
                    project_path=str(resolved_output),
                    file_path=file_path,
                    review_report=scoped_report,
                    apply_changes=True,
                )
                if fix_result.success:
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
            / interface.lower()
            / (vendor.strip().lower() or "generic")
            / ((device.strip() or chip.strip()).lower())
        ).resolve()

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

