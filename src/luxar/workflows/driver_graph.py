from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from luxar.core.driver_pipeline import DriverPipeline
from luxar.models.schemas import DriverPipelineResult, DriverMetadata, ReviewReport

try:
    from langgraph.graph import END, StateGraph

    LANGGRAPH_AVAILABLE = True
except Exception:
    END = "__end__"
    StateGraph = None
    LANGGRAPH_AVAILABLE = False


class DriverWorkflowState(TypedDict, total=False):
    chip: str
    interface: str
    protocol_summary: str
    register_summary: str
    vendor: str
    device: str
    output_dir: str
    generated_files: list[str]
    review_passed: bool
    review_report: ReviewReport | None
    fix_iterations: int
    max_fix_iterations: int
    fixed_files: list[str]
    stored: bool
    stored_records: list[DriverMetadata]
    reuse_context: dict
    reuse_decision: str
    generation_result: dict
    error: str
    success: bool


class LangGraphDriverWorkflow:
    def __init__(self, pipeline: DriverPipeline):
        self.pipeline = pipeline
        self.generator = pipeline.generator
        self.fixer = pipeline.fixer
        self.driver_library = pipeline.driver_library

    def run(
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
        if not LANGGRAPH_AVAILABLE:
            return self.pipeline.generate_review_fix(
                chip=chip,
                interface=interface,
                protocol_summary=protocol_summary,
                register_summary=register_summary,
                vendor=vendor,
                device=device,
                output_dir=output_dir,
                max_fix_iterations=max_fix_iterations,
            )

        resolved_output = self.pipeline._resolve_output_dir(
            interface=interface,
            chip=chip,
            vendor=vendor,
            device=device,
            output_dir=output_dir,
        )
        workflow = self._build_graph()
        initial_state: DriverWorkflowState = {
            "chip": chip,
            "interface": interface,
            "protocol_summary": protocol_summary,
            "register_summary": register_summary,
            "vendor": vendor,
            "device": device,
            "output_dir": str(resolved_output),
            "generated_files": [],
            "review_passed": False,
            "review_report": None,
            "fix_iterations": 0,
            "max_fix_iterations": (
                max_fix_iterations if max_fix_iterations is not None else self.pipeline.config.review.max_fix_iterations
            ),
            "fixed_files": [],
            "stored": False,
            "stored_records": [],
            "generation_result": {},
            "error": "",
            "success": False,
        }
        final_state = workflow.invoke(initial_state)
        return self._state_to_result(final_state)

    def _build_graph(self):
        graph = StateGraph(DriverWorkflowState)
        graph.add_node("retrieve", self._retrieve_node)
        graph.add_node("decide", self._decide_node)
        graph.add_node("reuse", self._reuse_node)
        graph.add_node("generate", self._generate_node)
        graph.add_node("review", self._review_node)
        graph.add_node("fix", self._fix_node)
        graph.add_node("store", self._store_node)
        graph.set_entry_point("retrieve")
        graph.add_edge("retrieve", "decide")
        graph.add_conditional_edges(
            "decide",
            self._after_decide,
            {
                "reuse": "reuse",
                "generate": "generate",
            },
        )
        graph.add_edge("reuse", "review")
        graph.add_conditional_edges(
            "generate",
            self._after_generate,
            {
                "review": "review",
                "end": END,
            },
        )
        graph.add_conditional_edges(
            "review",
            self._after_review,
            {
                "fix": "fix",
                "store": "store",
                "end": END,
            },
        )
        graph.add_edge("fix", "review")
        graph.add_edge("store", END)
        return graph.compile()

    def _retrieve_node(self, state: DriverWorkflowState) -> DriverWorkflowState:
        reuse_context = self.generator.reuse_advisor.build_context(
            chip=state["chip"],
            interface=state["interface"],
            vendor=state["vendor"],
            device=state["device"],
            register_summary=state["register_summary"],
        )
        return {"reuse_context": reuse_context, "success": True}

    def _decide_node(self, state: DriverWorkflowState) -> DriverWorkflowState:
        reuse_context = state.get("reuse_context", {})
        decision = "reuse" if reuse_context.get("reuse_candidate") else "generate"
        return {"reuse_decision": decision, "success": True}

    def _reuse_node(self, state: DriverWorkflowState) -> DriverWorkflowState:
        result = self.generator.reuse_existing_driver(
            chip=state["chip"],
            interface=state["interface"],
            output_dir=state["output_dir"],
            reuse_context=state.get("reuse_context", {}),
            vendor=state["vendor"],
            device=state["device"],
        )
        if result is None:
            return {"error": "Reuse was selected but no reusable driver could be materialized.", "success": False}
        return {
            "generation_result": result.model_dump(mode="json"),
            "generated_files": [result.header_path, result.source_path],
            "error": "",
            "success": True,
        }

    def _generate_node(self, state: DriverWorkflowState) -> DriverWorkflowState:
        result = self.generator.generate_driver(
            chip=state["chip"],
            interface=state["interface"],
            protocol_summary=state["protocol_summary"],
            register_summary=state["register_summary"],
            output_dir=state["output_dir"],
            vendor=state["vendor"],
            device=state["device"],
            allow_reuse=False,
            reuse_context=state.get("reuse_context", {}),
        )
        generated_files = []
        if result.success:
            generated_files = [result.header_path, result.source_path]
        return {
            "generation_result": result.model_dump(mode="json"),
            "generated_files": generated_files,
            "error": result.error,
            "success": bool(result.success),
        }

    def _review_node(self, state: DriverWorkflowState) -> DriverWorkflowState:
        review_engine = self.pipeline._build_review_engine(state["output_dir"])
        report = review_engine.review_files(state.get("generated_files", []))
        return {
            "review_report": report,
            "review_passed": report.passed,
            "success": report.passed,
        }

    def _fix_node(self, state: DriverWorkflowState) -> DriverWorkflowState:
        report = state.get("review_report")
        if report is None:
            return {"error": "Fix node received no review report.", "success": False}

        fixed_files = list(state.get("fixed_files", []))
        target_files = self.pipeline._files_needing_fix(report)
        if not target_files:
            return {"error": "No files required fixing, but review did not pass.", "success": False}

        for file_path in target_files:
            scoped_report = self.pipeline._report_for_file(report, file_path)
            fix_result = self.fixer.fix_file(
                project_path=state["output_dir"],
                file_path=file_path,
                review_report=scoped_report,
                apply_changes=True,
            )
            if fix_result.success and file_path not in fixed_files:
                fixed_files.append(file_path)
        return {
            "fixed_files": fixed_files,
            "fix_iterations": state.get("fix_iterations", 0) + 1,
            "success": True,
        }

    def _store_node(self, state: DriverWorkflowState) -> DriverWorkflowState:
        generation_result = state.get("generation_result", {})
        review_report = state.get("review_report")
        if not generation_result or review_report is None:
            return {"stored": False, "success": False, "error": "Store node missing generation or review data."}

        stored_records = self.pipeline._store_generated_driver(
            chip=state["chip"],
            interface=state["interface"],
            vendor=state["vendor"],
            device=state["device"],
            source_doc=state["protocol_summary"],
            generation_result=self.pipeline._generation_result_from_state(generation_result),
            review_report=review_report,
        )
        return {
            "stored": bool(stored_records),
            "stored_records": stored_records,
            "success": bool(stored_records),
        }

    def _after_generate(self, state: DriverWorkflowState) -> str:
        return "review" if state.get("generated_files") else "end"

    def _after_decide(self, state: DriverWorkflowState) -> str:
        return "reuse" if state.get("reuse_decision") == "reuse" else "generate"

    def _after_review(self, state: DriverWorkflowState) -> str:
        report = state.get("review_report")
        if report is None:
            return "end"
        if report.passed:
            return "store"
        if state.get("fix_iterations", 0) >= state.get("max_fix_iterations", 0):
            return "end"
        return "fix"

    def _state_to_result(self, state: DriverWorkflowState) -> DriverPipelineResult:
        generation_result = self.pipeline._generation_result_from_state(state.get("generation_result", {}))
        report = state.get("review_report")
        success = bool(state.get("stored")) and bool(report and report.passed) and bool(generation_result and generation_result.success)
        error = state.get("error", "")
        if not success and not error:
            if report is not None and not report.passed and state.get("fix_iterations", 0) >= state.get("max_fix_iterations", 0):
                error = "Driver workflow reached the maximum fix iterations without passing review."
            elif report is not None and not report.passed:
                error = "Driver workflow did not pass review."
            elif generation_result is not None and not generation_result.success:
                error = generation_result.error or "Driver generation failed."
            else:
                error = "Driver workflow did not complete successfully."
        return DriverPipelineResult(
            success=success,
            chip=state.get("chip", ""),
            interface=state.get("interface", ""),
            generated_files=list(state.get("generated_files", [])),
            generation_result=generation_result,
            review_report=report,
            fix_iterations=state.get("fix_iterations", 0),
            fixed_files=sorted(set(state.get("fixed_files", []))),
            stored=bool(state.get("stored", False)),
            stored_records=list(state.get("stored_records", [])),
            error=error,
        )

