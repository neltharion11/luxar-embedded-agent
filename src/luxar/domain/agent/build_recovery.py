"""把 ESP-IDF 构建失败转换为确定性的修复决策。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from luxar.domain.evidence import BuildEvidence


class BuildRecoveryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    category: Literal[
        "environment",
        "dependency",
        "source",
        "linker",
        "timeout",
        "unknown",
    ]
    action: Literal[
        "fix_environment",
        "resolve_dependency",
        "repair_source",
        "repair_linker",
        "retry_with_larger_budget",
        "inspect_build_output",
    ]
    retryable_after_action: bool
    requires_approval: bool = False
    target_files: list[str] = Field(default_factory=list, max_length=80)
    feedback: list[str] = Field(min_length=1, max_length=40)


class BuildFailureAdvisor:
    _ACTIONS = {
        "environment": "fix_environment",
        "dependency": "resolve_dependency",
        "source": "repair_source",
        "linker": "repair_linker",
        "timeout": "retry_with_larger_budget",
        "unknown": "inspect_build_output",
    }

    def analyze(self, evidence: BuildEvidence) -> BuildRecoveryDecision:
        if evidence.success:
            raise ValueError("成功构建不需要失败修复决策")
        category = evidence.error_category or "unknown"
        target_files = list(
            dict.fromkeys(
                diagnostic.file
                for diagnostic in evidence.diagnostics
                if diagnostic.file is not None
            )
        )
        feedback = self._feedback(category, evidence)
        return BuildRecoveryDecision(
            category=category,
            action=self._ACTIONS[category],  # type: ignore[arg-type]
            retryable_after_action=category in {"source", "linker", "timeout"},
            requires_approval=category == "dependency",
            target_files=target_files,
            feedback=feedback,
        )

    def _feedback(
        self,
        category: str,
        evidence: BuildEvidence,
    ) -> list[str]:
        if category == "source":
            prefix = "根据编译器诊断修复源码，不扩大当前任务文件范围"
        elif category == "linker":
            prefix = "检查缺失符号、重复定义和组件链接依赖"
        elif category == "dependency":
            prefix = "核对组件清单与锁文件；下载依赖前需要明确授权"
        elif category == "environment":
            prefix = "修复 ESP-IDF、CMake、Ninja 或工具链环境后再构建"
        elif category == "timeout":
            prefix = "确认构建未死锁后，使用受控的更大时间预算重试"
        else:
            prefix = "检查脱敏构建输出并补充可分类诊断"
        messages = [prefix]
        for diagnostic in evidence.diagnostics[:8]:
            location = diagnostic.file or "build"
            if diagnostic.line is not None:
                location = f"{location}:{diagnostic.line}"
            messages.append(f"{location}: {diagnostic.message}")
        return messages


__all__ = ["BuildFailureAdvisor", "BuildRecoveryDecision"]
