"""ESP-IDF 工具链自动探测与本地配置持久化。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


ToolchainProbe = Callable[[Sequence[str], Path | None], tuple[bool, str]]
ToolchainSource = Literal[
    "none",
    "environment",
    "configured",
    "installer",
    "path",
    "search",
]


@dataclass(frozen=True)
class EspIdfToolchainStatus:
    available: bool
    source: ToolchainSource
    version: str | None
    idf_path: str | None
    message: str


@dataclass(frozen=True)
class _InstallerRecord:
    idf_path: Path
    python: Path
    tools_path: Path
    activation_script: Path | None
    version: str


def _default_probe(
    command: Sequence[str],
    idf_path: Path | None,
) -> tuple[bool, str]:
    environment = os.environ.copy()
    if idf_path is not None:
        environment["IDF_PATH"] = str(idf_path)
    try:
        result = subprocess.run(
            [*command, "--version"],
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""

    output = (result.stdout or result.stderr).strip()
    first_line = output.splitlines()[0][:160] if output else ""
    return result.returncode == 0, first_line


class EspIdfToolchainManager:
    """管理一次 Web 进程使用的、经过实际命令验证的 ESP-IDF。"""

    def __init__(
        self,
        *,
        config_path: Path,
        probe: ToolchainProbe = _default_probe,
        installer_config_paths: Sequence[Path] | None = None,
        idf_search_paths: Sequence[Path] | None = None,
    ) -> None:
        self._config_path = config_path
        self._probe = probe
        self._installer_config_paths = tuple(
            installer_config_paths
            if installer_config_paths is not None
            else self._default_installer_config_paths()
        )
        self._idf_search_paths = tuple(
            idf_search_paths
            if idf_search_paths is not None
            else self._default_idf_search_paths()
        )
        self._lock = threading.RLock()
        self._command: tuple[str, ...] | None = None
        self._status = EspIdfToolchainStatus(
            available=False,
            source="none",
            version=None,
            idf_path=None,
            message="未搜索到可用的 ESP-IDF 环境",
        )
        self.refresh()

    @staticmethod
    def _default_installer_config_paths() -> tuple[Path, ...]:
        configured = os.environ.get("ESPRESSIF_EIM_CONFIG")
        if configured:
            return (Path(configured),)
        candidates = [Path.home() / ".espressif" / "eim_idf.json"]
        if os.name == "nt":
            candidates.insert(0, Path(r"C:\Espressif\tools\eim_idf.json"))
            local_app_data = os.environ.get("LOCALAPPDATA")
            program_data = os.environ.get("PROGRAMDATA")
            if local_app_data:
                candidates.append(
                    Path(local_app_data) / "Espressif" / "tools" / "eim_idf.json"
                )
            if program_data:
                candidates.append(
                    Path(program_data) / "Espressif" / "tools" / "eim_idf.json"
                )
        return tuple(dict.fromkeys(candidates))

    @staticmethod
    def _default_idf_search_paths() -> tuple[Path, ...]:
        candidates = [
            Path.home() / "esp" / "esp-idf",
            Path.home() / "esp-idf",
        ]
        if os.name == "nt":
            frameworks = Path(os.environ.get("SystemDrive", "C:")) / "Espressif" / "frameworks"
            if frameworks.is_dir():
                candidates.extend(sorted(frameworks.glob("esp-idf*"), reverse=True))
        return tuple(dict.fromkeys(candidates))

    def _installer_records(self) -> list[_InstallerRecord]:
        records: list[_InstallerRecord] = []
        for config_path in self._installer_config_paths:
            try:
                payload = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            installations = payload.get("idfInstalled", [])
            if not isinstance(installations, list):
                continue
            selected_id = payload.get("idfSelectedId")
            ordered = sorted(
                (item for item in installations if isinstance(item, dict)),
                key=lambda item: item.get("id") != selected_id,
            )
            for item in ordered:
                raw_path = item.get("path")
                raw_python = item.get("python")
                raw_tools = item.get("idfToolsPath")
                if not all(isinstance(value, str) for value in (raw_path, raw_python, raw_tools)):
                    continue
                raw_script = item.get("activationScript")
                records.append(
                    _InstallerRecord(
                        idf_path=Path(raw_path),
                        python=Path(raw_python),
                        tools_path=Path(raw_tools),
                        activation_script=(
                            Path(raw_script) if isinstance(raw_script, str) else None
                        ),
                        version=str(item.get("name") or ""),
                    )
                )
        return records

    @staticmethod
    def _version_major_minor(version: str) -> str:
        normalized = version.lstrip("v")
        parts = normalized.split(".")
        return ".".join(parts[:2]) if len(parts) >= 2 else normalized

    def _activate_installer_environment(self, record: _InstallerRecord) -> None:
        environment = {
            "IDF_PATH": str(record.idf_path),
            "IDF_TOOLS_PATH": str(record.tools_path),
            "IDF_PYTHON_ENV_PATH": str(record.python.parent.parent),
            "ESP_IDF_VERSION": self._version_major_minor(record.version),
        }
        script = record.activation_script
        if script is not None and script.is_file() and os.name == "nt":
            try:
                content = script.read_text(encoding="utf-8-sig")
                for line in content.splitlines():
                    pair = re.fullmatch(
                        r'\s*"([A-Z][A-Z0-9_]+)"\s*=\s*"([^"]*)"\s*',
                        line,
                    )
                    if pair:
                        environment[pair.group(1)] = pair.group(2)
                    stripped = line.strip()
                    if stripped.startswith('"PATH=') and stripped.endswith('"'):
                        tools_path = stripped[len('"PATH=') : -1]
                        # create_app/测试可能多次刷新工具链。逐项去重，避免每次
                        # 激活都把完整 PATH 再拼一遍并超过 Windows 32767 限制。
                        path_entries = [
                            *tools_path.split(os.pathsep),
                            *os.environ.get("PATH", "").split(os.pathsep),
                        ]
                        unique_entries: list[str] = []
                        seen: set[str] = set()
                        for entry in path_entries:
                            normalized = os.path.normcase(entry.strip())
                            if not normalized or normalized in seen:
                                continue
                            seen.add(normalized)
                            unique_entries.append(entry.strip())
                        environment["PATH"] = os.pathsep.join(unique_entries)
            except (OSError, UnicodeError):
                pass
        os.environ.update(environment)

    @property
    def command(self) -> tuple[str, ...] | None:
        with self._lock:
            return self._command

    @property
    def status(self) -> EspIdfToolchainStatus:
        with self._lock:
            return self._status

    def _read_configured_path(self) -> Path | None:
        try:
            payload = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        raw = payload.get("idf_path") if isinstance(payload, dict) else None
        return Path(raw) if isinstance(raw, str) and raw.strip() else None

    def _write_configured_path(self, idf_path: Path) -> None:
        parent = self._config_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        temporary = self._config_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"idf_path": str(idf_path)},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(self._config_path)

    def _python_candidates(
        self,
        idf_path: Path,
        installer_records: Sequence[_InstallerRecord],
    ) -> list[Path]:
        candidates: list[Path] = []
        candidates.extend(
            record.python
            for record in installer_records
            if record.idf_path.resolve() == idf_path
        )
        configured_env = os.environ.get("IDF_PYTHON_ENV_PATH")
        if configured_env:
            env_root = Path(configured_env)
            candidates.append(
                env_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            )

        tools_root = Path(
            os.environ.get("IDF_TOOLS_PATH", str(Path.home() / ".espressif"))
        )
        python_env_root = tools_root / "python_env"
        if python_env_root.is_dir():
            executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
            candidates.extend(
                env / executable
                for env in sorted(python_env_root.iterdir(), reverse=True)
                if env.is_dir()
            )
        candidates.append(Path(sys.executable))

        unique: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate).casefold()
            if key not in seen and candidate.is_file():
                seen.add(key)
                unique.append(candidate)
        return unique

    def _commands_for_root(
        self,
        idf_path: Path,
        installer_records: Sequence[_InstallerRecord],
    ) -> list[tuple[str, ...]]:
        script = idf_path / "tools" / "idf.py"
        if not script.is_file():
            return []
        return [
            (str(python), str(script))
            for python in self._python_candidates(idf_path, installer_records)
        ]

    def _activate(
        self,
        *,
        command: tuple[str, ...],
        idf_path: Path | None,
        source: ToolchainSource,
        version: str,
    ) -> EspIdfToolchainStatus:
        if idf_path is not None:
            os.environ["IDF_PATH"] = str(idf_path)
            python = Path(command[0])
            if python.is_absolute():
                os.environ["IDF_PYTHON_ENV_PATH"] = str(python.parent.parent)
        self._command = command
        self._status = EspIdfToolchainStatus(
            available=True,
            source=source,
            version=version or "ESP-IDF",
            idf_path=str(idf_path) if idf_path is not None else None,
            message="ESP-IDF 工具链可用",
        )
        return self._status

    def refresh(self) -> EspIdfToolchainStatus:
        with self._lock:
            self._command = None
            installer_records = self._installer_records()
            candidates: list[tuple[Path, ToolchainSource]] = []
            configured = self._read_configured_path()
            if configured is not None:
                candidates.append((configured, "configured"))
            else:
                environment_path = os.environ.get("IDF_PATH")
                if environment_path:
                    candidates.append((Path(environment_path), "environment"))
                candidates.extend(
                    (record.idf_path, "installer")
                    for record in installer_records
                    if all(record.idf_path != item[0] for item in candidates)
                )
                candidates.extend(
                    (path, "search")
                    for path in self._idf_search_paths
                    if all(path != item[0] for item in candidates)
                )

            detected_path: Path | None = None
            for raw_path, source in candidates:
                try:
                    idf_path = raw_path.resolve(strict=True)
                except (OSError, RuntimeError):
                    continue
                if not idf_path.is_dir():
                    continue
                detected_path = detected_path or idf_path
                matching_installer = next(
                    (
                        record
                        for record in installer_records
                        if record.idf_path.resolve() == idf_path
                    ),
                    None,
                )
                if matching_installer is not None:
                    self._activate_installer_environment(matching_installer)
                for command in self._commands_for_root(idf_path, installer_records):
                    available, version = self._probe(command, idf_path)
                    if available:
                        return self._activate(
                            command=command,
                            idf_path=idf_path,
                            source=source,
                            version=version,
                        )

            path_command = shutil.which("idf.py")
            if path_command:
                command = ("idf.py",)
                available, version = self._probe(command, None)
                if available:
                    return self._activate(
                        command=command,
                        idf_path=None,
                        source="path",
                        version=version,
                    )

            self._status = EspIdfToolchainStatus(
                available=False,
                source="configured" if configured is not None else "none",
                version=None,
                idf_path=str(detected_path or configured) if (detected_path or configured) else None,
                message=(
                    "检测到 ESP-IDF 目录，但其 Python 工具环境不可用"
                    if detected_path is not None
                    else "未搜索到可用的 ESP-IDF 环境"
                ),
            )
            return self._status

    def configure(self, selected_path: Path) -> EspIdfToolchainStatus:
        try:
            idf_path = selected_path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ValueError("ESP-IDF 目录无效") from error
        if not idf_path.is_dir() or not (idf_path / "tools" / "idf.py").is_file():
            raise ValueError("所选目录不是 ESP-IDF 根目录")
        with self._lock:
            self._write_configured_path(idf_path)
            return self.refresh()
