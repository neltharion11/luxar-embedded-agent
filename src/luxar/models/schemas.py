from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional, TypedDict

from pydantic import BaseModel, Field


class ReviewIssue(BaseModel):
    file: str
    line: int
    column: int = 0
    severity: Literal["critical", "error", "warning", "info"]
    rule_id: str
    message: str
    suggestion: str = ""


class ReviewReport(BaseModel):
    passed: bool
    total_issues: int
    critical_count: int
    error_count: int
    warning_count: int
    issues: list[ReviewIssue] = Field(default_factory=list)
    raw_logs: dict = Field(default_factory=dict)
    reviewed_at: datetime = Field(default_factory=datetime.now)


class BuildResult(BaseModel):
    success: bool
    command: list[str] = Field(default_factory=list)
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    flash_used_kb: Optional[float] = None
    ram_used_kb: Optional[float] = None
    built_at: datetime = Field(default_factory=datetime.now)


class FlashResult(BaseModel):
    success: bool
    command: list[str] = Field(default_factory=list)
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""
    artifact_path: str = ""
    flashed_at: datetime = Field(default_factory=datetime.now)


class MonitorResult(BaseModel):
    success: bool
    port: str = ""
    lines: list[str] = Field(default_factory=list)
    error: str = ""
    port_released: bool = True
    monitored_at: datetime = Field(default_factory=datetime.now)


class DebugRecoveryEvent(BaseModel):
    phase: Literal["build", "flash", "monitor"]
    action_kind: Literal["retry", "fix"]
    message: str
    attempt: int = 0


class DebugLoopResult(BaseModel):
    success: bool
    stage: Literal["build", "flash", "monitor", "complete"]
    diagnosis: str = ""
    build_result: Optional[BuildResult] = None
    build_fix_files: list[str] = Field(default_factory=list)
    build_fix_review_report: Optional[ReviewReport] = None
    flash_result: Optional[FlashResult] = None
    monitor_result: Optional[MonitorResult] = None
    recovery_actions: list[str] = Field(default_factory=list)
    recovery_events: list[DebugRecoveryEvent] = Field(default_factory=list)
    build_attempts: int = 0
    flash_attempts: int = 0
    monitor_attempts: int = 0
    snapshot_path: str = ""
    log_dir: str = ""
    debugged_at: datetime = Field(default_factory=datetime.now)


class ProjectConfig(BaseModel):
    name: str
    path: str
    platform: str = "stm32cubemx"
    runtime: str = "baremetal"
    project_mode: str = "cubemx"
    mcu: str = ""
    ioc_file: str = ""
    firmware_package: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


class DriverMetadata(BaseModel):
    name: str
    protocol: str
    chip: str = ""
    vendor: str = ""
    device: str = ""
    path: str
    header_path: str = ""
    source_path: str = ""
    review_passed: bool = False
    source_doc: str = ""
    review_issue_count: int = 0
    reuse_count: int = 0
    kb_score: float = 0.0
    last_reused_at: Optional[datetime] = None
    stored_at: datetime = Field(default_factory=datetime.now)


class SkillArtifact(BaseModel):
    name: str
    protocol: str
    path: str
    platforms: list[str] = Field(default_factory=list)
    runtimes: list[str] = Field(default_factory=list)
    source_projects: list[str] = Field(default_factory=list)
    validation_count: int = 0
    updated_at: datetime = Field(default_factory=datetime.now)


class DriverRequirement(BaseModel):
    chip: str
    interface: str
    vendor: str = ""
    device: str = ""
    confidence: float = 0.0
    rationale: str = ""


class PinRequirement(BaseModel):
    name: str
    role: str = ""
    required: bool = True
    notes: str = ""


class BusRequirement(BaseModel):
    interface: str
    mode: str = ""
    speed_hint: str = ""
    direction: str = ""
    notes: str = ""


class ProtocolFrameHint(BaseModel):
    direction: Literal["tx", "rx", "txrx", "command", "response"]
    summary: str
    notes: str = ""


class BringupStep(BaseModel):
    step: str
    notes: str = ""


class EngineeringContext(BaseModel):
    source_documents: list[str] = Field(default_factory=list)
    document_summary: str = ""
    pin_requirements: list[PinRequirement] = Field(default_factory=list)
    bus_requirements: list[BusRequirement] = Field(default_factory=list)
    protocol_frames: list[ProtocolFrameHint] = Field(default_factory=list)
    register_hints: list[str] = Field(default_factory=list)
    bringup_sequence: list[BringupStep] = Field(default_factory=list)
    timing_constraints: list[str] = Field(default_factory=list)
    integration_notes: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    raw_matches: list[KnowledgeChunk] = Field(default_factory=list)
    parse_errors: list[str] = Field(default_factory=list)


