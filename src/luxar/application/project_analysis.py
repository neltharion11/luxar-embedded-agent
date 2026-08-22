"""Shared project-analysis use case with fingerprint-based persistent reuse."""

from __future__ import annotations

import hashlib
from pathlib import Path

from luxar.database.persistence import PersistencePort
from luxar.domain.project_analysis import ProjectAnalysis
from luxar.domain.repairs import ProjectFile
from luxar.ports.project_analyzer import ProjectAnalyzer
from luxar.ports.workspace import WorkspacePort


_ANALYSIS_MEMORY_KEY = "project.current_analysis"
_ANALYSIS_VERSION = b"luxar-project-analysis-v2\0"
_SOURCE_SUFFIXES = (".c", ".cc", ".cpp", ".s")


def _structural_gaps(files: list[ProjectFile]) -> list[str]:
    paths = {item.path for item in files}
    sources = [
        item for item in files if item.path.lower().endswith(_SOURCE_SUFFIXES)
    ]
    gaps: list[str] = []
    if "CMakeLists.txt" not in paths:
        gaps.append("项目根目录缺少 CMakeLists.txt，当前不是完整的 ESP-IDF 项目结构")
    if not sources:
        gaps.append("没有发现 C/C++/汇编源文件，当前没有可执行的固件实现")
    elif not any("app_main" in item.content for item in sources):
        gaps.append("源码中没有发现 app_main 入口，应用入口尚未实现或无法确认")
    return gaps


def source_fingerprint(files: list[ProjectFile]) -> str:
    digest = hashlib.sha256()
    # 分析规则升级时改变版本串，旧缓存会自然失效。
    digest.update(_ANALYSIS_VERSION)
    for item in sorted(files, key=lambda value: value.path):
        digest.update(item.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.content.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _fallback_analysis(
    files: list[ProjectFile],
    fingerprint: str,
) -> ProjectAnalysis:
    paths = [item.path for item in files]
    sources = [
        item for item in files if item.path.lower().endswith(_SOURCE_SUFFIXES)
    ]
    entry_points = [
        item.path for item in sources if "app_main" in item.content
    ]
    has_source = bool(sources)
    summary = (
        "项目包含可读取的 ESP-IDF 源码；当前仅完成确定性结构分析。"
        if has_source
        else "项目结构存在，但没有发现可执行源码。"
    )
    gaps = _structural_gaps(files)
    return ProjectAnalysis(
        project_exists=True,
        has_source_code=has_source,
        fingerprint=fingerprint,
        summary=summary,
        entry_points=entry_points,
        implemented_features=[],
        architecture=[f"共发现 {len(files)} 个受控源码或配置文件"],
        gaps=gaps,
        risks=[],
        evidence_paths=paths[:80],
        cache_hit=False,
    )


def _missing_analysis() -> ProjectAnalysis:
    return ProjectAnalysis(
        project_exists=False,
        has_source_code=False,
        fingerprint="missing",
        summary="项目尚不存在，需要先创建基础 ESP-IDF 工程。",
        gaps=["项目目录和源码尚未创建"],
    )


def _cached_analysis(
    persistence: PersistencePort | None,
    project_key: str | None,
    fingerprint: str,
) -> ProjectAnalysis | None:
    if persistence is None or project_key is None:
        return None
    memories = persistence.find_memories(
        project_key,
        memory_type="project_analysis",
        limit=1,
    )
    if not memories:
        return None
    try:
        analysis = ProjectAnalysis.model_validate(memories[0].value)
    except (TypeError, ValueError):
        return None
    if analysis.fingerprint != fingerprint:
        return None
    return analysis.model_copy(update={"cache_hit": True})


def _persist_analysis(
    analysis: ProjectAnalysis,
    persistence: PersistencePort | None,
    project_key: str | None,
) -> None:
    if persistence is None or project_key is None:
        return
    persistence.upsert_memory(
        project_key=project_key,
        memory_key=_ANALYSIS_MEMORY_KEY,
        memory_type="project_analysis",
        value=analysis.model_copy(
            update={"cache_hit": False}
        ).model_dump(mode="json"),
        confidence=1.0,
    )


def analyze_current_project(
    *,
    project_path: Path,
    target_chip: str | None,
    workspace: WorkspacePort,
    analyzer: ProjectAnalyzer | None,
    persistence: PersistencePort | None,
    project_key: str | None,
    force: bool = False,
) -> ProjectAnalysis:
    """Analyze current code, reusing only an exact source fingerprint match."""

    exists = bool(
        getattr(workspace, "project_exists", project_path.exists())
    )
    if not exists:
        analysis = _missing_analysis()
        _persist_analysis(analysis, persistence, project_key)
        return analysis

    files = workspace.read_project_files(project_path)
    fingerprint = source_fingerprint(files)
    if not force:
        cached = _cached_analysis(persistence, project_key, fingerprint)
        if cached is not None:
            return cached

    analysis = (
        analyzer.analyze(
            project_name=project_path.name,
            target_chip=target_chip,
            fingerprint=fingerprint,
            files=files,
        )
        if analyzer is not None
        else _fallback_analysis(files, fingerprint)
    )
    analysis = analysis.model_copy(
        update={
            "project_exists": True,
            "has_source_code": any(
                item.path.lower().endswith(_SOURCE_SUFFIXES)
                for item in files
            ),
            "fingerprint": fingerprint,
            "cache_hit": False,
            # 文件结构属于确定性事实，即使模型遗漏，也必须明确报告。
            "gaps": list(dict.fromkeys([
                *analysis.gaps,
                *_structural_gaps(files),
            ])),
        }
    )
    _persist_analysis(analysis, persistence, project_key)
    return analysis


def render_project_analysis(
    project_name: str,
    analysis: ProjectAnalysis,
) -> str:
    """Turn validated analysis into readable prose rather than a data dump."""

    if not analysis.project_exists:
        return f"项目 {project_name} 尚不存在，需要先创建基础 ESP-IDF 工程。"
    paragraphs = [f"项目 {project_name} 的当前代码分析如下。", analysis.summary]
    if analysis.entry_points:
        paragraphs.append("程序入口位于 " + "、".join(analysis.entry_points) + "。")
    if analysis.implemented_features:
        paragraphs.append(
            "当前已经实现：" + "；".join(analysis.implemented_features) + "。"
        )
    if analysis.architecture:
        paragraphs.append("代码结构：" + "；".join(analysis.architecture) + "。")
    if analysis.gaps:
        paragraphs.append("尚未完成或无法确认：" + "；".join(analysis.gaps) + "。")
    if analysis.risks:
        paragraphs.append("需要注意：" + "；".join(analysis.risks) + "。")
    if analysis.evidence_paths:
        paragraphs.append(
            "以上判断主要依据：" + "、".join(analysis.evidence_paths[:12]) + "。"
        )
    return "\n\n".join(paragraphs)
