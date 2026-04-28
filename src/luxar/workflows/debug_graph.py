from __future__ import annotations

from typing import TypedDict

from luxar.core.debug_loop import DebugLoop
from luxar.models.schemas import (
    BuildResult,
    DebugLoopResult,
    DebugRecoveryEvent,
    FlashResult,
    MonitorResult,
    ReviewReport,
)
from luxar.workflows.driver_graph import END, LANGGRAPH_AVAILABLE, StateGraph


class DebugWorkflowState(TypedDict, total=False):
    project_path: str
    probe: str | None
    port: str
    clean: bool
    lines: int
    baudrate: int | None
    timeout: int | float
    context: dict
    build_result: BuildResult | None
    flash_result: FlashResult | None
    monitor_result: MonitorResult | None
    recovery_actions: list[str]
    recovery_events: list[DebugRecoveryEvent]
    build_fix_files: list[str]
    build_fix_review_report: ReviewReport | None
    build_attempts: int
    flash_attempts: int
    monitor_attempts: int
    build_failure_type: str
    flash_failure_type: str
    monitor_failure_type: str
    serial_anomalies: list[str]
    diagnosis: str
    stage: str
    success: bool


class LangGraphDebugWorkflow:
    def __init__(self, debug_loop: DebugLoop):
        self.debug_loop = debug_loop

    def run(
        self,
        project_path: str,
        probe: str | None = None,
        port: str = "",
        clean: bool = False,
        lines: int = 10,
        baudrate: int | None = None,
    ) -> DebugLoopResult:
        if not LANGGRAPH_AVAILABLE:
            return self.debug_loop.run(
                project_path=project_path,
                probe=probe,
                port=port,
                clean=clean,
                lines=lines,
                baudrate=baudrate,
            )

        workflow = self._build_graph()
        final_state = workflow.invoke(
            {
                "project_path": project_path,
                "probe": probe,
                "port": port,
                "clean": clean,
                "lines": lines,
                "baudrate": baudrate,
                "timeout": self.debug_loop.config.monitor.default_timeout,
                "recovery_actions": [],
                "recovery_events": [],
                "build_fix_files": [],
                "build_fix_review_report": None,
                "build_attempts": 0,
                "flash_attempts": 0,
                "monitor_attempts": 0,
                "build_failure_type": "",
                "flash_failure_type": "",
                "monitor_failure_type": "",
                "diagnosis": "",
                "stage": "build",
                "success": False,
            }
        )
        return self._state_to_result(final_state)

    def _build_graph(self):
        graph = StateGraph(DebugWorkflowState)
        graph.add_node("prepare", self._prepare_node)
        graph.add_node("build", self._build_node)
        graph.add_node("recover_build", self._recover_build_node)
        graph.add_node("recover_build_fix", self._recover_build_fix_node)
        graph.add_node("recover_build_link", self._recover_build_link_node)
        graph.add_node("review_build_fix", self._review_build_fix_node)
        graph.add_node("flash", self._flash_node)
        graph.add_node("recover_flash", self._recover_flash_node)
        graph.add_node("monitor", self._monitor_node)
        graph.add_node("recover_monitor", self._recover_monitor_node)
        graph.add_node("recover_serial_fix", self._recover_serial_fix_node)
        graph.set_entry_point("prepare")
        graph.add_edge("prepare", "build")
        graph.add_conditional_edges(
            "build",
            self._after_build,
            {
                "flash": "flash",
                "recover_build_fix": "recover_build_fix",
                "recover_build_link": "recover_build_link",
                "recover_build": "recover_build",
                "end": END,
            },
        )
        graph.add_edge("recover_build_fix", "review_build_fix")
        graph.add_edge("recover_build_link", "review_build_fix")
        graph.add_conditional_edges(
            "review_build_fix",
            self._after_build_fix_review,
            {
                "build": "build",
                "end": END,
            },
        )
        graph.add_edge("recover_build", "build")
        graph.add_conditional_edges(
            "flash",
            self._after_flash,
            {
                "monitor": "monitor",
                "recover_flash": "recover_flash",
                "end": END,
            },
        )
        graph.add_edge("recover_flash", "flash")
        graph.add_conditional_edges(
            "monitor",
            self._after_monitor,
            {
                "recover_serial_fix": "recover_serial_fix",
                "recover_monitor": "recover_monitor",
                "end": END,
            },
        )
        graph.add_edge("recover_serial_fix", "review_build_fix")
        graph.add_edge("recover_monitor", "monitor")
        return graph.compile()

    def _prepare_node(self, state: DebugWorkflowState) -> DebugWorkflowState:
        context = self.debug_loop._create_context(self.debug_loop._resolve_project(state["project_path"]))
        return {"context": context}

    def _build_node(self, state: DebugWorkflowState) -> DebugWorkflowState:
        build_result = self.debug_loop._run_build(state["context"], clean=state.get("clean", False))
        diagnosis = "" if build_result.success else self.debug_loop._diagnose_build_failure(build_result.stderr)
        failure_type = "" if build_result.success else self.debug_loop._classify_build_failure(build_result.stderr)
        return {
            "build_result": build_result,
            "build_attempts": state.get("build_attempts", 0) + 1,
            "build_failure_type": failure_type,
            "diagnosis": diagnosis,
            "stage": "build" if not build_result.success else "flash",
            "success": bool(build_result.success),
        }

    def _recover_build_node(self, state: DebugWorkflowState) -> DebugWorkflowState:
        actions = list(state.get("recovery_actions", []))
        events = list(state.get("recovery_events", []))
        failure_type = state.get("build_failure_type", "")
        if failure_type == "configure_or_cache_issue":
            action = "Retried build with clean build directory after configure/cache-related build failure."
        else:
            action = "Retried build with clean build directory after initial build failure."
        actions.append(action)
        events.append(
            DebugRecoveryEvent(
                phase="build",
                action_kind="retry",
                message=action,
                attempt=state.get("build_attempts", 0) + 1,
            )
        )
        return {
            "clean": True,
            "recovery_actions": actions,
            "recovery_events": events,
            "diagnosis": "Retrying build with a clean build directory.",
        }

    def _recover_build_fix_node(self, state: DebugWorkflowState) -> DebugWorkflowState:
        actions = list(state.get("recovery_actions", []))
        events = list(state.get("recovery_events", []))
        fix_result = self.debug_loop._attempt_build_fix(state["context"], state["build_result"])
        applied_actions = fix_result["actions"] or ["Attempted build-aware source repair, but no automatic fix was applied."]
        actions.extend(applied_actions)
        for item in applied_actions:
            events.append(
                DebugRecoveryEvent(
                    phase="build",
                    action_kind="fix",
                    message=item,
                    attempt=state.get("build_attempts", 0) + 1,
                )
            )
        return {
            "recovery_actions": actions,
            "recovery_events": events,
            "build_fix_files": fix_result["fixed_files"],
            "build_fix_review_report": None,
            "diagnosis": "Reviewing source-level repair before rebuilding.",
        }

    def _recover_build_link_node(self, state: DebugWorkflowState) -> DebugWorkflowState:
        actions = list(state.get("recovery_actions", []))
        events = list(state.get("recovery_events", []))
        fix_result = self.debug_loop._attempt_link_repair(state["context"], state["build_result"])
        applied_actions = fix_result["actions"] or ["Attempted STM32 link-context repair, but no automatic repair was applied."]
        actions.extend(applied_actions)
        for item in applied_actions:
            events.append(
                DebugRecoveryEvent(
                    phase="build",
                    action_kind="fix",
                    message=item,
                    attempt=state.get("build_attempts", 0) + 1,
                )
            )
        return {
            "recovery_actions": actions,
            "recovery_events": events,
            "build_fix_files": fix_result["fixed_files"],
            "build_fix_review_report": None,
            "diagnosis": "Reviewing STM32 link-context repair before rebuilding.",
        }

    def _review_build_fix_node(self, state: DebugWorkflowState) -> DebugWorkflowState:
        report = self.debug_loop._review_fixed_files(
            state["context"],
            list(state.get("build_fix_files", [])),
        )
        diagnosis = (
            "Build-aware source repair passed review. Rebuilding the project."
            if report.passed
            else "Automatic build-aware source repair failed review and was stopped before rebuild."
        )
        return {
            "build_fix_review_report": report,
            "diagnosis": diagnosis,
        }

    def _flash_node(self, state: DebugWorkflowState) -> DebugWorkflowState:
        flash_result = self.debug_loop._run_flash(
            state["context"],
            probe=state.get("probe") or self.debug_loop.config.flash.default_probe,
        )
        diagnosis = "" if flash_result.success else self.debug_loop._diagnose_flash_failure(
            flash_result.stdout,
            flash_result.stderr,
        )
        failure_type = "" if flash_result.success else self.debug_loop._classify_flash_failure(
            flash_result.stdout,
            flash_result.stderr,
        )
        return {
            "flash_result": flash_result,
            "flash_attempts": state.get("flash_attempts", 0) + 1,
            "flash_failure_type": failure_type,
            "diagnosis": diagnosis,
            "stage": "flash" if not flash_result.success else "monitor",
            "success": bool(flash_result.success),
        }

    def _recover_flash_node(self, state: DebugWorkflowState) -> DebugWorkflowState:
        actions = list(state.get("recovery_actions", []))
        events = list(state.get("recovery_events", []))
        failure_type = state.get("flash_failure_type", "")
        probe = state.get("probe") or self.debug_loop.config.flash.default_probe
        if failure_type == "target_not_identified":
            message = "Retried flash with an explicit default probe after target identification failure."
            actions.append(message)
            events.append(
                DebugRecoveryEvent(
                    phase="flash",
                    action_kind="retry",
                    message=message,
                    attempt=state.get("flash_attempts", 0) + 1,
                )
            )
            return {
                "probe": probe,
                "recovery_actions": actions,
                "recovery_events": events,
                "diagnosis": "Retrying flash with explicit probe selection after target identification failure.",
            }
        message = "Retried flash after a transient programmer/probe failure."
        actions.append(message)
        events.append(
            DebugRecoveryEvent(
                phase="flash",
                action_kind="retry",
                message=message,
                attempt=state.get("flash_attempts", 0) + 1,
            )
        )
        return {
            "recovery_actions": actions,
            "recovery_events": events,
            "diagnosis": "Retrying flash after transient flash failure.",
        }

    def _monitor_node(self, state: DebugWorkflowState) -> DebugWorkflowState:
        monitor_result = self.debug_loop._run_monitor(
            state["context"],
            port=state.get("port", ""),
            baudrate=state.get("baudrate") or self.debug_loop.config.monitor.default_baudrate,
            timeout=state.get("timeout", self.debug_loop.config.monitor.default_timeout),
            lines=state.get("lines", 10),
        )
        diagnosis = self.debug_loop._diagnose_monitor_result(monitor_result)
        failure_type = "" if (monitor_result.success and monitor_result.lines) else self.debug_loop._classify_monitor_failure(
            monitor_result
        )
        serial_anomalies: list[str] = []
        if monitor_result.lines:
            serial_anomalies = self.debug_loop._parse_serial_diagnostics(monitor_result.lines)
        success = bool(monitor_result.success and monitor_result.lines)
        stage = "complete" if success else "monitor"
        state["context"]["logger"].log_event(
            "DEBUG_LOOP",
            state["context"]["project"].name,
            {
                "success": success,
                "stage": stage,
                "diagnosis": diagnosis,
                "snapshot_path": state["context"]["snapshot_path"],
            },
        )
        return {
            "monitor_result": monitor_result,
            "monitor_attempts": state.get("monitor_attempts", 0) + 1,
            "monitor_failure_type": failure_type,
            "serial_anomalies": serial_anomalies,
            "diagnosis": diagnosis,
            "stage": stage,
            "success": success,
        }

    def _recover_monitor_node(self, state: DebugWorkflowState) -> DebugWorkflowState:
        actions = list(state.get("recovery_actions", []))
        events = list(state.get("recovery_events", []))
        timeout = max(float(state.get("timeout", self.debug_loop.config.monitor.default_timeout)), 1.0)
        failure_type = state.get("monitor_failure_type", "")
        if failure_type == "port_busy":
            message = "Retried monitor after a transient busy-port condition."
            actions.append(message)
            events.append(
                DebugRecoveryEvent(
                    phase="monitor",
                    action_kind="retry",
                    message=message,
                    attempt=state.get("monitor_attempts", 0) + 1,
                )
            )
            return {
                "recovery_actions": actions,
                "recovery_events": events,
                "diagnosis": "Retrying monitor after transient serial-port contention.",
            }
        new_timeout = timeout + 2.0
        new_lines = max(int(state.get("lines", 10)), 10)
        message = f"Retried monitor with increased timeout ({int(new_timeout)}s) after missing initial UART output."
        actions.append(message)
        events.append(
            DebugRecoveryEvent(
                phase="monitor",
                action_kind="retry",
                message=message,
                attempt=state.get("monitor_attempts", 0) + 1,
            )
        )
        return {
            "timeout": new_timeout,
            "lines": new_lines,
            "recovery_actions": actions,
            "recovery_events": events,
            "diagnosis": "Retrying monitor with a longer timeout.",
        }

    def _after_build(self, state: DebugWorkflowState) -> str:
        build_result = state.get("build_result")
        if build_result and build_result.success:
            return "flash"
        if state.get("build_attempts", 0) < 2 and state.get("build_failure_type") == "compile_error":
            return "recover_build_fix"
        if state.get("build_attempts", 0) < 2 and state.get("build_failure_type") == "link_error":
            return "recover_build_link"
        if (
            state.get("build_attempts", 0) < 2
            and not state.get("clean", False)
            and state.get("build_failure_type") in {"configure_or_cache_issue", "generic_build_failure"}
        ):
            return "recover_build"
        return "end"

    def _after_build_fix_review(self, state: DebugWorkflowState) -> str:
        report = state.get("build_fix_review_report")
        if report and report.passed:
            return "build"
        return "end"

    def _after_flash(self, state: DebugWorkflowState) -> str:
        flash_result = state.get("flash_result")
        if flash_result and flash_result.success:
            return "monitor"
        if state.get("flash_attempts", 0) < 2 and state.get("flash_failure_type") in {
            "probe_missing",
            "target_not_identified",
        }:
            return "recover_flash"
        return "end"

    def _after_monitor(self, state: DebugWorkflowState) -> str:
        monitor_result = state.get("monitor_result")
        # If serial anomalies exist (FAIL markers), route to fix
        anomalies = state.get("serial_anomalies", [])
        if monitor_result and monitor_result.success and monitor_result.lines:
            if anomalies:
                return "recover_serial_fix"
            return "end"
        if state.get("monitor_attempts", 0) < 2 and state.get("monitor_failure_type") in {
            "port_busy",
            "no_data",
            "no_output",
        }:
            return "recover_monitor"
        return "end"

    def _recover_serial_fix_node(self, state: DebugWorkflowState) -> DebugWorkflowState:
        """Generate a code fix based on serial diagnostic anomalies."""
        from luxar.core.code_fixer import CodeFixer

        anomalies = state.get("serial_anomalies", [])
        actions = list(state.get("recovery_actions", []))
        events = list(state.get("recovery_events", []))
        project = state["context"]["project"]
        app_source = project / "App" / "Src" / "app_main.c"

        fix_context = self.debug_loop._serial_anomalies_to_fix_context(anomalies)
        action = f"Serial diagnostic fix triggered: {fix_context[:120]}"
        actions.append(action)
        events.append(
            DebugRecoveryEvent(
                phase="monitor",
                action_kind="fix",
                message=action,
                attempt=state.get("monitor_attempts", 0),
            )
        )

        fixer = CodeFixer(self.debug_loop.config)
        if app_source.exists():
            review_engine = self.debug_loop.config.review if hasattr(self.debug_loop.config, 'review') else None
            from luxar.core.review_engine import ReviewEngine
            from luxar.models.schemas import ReviewReport, ReviewIssue
            # Create a synthetic review report from serial anomalies
            issues: list = []
            for a in anomalies:
                issues.append(ReviewIssue(
                    file=str(app_source),
                    line=1,
                    column=0,
                    severity="critical" if "FAIL" in a else "warning",
                    rule_id="SERIAL-DIAG",
                    message=a,
                    suggestion=f"Fix firmware to resolve: {a}",
                ))
            report = ReviewReport(
                passed=False,
                total_issues=len(issues),
                critical_count=sum(1 for i in issues if getattr(i, 'severity', '') == 'critical'),
                error_count=0,
                warning_count=sum(1 for i in issues if getattr(i, 'severity', '') == 'warning'),
                issues=issues,
                raw_logs={"serial_diagnostics": {"anomalies": anomalies}},
            )
            fix_result = fixer.fix_file(
                project_path=str(project),
                file_path=str(app_source),
                review_report=report,
                apply_changes=True,
            )
            if fix_result.success:
                build_fix_files = list(state.get("build_fix_files", []))
                build_fix_files.append(str(app_source))
                return {
                    "recovery_actions": actions,
                    "recovery_events": events,
                    "build_fix_files": build_fix_files,
                    "build_fix_review_report": fix_result.review_report,
                    "diagnosis": f"Applied serial diagnostic fix for {len(anomalies)} anomaly(s).",
                    "clean": True,
                }

        actions.append("Serial fix skipped — app_main.c not found.")
        return {
            "recovery_actions": actions,
            "recovery_events": events,
            "diagnosis": "Serial fix attempted but no fixable source found.",
        }

    def _state_to_result(self, state: DebugWorkflowState) -> DebugLoopResult:
        context = state.get("context", {})
        return DebugLoopResult(
            success=bool(state.get("success", False)),
            stage=state.get("stage", "build"),
            diagnosis=state.get("diagnosis", ""),
            build_result=state.get("build_result"),
            build_fix_files=list(state.get("build_fix_files", [])),
            build_fix_review_report=state.get("build_fix_review_report"),
            flash_result=state.get("flash_result"),
            monitor_result=state.get("monitor_result"),
            recovery_actions=list(state.get("recovery_actions", [])),
            recovery_events=list(state.get("recovery_events", [])),
            build_attempts=state.get("build_attempts", 0),
            flash_attempts=state.get("flash_attempts", 0),
            monitor_attempts=state.get("monitor_attempts", 0),
            snapshot_path=context.get("snapshot_path", ""),
            log_dir=context.get("log_dir", ""),
        )

