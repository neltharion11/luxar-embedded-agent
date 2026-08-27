"""Shared project-analysis use case with fingerprint-based persistent reuse."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from luxar.database.persistence import PersistencePort
from luxar.domain.project_analysis import ProjectAnalysis
from luxar.domain.repairs import ProjectFile
from luxar.ports.project_analyzer import ProjectAnalyzer
from luxar.ports.workspace import WorkspacePort


_ANALYSIS_MEMORY_KEY = "project.current_analysis"
_ANALYSIS_VERSION = b"luxar-project-analysis-v2\0"
_SOURCE_SUFFIXES = (".c", ".cc", ".cpp", ".s")
_CODE_SYMBOL_RE = re.compile(r"\b(SDA|SCL)\b", re.IGNORECASE)
_DISPLAY_DIAGNOSIS_RE = re.compile(
    r"(?:屏幕|显示|oled|ssd1306|sh1106|screen|display).*(?:不亮|没亮|未亮|"
    r"无显示|没有显示|不显示|显示不出来|黑屏|无反应|没有反应|没有任何反应|"
    r"没反应|不工作|blank|no display|not lit|no response|doesn.?t work)",
    re.IGNORECASE,
)
_DEFINE_RE = re.compile(
    r"^\s*#define\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+(?P<value>[^\s/]+)",
    re.MULTILINE,
)


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
    inspection_request: str | None = None,
) -> ProjectAnalysis:
    """Analyze current code without confusing a focused query with cached overview."""

    exists = bool(
        getattr(workspace, "project_exists", project_path.exists())
    )
    if not exists:
        analysis = _missing_analysis()
        _persist_analysis(analysis, persistence, project_key)
        return analysis

    files = workspace.read_project_files(project_path)
    fingerprint = source_fingerprint(files)
    focused = bool(inspection_request and inspection_request.strip())
    if not force and not focused:
        cached = _cached_analysis(persistence, project_key, fingerprint)
        if cached is not None:
            return cached

    analysis = (
        analyzer.analyze(
            project_name=project_path.name,
            target_chip=target_chip,
            fingerprint=fingerprint,
            files=files,
            inspection_request=inspection_request,
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
    # 针对本轮问题裁剪过的分析不能覆盖可复用的全项目概览。
    if not focused:
        _persist_analysis(analysis, persistence, project_key)
    return analysis


def render_project_analysis(
    project_name: str,
    analysis: ProjectAnalysis,
    inspection_request: str | None = None,
) -> str:
    """Turn validated analysis into readable prose rather than a data dump."""

    if not analysis.project_exists:
        return f"项目 {project_name} 尚不存在，需要先创建基础 ESP-IDF 工程。"
    request = " ".join((inspection_request or "").split())
    heading = (
        f"针对“{request[:240]}”，项目 {project_name} 的检查结果如下。"
        if request
        else f"项目 {project_name} 的当前代码分析如下。"
    )
    paragraphs = [heading, analysis.summary]
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


def extract_focused_project_fact(
    question: str,
    files: list[ProjectFile],
) -> str | None:
    """Return a minimal answer when the source contains an exact pin mapping.

    This is a source-fact extractor, not a question classifier. It only emits
    an answer when every requested I2C symbol has a consistent numeric macro
    definition; all other questions continue through the normal project
    analysis report.
    """

    requested = list(dict.fromkeys(
        symbol.upper() for symbol in _CODE_SYMBOL_RE.findall(question)
    ))
    if not requested:
        return None

    values: dict[str, set[str]] = {symbol: set() for symbol in requested}
    for project_file in files:
        for match in _DEFINE_RE.finditer(project_file.content):
            name = match.group("name").upper()
            value = match.group("value")
            for symbol in requested:
                if symbol not in name:
                    continue
                number = re.fullmatch(r"(?:GPIO_NUM_)?(\d+)", value, re.IGNORECASE)
                if number is not None:
                    values[symbol].add(f"GPIO{number.group(1)}")

    if any(len(values[symbol]) != 1 for symbol in requested):
        return None
    lines = ["根据项目当前配置："]
    for symbol in requested:
        lines.append(f"- {symbol}：{next(iter(values[symbol]))}")
    return "\n".join(lines)


def extract_focused_project_diagnosis(
    question: str,
    files: list[ProjectFile],
) -> str | None:
    """Return a concise, source-grounded first diagnosis for a blank display.

    This deliberately separates confirmed software facts from likely hardware
    causes. Without serial output or a connected board, source inspection cannot
    prove which physical cause is the one affecting the user's device.
    """

    if _DISPLAY_DIAGNOSIS_RE.search(question) is None:
        return None

    values: dict[str, set[str]] = {"SDA": set(), "SCL": set()}
    address_values: set[str] = set()
    has_init = False
    has_clear = False
    has_display = False
    has_error_logs = False
    for project_file in files:
        content = project_file.content
        for match in _DEFINE_RE.finditer(content):
            name = match.group("name").upper()
            value = match.group("value")
            for symbol in values:
                if symbol not in name:
                    continue
                number = re.fullmatch(r"(?:GPIO_NUM_)?(\d+)", value, re.IGNORECASE)
                if number is not None:
                    values[symbol].add(f"GPIO{number.group(1)}")
            if "I2C_ADDR" in name and re.fullmatch(
                r"0x3[CD]", value, re.IGNORECASE
            ):
                address_values.add(value.upper())
        has_init = has_init or "ssd1306_init" in content
        has_clear = has_clear or "ssd1306_clear" in content
        has_display = has_display or "ssd1306_display_text" in content
        has_error_logs = has_error_logs or any(
            marker in content
            for marker in (
                "SSD1306 init failed",
                "SSD1306 clear failed",
                "SSD1306 display_text failed",
            )
        )

    lines = ["根据当前项目代码，软件侧已确认："]
    if values["SDA"] and values["SCL"]:
        lines.append(
            f"- I2C 引脚是 SDA={next(iter(values['SDA']))}、"
            f"SCL={next(iter(values['SCL']))}。"
        )
    if address_values:
        lines.append(
            "- 启动时会自动探测 OLED 常用地址 "
            + " 和 ".join(sorted(address_values))
            + "。"
        )
    if has_init and has_clear and has_display:
        lines.append("- 启动流程是初始化 OLED、清屏，然后显示 `helloworld`。")
    if has_error_logs:
        lines.append(
            "- 串口会打印初始化、清屏和写屏失败信息，说明代码已有基本错误出口。"
        )

    lines.extend(
        [
            "",
            "但当前没有这块开发板的串口运行日志，所以还不能仅凭源码断定唯一根因。",
            "优先排查：",
            "1. OLED 的 VCC/GND 是否接对，模块是否使用 3.3V；",
            "2. SDA/SCL 是否接反，实际是否接到了 GPIO21/GPIO22；",
            "3. 模块控制器是否真的是 SSD1306（有些模块实际是 SH1106）；",
            "4. 查看串口是否出现 `SSD1306 init failed`、`clear failed` 或 "
            "`display_text failed`。初始化成功但仍黑屏时，优先怀疑供电、接线或控制器兼容性。",
        ]
    )
    return "\n".join(lines)
