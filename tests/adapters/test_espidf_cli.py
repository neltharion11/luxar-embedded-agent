from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from luxar.adapters.espidf_cli import (
    EspIdfCliAdapter,
    _classify_failure,
    _parse_diagnostics,
    _sanitize_output,
)
from luxar.ports.espidf_errors import EspIdfError


def _make_project(root: Path) -> Path:
    root.mkdir(exist_ok=True)
    (root / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n",
        encoding="utf-8",
    )
    return root


def _allow_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "luxar.adapters.espidf_cli.shutil.which",
        lambda command: f"C:/tools/{command}",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reconfigure_timeout_seconds", 0),
        ("build_timeout_seconds", -1),
        ("max_summary_chars", True),
        ("max_manifest_bytes", 0),
        ("max_manifest_total_bytes", -1),
    ],
)
def test_constructor_rejects_invalid_positive_integer_limits(
    field: str,
    value: int | bool,
) -> None:
    with pytest.raises(ValueError, match=f"{field} must be a positive integer"):
        EspIdfCliAdapter(**{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize("command", [(), [], [""], ["python", "  "]])
def test_constructor_rejects_empty_command(command: list[str] | tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="idf_command"):
        EspIdfCliAdapter(idf_command=command)


def test_constructor_rejects_nonboolean_download_authorization() -> None:
    with pytest.raises(ValueError, match="allow_dependency_downloads"):
        EspIdfCliAdapter(allow_dependency_downloads=1)  # type: ignore[arg-type]


def test_constructor_copies_idf_command() -> None:
    command = ["python", "idf.py"]
    adapter = EspIdfCliAdapter(idf_command=command)
    command.append("unsafe")
    assert adapter.idf_command == ("python", "idf.py")


@pytest.mark.parametrize("kind", ["missing", "file", "no_cmake"])
def test_preflight_rejects_invalid_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    _allow_launcher(monkeypatch)
    project = tmp_path / "SECRET_PROJECT"
    if kind == "file":
        project.write_text("not a directory", encoding="utf-8")
    elif kind == "no_cmake":
        project.mkdir()

    with pytest.raises(EspIdfError) as captured:
        EspIdfCliAdapter()._preflight(project)

    assert captured.value.category == "invalid_project"
    assert str(project) not in captured.value.message


def test_preflight_rejects_missing_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _make_project(tmp_path / "project")
    monkeypatch.setattr("luxar.adapters.espidf_cli.shutil.which", lambda _: None)

    with pytest.raises(EspIdfError) as captured:
        EspIdfCliAdapter()._preflight(project)

    assert captured.value.category == "environment"


def test_preflight_rejects_missing_absolute_command_file(tmp_path: Path) -> None:
    project = _make_project(tmp_path / "project")
    missing = (tmp_path / "SECRET" / "idf.py").resolve()

    with pytest.raises(EspIdfError) as captured:
        EspIdfCliAdapter(idf_command=(str(missing),))._preflight(project)

    assert captured.value.category == "environment"
    assert str(missing) not in captured.value.message


@pytest.mark.parametrize(
    "content",
    ["", "version: 1.0.0\n", "dependencies: {}\n"],
)
def test_preflight_allows_manifest_without_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")
    (project / "idf_component.yml").write_text(content, encoding="utf-8")

    root, environment = EspIdfCliAdapter()._preflight(project)

    assert root == project.resolve()
    assert environment["IDF_COMPONENT_MANAGER"] == "0"
    assert environment["IDF_COMPONENT_NO_COLORS"] == "1"
    assert environment["IDF_COMPONENT_NO_HINTS"] == "1"


def test_preflight_rejects_declared_dependencies_before_any_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")
    component = project / "components" / "sensor"
    component.mkdir(parents=True)
    (component / "idf_component.yml").write_text(
        "dependencies:\n  espressif/led_strip: '>=2.0'\n",
        encoding="utf-8",
    )

    with pytest.raises(EspIdfError) as captured:
        EspIdfCliAdapter()._preflight(project)

    assert captured.value.category == "dependency"
    assert captured.value.retryable is False


def test_preflight_allows_declared_dependencies_when_explicitly_authorized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    monkeypatch.setenv("IDF_COMPONENT_MANAGER", "0")
    project = _make_project(tmp_path / "project")
    (project / "idf_component.yml").write_text(
        "dependencies:\n  espressif/led_strip: '*'\n",
        encoding="utf-8",
    )

    _, environment = EspIdfCliAdapter(
        allow_dependency_downloads=True
    )._preflight(project)

    assert "IDF_COMPONENT_MANAGER" not in environment


@pytest.mark.parametrize(
    "directory_name",
    [".git", ".vscode", ".hidden", "build", "build_esp32", "managed_components"],
)
def test_preflight_does_not_scan_excluded_manifest_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    directory_name: str,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")
    excluded = project / directory_name
    excluded.mkdir()
    (excluded / "idf_component.yml").write_text(
        "dependencies:\n  secret/component: '*'\n",
        encoding="utf-8",
    )

    _, environment = EspIdfCliAdapter()._preflight(project)
    assert environment["IDF_COMPONENT_MANAGER"] == "0"


@pytest.mark.parametrize(
    "data",
    [b"\xff\xfe", b"bad\x00yaml", b"[unterminated", b"- item\n", b"dependencies: []\n"],
)
def test_preflight_rejects_invalid_manifest_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    data: bytes,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "SECRET_PROJECT")
    manifest = project / "idf_component.yml"
    manifest.write_bytes(data)

    with pytest.raises(EspIdfError) as captured:
        EspIdfCliAdapter()._preflight(project)

    assert captured.value.category == "invalid_project"
    assert str(project) not in captured.value.message
    assert "SECRET" not in captured.value.message


def test_preflight_enforces_manifest_file_and_total_byte_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")
    (project / "idf_component.yml").write_text("version: 123\n", encoding="utf-8")

    with pytest.raises(EspIdfError) as file_error:
        EspIdfCliAdapter(max_manifest_bytes=3)._preflight(project)
    assert file_error.value.category == "invalid_project"

    component = project / "component"
    component.mkdir()
    (component / "idf_component.yml").write_text("version: 456\n", encoding="utf-8")
    with pytest.raises(EspIdfError) as total_error:
        EspIdfCliAdapter(max_manifest_total_bytes=15)._preflight(project)
    assert total_error.value.category == "invalid_project"


def test_preflight_counts_bytes_actually_read_from_manifests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")
    (project / "idf_component.yml").write_text("{}\n", encoding="utf-8")
    component = project / "component"
    component.mkdir()
    (component / "idf_component.yml").write_text("{}\n", encoding="utf-8")

    adapter = EspIdfCliAdapter(max_manifest_total_bytes=15)
    monkeypatch.setattr(adapter, "_read_manifest", lambda _path: ({}, 10))

    with pytest.raises(EspIdfError, match="总量"):
        adapter._preflight(project)


def _create_directory_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlink unavailable: {type(error).__name__}")


def _create_junction_or_skip(link: Path, target: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows junction test")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("Windows junction creation unavailable")


@pytest.mark.parametrize("link_kind", ["symlink", "junction"])
def test_preflight_rejects_linked_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    link_kind: str,
) -> None:
    _allow_launcher(monkeypatch)
    target = _make_project(tmp_path / "target")
    link = tmp_path / "project-link"
    if link_kind == "symlink":
        _create_directory_symlink_or_skip(link, target)
    else:
        _create_junction_or_skip(link, target)

    with pytest.raises(EspIdfError) as captured:
        EspIdfCliAdapter()._preflight(link)
    assert captured.value.category == "invalid_project"


@pytest.mark.parametrize("link_kind", ["symlink", "junction"])
def test_preflight_rejects_linked_manifest_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    link_kind: str,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "idf_component.yml").write_text("dependencies: {}\n", encoding="utf-8")
    link = project / "component-link"
    if link_kind == "symlink":
        _create_directory_symlink_or_skip(link, outside)
    else:
        _create_junction_or_skip(link, outside)

    with pytest.raises(EspIdfError) as captured:
        EspIdfCliAdapter()._preflight(project)
    assert captured.value.category == "invalid_project"


