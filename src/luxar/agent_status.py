"""Safe, structured self-inspection for the conversation entry agent."""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from importlib.util import find_spec

from luxar.application.graph import build_graph
from luxar.model_config import ModelEndpoint, RuntimeModelConfig


# These are the application capabilities that the agent can dispatch or use as
# evidence.  Keeping this registry separate from the LangGraph node list avoids
# pretending that every orchestration node is an external tool.
_TOOL_NAMES = (
    "inspect_agent_status",
    "route_conversation",
    "analyze_requirement",
    "analyze_project",
    "create_execution_plan",
    "create_project",
    "search_sdk_examples",
    "edit_firmware",
    "build_project",
    "flash_project",
    "monitor_serial",
    "analyze_device_logs",
    "plan_repair",
    "parse_knowledge_task",
    "manage_project_rag",
    "read_pdf",
    "analyze_pdf",
)


@lru_cache(maxsize=1)
def _workflow_node_names() -> tuple[str, ...]:
    """Read the compiled topology so the reported count cannot drift."""

    graph = build_graph()
    return tuple(sorted(name for name in graph.nodes if name != "__start__"))


class AgentStatusTool:
    """Inspect non-secret runtime facts before an entry-route decision.

    The result is deliberately JSON-compatible and bounded.  It contains
    configuration metadata and workflow evidence, never API keys, raw command
    output, document contents, or host paths.
    """

    name = "inspect_agent_status"

    def __init__(
        self,
        *,
        config_loader: Callable[[], RuntimeModelConfig] | None = None,
        model_endpoint_loader: Callable[[], ModelEndpoint] | None = None,
        toolchain_status_loader: Callable[[], object] | None = None,
        workflow_status_loader: Callable[[], dict[str, object]] | None = None,
        fallback_model: str | None = None,
    ) -> None:
        self._config_loader = config_loader
        self._model_endpoint_loader = model_endpoint_loader
        self._toolchain_status_loader = toolchain_status_loader
        self._workflow_status_loader = workflow_status_loader
        self._fallback_model = fallback_model or ""

    def inspect(
        self,
        *,
        user_input: str,
        knowledge_status: str | None = None,
        previous_run: dict[str, object] | None = None,
    ) -> dict[str, object]:
        config = self._load_config()
        conversation = config.conversation.resolved()
        vision = config.vision_endpoint()
        embedding = config.embedding
        nodes = _workflow_node_names()
        pdf_available = find_spec("fitz") is not None
        toolchain = self._toolchain_status()
        toolchain_available = toolchain.get("available")

        project_rag_available = bool(
            knowledge_status is not None
            and "项目外部知识库已启用" in knowledge_status
        )
        tool_availability: dict[str, bool | None] = {}
        for name in _TOOL_NAMES:
            available: bool | None = True
            if name == "manage_project_rag":
                available = project_rag_available
            elif name in {"read_pdf", "analyze_pdf"}:
                available = pdf_available
            elif name in {
                "create_project",
                "search_sdk_examples",
                "build_project",
                "flash_project",
                "monitor_serial",
            }:
                available = (
                    bool(toolchain_available)
                    if toolchain_available is not None
                    else None
                )
            tool_availability[name] = available

        latest_run = self._latest_run_status(previous_run)
        constraints: list[str] = []
        if not project_rag_available:
            constraints.append(
                "项目 RAG 当前不可用；仍可读取 PDF，但不能检索或写入项目向量库"
            )
        if vision is None:
            constraints.append(
                "PDF 当前使用 PyMuPDF 文本提取；图纸页没有额外视觉模型识别"
            )
        if latest_run.get("approval_status") == "pending":
            constraints.append("上一工作流正在等待用户审批，应先处理审批")
        if toolchain_available is False:
            constraints.append("ESP-IDF 工具链不可用，固件执行类操作会被阻止")

        recommendation = (
            "handle_pending_approval"
            if latest_run.get("approval_status") == "pending"
            else "route_latest_user_input"
        )

        return {
            "tool": self.name,
            "conversation_model": {
                "provider": conversation.provider,
                "model": conversation.model,
                "configured": conversation.configured,
                "context_window_tokens": conversation.context_window_tokens,
                "context_compaction_threshold": 0.95,
            },
            "pdf_reader": {
                "available": pdf_available,
                "text_extraction": "PyMuPDF",
                "batching": {
                    "strategy": "chapter_first",
                    "outline_priority": True,
                    "heading_inference_fallback": True,
                    "characters_per_batch": 60_000,
                    "reads_until_final_page": True,
                },
                "vision_mode": config.vision_mode,
                "vision_model": vision.model if vision is not None else None,
                "technical_analysis_model": conversation.model,
            },
            "rag": {
                "project": {
                    "available": project_rag_available,
                    "status": knowledge_status
                    or "调用方没有提供项目知识库状态",
                    "backend": "LanceDB",
                },
                "sdk_examples": {
                    "configured": True,
                    "backend": "LanceDB + local hash embedding",
                    "scope": "ESP-IDF 官方例程",
                },
            },
            "embedding": {
                "mode": embedding.mode,
                "provider": embedding.provider,
                "model": embedding.model or (
                    "local feature hashing" if embedding.mode == "local_hash" else ""
                ),
                "dimensions": embedding.dimensions,
                "configured": embedding.configured,
            },
            "tools": {
                "count": len(_TOOL_NAMES),
                "available_count": sum(
                    value is True for value in tool_availability.values()
                ),
                "unknown_count": sum(
                    value is None for value in tool_availability.values()
                ),
                "items": [
                    {"name": name, "available": tool_availability[name]}
                    for name in _TOOL_NAMES
                ],
            },
            "workflow": {
                "engine": "LangGraph",
                "node_count": len(nodes),
                "nodes": list(nodes),
                "modes": ["firmware", "inspection", "knowledge"],
                "runtime": self._workflow_runtime_status(),
                "latest_run": latest_run,
            },
            "toolchain": toolchain,
            "next_operation": {
                "selected_by": "conversation_entry_router",
                "recommendation": recommendation,
                "input_present": bool(user_input.strip()),
                "allowed": [
                    "reply_directly",
                    "report_previous_workflow",
                    "inspect_project",
                    "run_firmware_workflow",
                    "run_knowledge_workflow",
                ],
                "constraints": constraints,
            },
        }

    def _toolchain_status(self) -> dict[str, object]:
        if self._toolchain_status_loader is None:
            return {"available": None, "status": "not_provided"}
        try:
            status = self._toolchain_status_loader()
            return {
                "available": getattr(status, "available", None),
                "source": getattr(status, "source", None),
                "version": getattr(status, "version", None),
                "message": getattr(status, "message", None),
            }
        except Exception:
            return {"available": None, "status": "unavailable"}

    def _workflow_runtime_status(self) -> dict[str, object]:
        if self._workflow_status_loader is None:
            return {"status": "idle_or_unknown"}
        try:
            return self._workflow_status_loader()
        except Exception:
            return {"status": "unavailable"}

    def _load_config(self) -> RuntimeModelConfig:
        if self._config_loader is not None:
            return self._config_loader()
        if self._model_endpoint_loader is not None:
            return RuntimeModelConfig(
                conversation=self._model_endpoint_loader(),
            )
        return RuntimeModelConfig(
            conversation=ModelEndpoint(
                provider="local",
                base_url="http://127.0.0.1:8000/v1",
                model=self._fallback_model or "unknown",
            )
        )

    @staticmethod
    def _latest_run_status(
        previous_run: dict[str, object] | None,
    ) -> dict[str, object]:
        if previous_run is None:
            return {"present": False}
        trace = previous_run.get("trace")
        return {
            "present": True,
            "status": previous_run.get("status", "unknown"),
            "last_node": (
                trace[-1]
                if isinstance(trace, list) and trace and isinstance(trace[-1], str)
                else None
            ),
            "approval_status": previous_run.get(
                "approval_status", "not_requested"
            ),
            "build_executed": isinstance(
                previous_run.get("build_evidence"), dict
            ),
            "flash_executed": isinstance(
                previous_run.get("flash_evidence"), dict
            ),
        }
