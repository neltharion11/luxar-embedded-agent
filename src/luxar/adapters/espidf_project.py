"""ESP-IDF 项目创建适配器：在受控父目录内执行 idf.py create-project。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Sequence

from luxar.adapters.espidf_common import (
    _coerce_timeout_output,
    _is_link_or_junction,
    _sanitize_output,
    validate_espidf_launcher,
    validate_idf_command_tokens,
)
from luxar.domain.projects import ProjectEvidence
from luxar.ports.espidf_errors import EspIdfError

# 项目名与芯片名都必须是单一小写标识符，避免任何路径或选项注入。
_PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TARGET_CHIP_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# create-project 不可用时的输出特征，用于给出固定脱敏提示。
_UNSUPPORTED_SIGNALS = (
    "invalid choice",
    "unknown command",
    "no such command",
    "no such option",
)

_SDKCONFIG_MAX_BYTES = 64 * 1024

_TARGET_LINE_PREFIX = "CONFIG_IDF_TARGET="


def _logical_command(project_name: str) -> list[str]:
    # 逻辑命令只保留动作和项目名，不记录父目录绝对路径。
    return ["idf.py", "create-project", project_name]


def _assert_no_link_components(root: Path, target: Path) -> None:
    try:
        relative_path = target.relative_to(root)
    except ValueError as error:
        raise EspIdfError(
            category="invalid_project",
            message="ESP-IDF 项目路径不能离开父目录",
            retryable=False,
        ) from error

    current_path = root

    for part in relative_path.parts:
        current_path = current_path / part

        if _is_link_or_junction(current_path):
            raise EspIdfError(
                category="invalid_project",
                message="ESP-IDF 项目路径不能经过链接",
                retryable=False,
            )


class EspIdfProjectAdapter:
    def __init__(
        self,
        *,
        idf_command: Sequence[str] = ("idf.py",),
        create_timeout_seconds: int = 120,
        max_summary_chars: int = 16_000,
    ) -> None:
        self.idf_command = validate_idf_command_tokens(idf_command)

        limits = {
            "create_timeout_seconds": create_timeout_seconds,
            "max_summary_chars": max_summary_chars,
        }

        for name, value in limits.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a positive integer")

        self.create_timeout_seconds = create_timeout_seconds
        self.max_summary_chars = max_summary_chars

    def _resolve_parent(self, parent_dir: Path) -> Path:
        try:
            if _is_link_or_junction(parent_dir):
                raise EspIdfError(
                    category="invalid_project",
                    message="项目父目录不能是链接",
                    retryable=False,
                )

            if not parent_dir.exists() or not parent_dir.is_dir():
                raise EspIdfError(
                    category="invalid_project",
                    message="项目父目录无效",
                    retryable=False,
                )

            return parent_dir.resolve(strict=True)

        except EspIdfError:
            raise
        except (OSError, RuntimeError) as error:
            raise EspIdfError(
                category="invalid_project",
                message="项目父目录无效",
                retryable=False,
            ) from error

    def _validate_arguments(self, project_name: str, target_chip: str) -> None:
        if not _PROJECT_NAME_RE.fullmatch(project_name):
            raise EspIdfError(
                category="invalid_project",
                message="项目名称无效",
                retryable=False,
            )

        if not _TARGET_CHIP_RE.fullmatch(target_chip):
            raise EspIdfError(
                category="invalid_project",
                message="目标芯片名称无效",
                retryable=False,
            )

    def _ensure_target_config(
        self,
        project_root: Path,
        target_chip: str,
    ) -> None:
        config_path = project_root / "sdkconfig.defaults"
        _assert_no_link_components(project_root, config_path)

        expected_line = f"{_TARGET_LINE_PREFIX}{target_chip}"

        if config_path.exists():
            try:
                if config_path.stat().st_size > _SDKCONFIG_MAX_BYTES:
                    raise EspIdfError(
                        category="invalid_project",
                        message="项目目标配置文件超过大小限制",
                        retryable=False,
                    )

                content = config_path.read_text(
                    encoding="utf-8",
                    errors="strict",
                )

            except EspIdfError:
                raise
            except (OSError, UnicodeError) as error:
                raise EspIdfError(
                    category="invalid_project",
                    message="项目目标配置文件无效",
                    retryable=False,
                ) from error

            target_lines = [
                line
                for line in content.splitlines()
                if line.startswith(_TARGET_LINE_PREFIX)
            ]

            # 已有配置必须与本任务目标一致，绝不静默覆盖用户选择。
            if target_lines and any(
                line != expected_line for line in target_lines
            ):
                raise EspIdfError(
                    category="invalid_project",
                    message="项目已有目标配置与本任务目标不一致",
                    retryable=False,
                )

            if target_lines:
                return

            # 没有目标行时追加一行；保留用户其余配置。
            try:
                config_path.write_text(
                    content + f"\n{expected_line}\n",
                    encoding="utf-8",
                )
            except OSError as error:
                raise EspIdfError(
                    category="environment",
                    message="项目目标配置写入失败",
                    retryable=False,
                ) from error

            return

        try:
            config_path.write_text(
                "# 由 LUXAR 写入，请勿删除本行。\n"
                f"{expected_line}\n",
                encoding="utf-8",
            )
        except OSError as error:
            raise EspIdfError(
                category="environment",
                message="项目目标配置写入失败",
                retryable=False,
            ) from error

    def _run_create(
        self,
        parent_root: Path,
        project_name: str,
    ) -> tuple[int, str, str]:
        # IDF v5+/v6 语义：--path 指定项目要直接创建的目录，
        # NAME 只用作主源文件名。因此把完整目标目录作为 --path。
        target_dir = parent_root / project_name
        try:
            result = subprocess.run(
                [
                    *self.idf_command,
                    "create-project",
                    "--path",
                    str(target_dir),
                    project_name,
                ],
                cwd=parent_root,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=self.create_timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raw_stdout = _coerce_timeout_output(error.stdout)
            raw_stderr = _coerce_timeout_output(error.stderr)
            return (
                -1,
                _sanitize_output(
                    raw_stdout,
                    parent_root,
                    self.max_summary_chars,
                ),
                _sanitize_output(
                    raw_stderr,
                    parent_root,
                    self.max_summary_chars,
                ),
            )
        except OSError as error:
            raise EspIdfError(
                category="process",
                message="ESP-IDF 进程无法启动",
                retryable=True,
            ) from error

        return (
            result.returncode,
            _sanitize_output(
                result.stdout,
                parent_root,
                self.max_summary_chars,
            ),
            _sanitize_output(
                result.stderr,
                parent_root,
                self.max_summary_chars,
            ),
        )

    def create_project(
        self,
        parent_dir: Path,
        project_name: str,
        target_chip: str,
    ) -> ProjectEvidence:
        self._validate_arguments(project_name, target_chip)
        parent_root = self._resolve_parent(parent_dir)

        target = parent_root / project_name
        _assert_no_link_components(parent_root, target)

        # 项目已存在：验证为合法 ESP-IDF 工程并保证目标配置一致，不重建。
        if target.exists():
            if not target.is_dir():
                raise EspIdfError(
                    category="invalid_project",
                    message="目标路径已存在但不是目录",
                    retryable=False,
                )

            cmake_file = target / "CMakeLists.txt"
            if (
                _is_link_or_junction(cmake_file)
                or not cmake_file.is_file()
            ):
                raise EspIdfError(
                    category="invalid_project",
                    message="目标目录已存在但不是 ESP-IDF 项目",
                    retryable=False,
                )

            self._ensure_target_config(target, target_chip)
            return ProjectEvidence(
                success=True,
                command=_logical_command(project_name),
                return_code=0,
                created_dir=project_name,
                already_existed=True,
            )

        validate_espidf_launcher(self.idf_command)
        return_code, stdout_summary, stderr_summary = self._run_create(
            parent_root,
            project_name,
        )

        if return_code == 0:
            self._ensure_target_config(target, target_chip)
            return ProjectEvidence(
                success=True,
                command=_logical_command(project_name),
                return_code=0,
                created_dir=project_name,
                stdout_summary=stdout_summary,
                stderr_summary=stderr_summary,
            )

        combined = f"{stdout_summary}\n{stderr_summary}".casefold()

        if any(signal in combined for signal in _UNSUPPORTED_SIGNALS):
            raise EspIdfError(
                category="environment",
                message="当前 ESP-IDF 不支持 create-project 命令",
                retryable=False,
            )

        if return_code == -1:
            return ProjectEvidence(
                success=False,
                command=_logical_command(project_name),
                return_code=-1,
                created_dir=project_name,
                stdout_summary=stdout_summary,
                stderr_summary=stderr_summary,
                error_category="timeout",
            )

        return ProjectEvidence(
            success=False,
            command=_logical_command(project_name),
            return_code=return_code,
            created_dir=project_name,
            stdout_summary=stdout_summary,
            stderr_summary=stderr_summary,
            error_category="environment",
        )