class ProjectPlan(BaseModel):
    requirement_summary: str
    features: list[str] = Field(default_factory=list)
    needed_drivers: list[DriverRequirement] = Field(default_factory=list)
    peripheral_hints: list[str] = Field(default_factory=list)
    cubemx_or_firmware_actions: list[str] = Field(default_factory=list)
    app_behavior_summary: str = ""
    document_context_summary: str = ""
    engineering_context: Optional[EngineeringContext] = None
    risk_notes: list[str] = Field(default_factory=list)
    used_fallback: bool = False
    raw_response: str = ""


class DriverGenerationResult(BaseModel):
    success: bool
    chip: str
    interface: str
    output_dir: str = ""
    header_path: str = ""
    source_path: str = ""
    reused_existing: bool = False
    reused_driver_path: str = ""
    reuse_summary: str = ""
    reuse_sources: list[str] = Field(default_factory=list)
    error: str = ""
    raw_response: str = ""
    generated_at: datetime = Field(default_factory=datetime.now)


class CodeFixResult(BaseModel):
    success: bool
    file_path: str = ""
    applied: bool = False
    error: str = ""
    raw_response: str = ""
    review_report: Optional[ReviewReport] = None
    fixed_at: datetime = Field(default_factory=datetime.now)


class AppGenerationResult(BaseModel):
    success: bool
    project: str = ""
    requirement: str = ""
    project_plan: Optional[ProjectPlan] = None
    header_path: str = ""
    source_path: str = ""
    used_fallback: bool = False
    raw_response: str = ""
    error: str = ""
    generated_at: datetime = Field(default_factory=datetime.now)


class DriverPipelineResult(BaseModel):
    success: bool
    chip: str
    interface: str
    generated_files: list[str] = Field(default_factory=list)
    generation_result: Optional[DriverGenerationResult] = None
    review_report: Optional[ReviewReport] = None
    fix_iterations: int = 0
    fixed_files: list[str] = Field(default_factory=list)
    stored: bool = False
    stored_records: list[DriverMetadata] = Field(default_factory=list)
    skill_artifact: Optional[SkillArtifact] = None
    error: str = ""
    completed_at: datetime = Field(default_factory=datetime.now)


class WorkflowStepResult(BaseModel):
    name: str
    status: Literal["completed", "failed", "skipped"]
    message: str = ""
    payload: dict = Field(default_factory=dict)
    completed_at: datetime = Field(default_factory=datetime.now)


class WorkflowRunResult(BaseModel):
    success: bool
    workflow: str
    backend: str = "pipeline"
    steps: list[WorkflowStepResult] = Field(default_factory=list)
    summary: str = ""
    output: dict = Field(default_factory=dict)
    completed_at: datetime = Field(default_factory=datetime.now)


class TaskIntent(BaseModel):
    intent_type: Literal[
        "explain",
        "forge_project",
        "generate_driver",
        "review_or_fix",
        "debug_project",
        "project_status",
    ]
    execution_mode: Literal["explain", "plan", "execute"] = "plan"
    required_capabilities: list[str] = Field(default_factory=list)
    recommended_workflow: str = ""
    confidence: float = 0.0
    reason: str = ""


class ExecutionPlan(BaseModel):
    intent: TaskIntent
    project: str = ""
    docs: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    missing_info_questions: list[str] = Field(default_factory=list)
    dry_run: bool = False
    plan_only: bool = False


class KnowledgeChunk(BaseModel):
    doc_id: str
    chunk_id: str
    source_path: str
    title: str = ""
    content: str
    keywords: list[str] = Field(default_factory=list)
    page_start: int = 0
    page_end: int = 0
    score: float = 0.0


class DocumentParseResult(BaseModel):
    success: bool
    source_path: str
    document_id: str = ""
    title: str = ""
    extracted_text: str = ""
    chunk_count: int = 0
    chunks: list[KnowledgeChunk] = Field(default_factory=list)
    summary: str = ""
    error: str = ""
    parsed_at: datetime = Field(default_factory=datetime.now)


class AgentState(TypedDict, total=False):
    project_name: str
    project_config: ProjectConfig
    platform: str
    runtime: str
    protocol: Optional[str]
    generated_files: list[str]
    review_report: Optional[ReviewReport]
    fix_iteration: int
    max_fix_iterations: int
    build_result: Optional[BuildResult]
    flash_result: Optional[dict]
    uart_result: Optional[dict]
    snapshot_path: Optional[str]
    project_success: bool
    skill_artifact: Optional[dict]
