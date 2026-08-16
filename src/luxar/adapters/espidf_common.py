"""ESP-IDF Adapter 共享内部工具：启动器校验、链接检查和输出脱敏。

三个硬件 Adapter(构建、项目创建、设备)必须使用同一套安全规则，
因此这些私有辅助函数从 espidf_cli.py 提取到本模块集中维护。
它们都是纯函数或只读检查，不启动任何进程。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path, PureWindowsPath
from typing import Sequence

from luxar.ports.espidf_errors import EspIdfError

_ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"
)
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?i)(?<![\w])([A-Z]:[\\/][^\s:]+(?:[\\/][^\s:]+)*)"
)
_POSIX_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![\w.])(/(?:[^\s:]+/)*[^\s:]+)"
)

_EXCLUDED_EXACT_DIRECTORIES = frozenset(
    {
        ".git",
        ".vscode",
        ".idea",
        "build",
        "managed_components",
        "__pycache__",
    }
)


def _is_excluded_directory_name(name: str) -> bool:
    return (
        name.startswith(".")
        or name in _EXCLUDED_EXACT_DIRECTORIES
        or name.startswith("build_")
    )


def _is_link_or_junction(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction()
    except OSError as error:
        raise EspIdfError(
            category="invalid_project",
            message="ESP-IDF 项目路径无效",
            retryable=False,
        ) from error


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def _coerce_timeout_output(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def _sanitize_output(text: str, root: Path, max_chars: int) -> str:
    # 去掉 ANSI 颜色并统一换行，再移除项目根路径和所有外部绝对路径。
    cleaned = _strip_ansi(text).replace("\r\n", "\n").replace("\r", "\n")
    root_text = str(root.resolve())
    root_variants = {
        root_text,
        root_text.replace("\\", "/"),
        root_text.replace("/", "\\"),
    }

    for variant in sorted(root_variants, key=len, reverse=True):
        cleaned = re.sub(
            re.escape(variant) + r"[\\/]?",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )

    cleaned = cleaned.replace("\\", "/")
    cleaned = _WINDOWS_ABSOLUTE_PATH_RE.sub("<external-path>", cleaned)
    cleaned = _POSIX_ABSOLUTE_PATH_RE.sub("<external-path>", cleaned)

    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars]


def validate_idf_command_tokens(command: Sequence[str]) -> tuple[str, ...]:
    """校验启动器令牌并返回不可变副本；拒绝空命令或空白令牌。"""

    normalized = tuple(command)

    if not normalized or any(
        not isinstance(token, str) or not token.strip()
        for token in normalized
    ):
        raise ValueError("idf_command must contain non-empty strings")

    return normalized


def build_safe_idf_environment(
    allow_dependency_downloads: bool,
) -> dict[str, str]:
    """构造 idf.py 子进程环境：去颜色、去提示，默认禁止依赖下载。"""

    import os

    environment = os.environ.copy()
    environment["IDF_COMPONENT_NO_COLORS"] = "1"
    environment["IDF_COMPONENT_NO_HINTS"] = "1"

    if allow_dependency_downloads:
        environment.pop("IDF_COMPONENT_MANAGER", None)
    else:
        environment["IDF_COMPONENT_MANAGER"] = "0"

    return environment


def validate_espidf_launcher(idf_command: tuple[str, ...]) -> None:
    """确认 idf.py 启动器可用，失败时抛出脱敏的环境错误。"""

    launcher = Path(idf_command[0])

    if launcher.is_absolute():
        if not launcher.is_file():
            raise EspIdfError(
                category="environment",
                message="ESP-IDF 命令不可用",
                retryable=False,
            )
    elif shutil.which(idf_command[0]) is None:
        raise EspIdfError(
            category="environment",
            message="ESP-IDF 命令不可用",
            retryable=False,
        )

    for token in idf_command[1:]:
        configured_path = Path(token)
        if configured_path.is_absolute() and not configured_path.is_file():
            raise EspIdfError(
                category="environment",
                message="ESP-IDF 命令不可用",
                retryable=False,
            )