def test_build_runs_reconfigure_then_build_and_returns_logical_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Project build complete",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = EspIdfCliAdapter(
        idf_command=("trusted-python", "trusted-idf.py"),
        reconfigure_timeout_seconds=11,
        build_timeout_seconds=22,
    )

    evidence = adapter.build(project)

    assert [call[0] for call in calls] == [
        ["trusted-python", "trusted-idf.py", "reconfigure"],
        ["trusted-python", "trusted-idf.py", "build"],
    ]
    assert calls[0][1]["timeout"] == 11
    assert calls[1][1]["timeout"] == 22
    assert evidence.success is True
    assert evidence.command == ["idf.py", "build"]
    assert evidence.return_code == 0


def test_reconfigure_failure_prevents_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 2, stdout="", stderr="failure")

    monkeypatch.setattr(subprocess, "run", fake_run)

    evidence = EspIdfCliAdapter().build(project)

    assert calls == [["idf.py", "reconfigure"]]
    assert evidence.success is False
    assert evidence.command == ["idf.py", "reconfigure"]
    assert evidence.return_code == 2
    assert evidence.error_category == "unknown"


def test_build_uses_safe_subprocess_arguments_and_copied_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")
    original_environment = os.environ.copy()
    calls: list[dict[str, object]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    EspIdfCliAdapter().build(project)

    assert os.environ == original_environment
    for kwargs in calls:
        assert kwargs["cwd"] == project.resolve()
        assert kwargs["shell"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        assert kwargs["check"] is False
        assert kwargs["env"] is not os.environ


@pytest.mark.parametrize("action", ["reconfigure", "build"])
def test_timeout_returns_evidence_and_stops_current_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")
    call_count = 0

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        nonlocal call_count
        call_count += 1
        if command[-1] == action:
            raise subprocess.TimeoutExpired(
                command,
                timeout=1,
                output=b"partial stdout",
                stderr=b"partial stderr",
            )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    evidence = EspIdfCliAdapter().build(project)

    assert evidence.success is False
    assert evidence.command == ["idf.py", action]
    assert evidence.return_code == -1
    assert evidence.error_category == "timeout"
    assert "partial stdout" in evidence.stdout_summary
    assert call_count == (1 if action == "reconfigure" else 2)


def test_process_start_failure_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "project")

    def fake_run(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        raise OSError("SECRET_EXECUTABLE_PATH")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(EspIdfError) as captured:
        EspIdfCliAdapter().build(project)

    assert captured.value.category == "process"
    assert "SECRET_EXECUTABLE_PATH" not in captured.value.message


@pytest.mark.parametrize(
    ("action", "stdout", "stderr", "expected"),
    [
        ("reconfigure", "CMake Error", "Failed to resolve component", "dependency"),
        ("reconfigure", "", "Could not find Ninja", "environment"),
        ("build", "error", "undefined reference to `app_wifi_init`", "linker"),
        ("build", "main/main.c:4:2: error: broken", "", "source"),
        ("build", "", "unrecognized failure", "unknown"),
    ],
)
def test_failure_classification_uses_stable_priority(
    action: str,
    stdout: str,
    stderr: str,
    expected: str,
) -> None:
    assert _classify_failure(action, stdout, stderr) == expected


def test_parse_diagnostics_extracts_locations_and_deduplicates(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    windows_root = str(root).replace("/", "\\")
    gcc_line = (
        f"{windows_root}\\main\\main.c:42:17: error: expected ';'\n"
    )
    text = (
        gcc_line
        + "main/component.cpp:8: warning: unused variable\n"
        + "CMake Error at main/CMakeLists.txt:12 (idf_component_register):\n"
        + "  Missing source file\n"
        + gcc_line
    )

    diagnostics = _parse_diagnostics(text, root)

    assert [item.model_dump() for item in diagnostics] == [
        {
            "file": "main/main.c",
            "line": 42,
            "column": 17,
            "severity": "error",
            "code": None,
            "message": "expected ';'",
        },
        {
            "file": "main/component.cpp",
            "line": 8,
            "column": None,
            "severity": "warning",
            "code": None,
            "message": "unused variable",
        },
        {
            "file": "main/CMakeLists.txt",
            "line": 12,
            "column": None,
            "severity": "error",
            "code": None,
            "message": "Missing source file",
        },
    ]


def test_parse_diagnostics_omits_external_absolute_file(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    diagnostics = _parse_diagnostics(
        "C:/Espressif/framework/components/foo.c:7:3: error: external failure",
        root,
    )
    assert diagnostics[0].file is None


def test_sanitize_output_removes_ansi_paths_and_truncates(tmp_path: Path) -> None:
    root = tmp_path / "SECRET_PROJECT"
    root.mkdir()
    text = (
        f"\x1b[31m{root / 'main' / 'main.c'}: error\x1b[0m\r\n"
        "C:/Espressif/SECRET_TOOL/tool.py failed\r\n"
        + ("x" * 100)
    )

    sanitized = _sanitize_output(text, root, 80)

    assert "\x1b" not in sanitized
    assert "\r" not in sanitized
    assert str(root) not in sanitized
    assert "SECRET_TOOL" not in sanitized
    assert "main/main.c" in sanitized
    assert len(sanitized) <= 80


def test_failed_build_uses_classification_diagnostics_and_sanitized_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    project = _make_project(tmp_path / "SECRET_PROJECT")
    absolute_source = project / "main" / "main.c"

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if command[-1] == "reconfigure":
            return subprocess.CompletedProcess(command, 0, stdout="configured", stderr="")
        return subprocess.CompletedProcess(
            command,
            2,
            stdout="",
            stderr=f"\x1b[31m{absolute_source}:9:4: error: broken source\x1b[0m",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    evidence = EspIdfCliAdapter(max_summary_chars=200).build(project)

    assert evidence.error_category == "source"
    assert evidence.diagnostics[0].file == "main/main.c"
    assert evidence.diagnostics[0].line == 9
    assert str(project) not in evidence.stderr_summary
    assert "\x1b" not in evidence.stderr_summary
