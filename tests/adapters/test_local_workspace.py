from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from luxar.adapters.local_workspace import LocalWorkspaceAdapter
from luxar.ports.workspace_errors import WorkspaceError


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_file_bytes", 0),
        ("max_file_bytes", -1),
        ("max_file_bytes", True),
        ("max_total_bytes", 0),
        ("max_total_bytes", -1),
        ("max_total_bytes", True),
    ],
)
def test_constructor_rejects_nonpositive_or_boolean_limits(
    field: str,
    value: int | bool,
) -> None:
    arguments = {field: value}

    with pytest.raises(ValueError, match=f"{field} must be a positive integer"):
        LocalWorkspaceAdapter(**arguments)  # type: ignore[arg-type]


def test_read_project_files_returns_allowed_files_in_path_order(
    tmp_path: Path,
) -> None:
    main_directory = tmp_path / "main"
    main_directory.mkdir()
    (main_directory / "main.c").write_bytes(
        "// 中文\nvoid app_main(void) {}\n".encode("utf-8")
    )
    (main_directory / "startup.S").write_text(
        "nop\n",
        encoding="utf-8",
    )
    (tmp_path / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n",
        encoding="utf-8",
    )

    files = LocalWorkspaceAdapter().read_project_files(tmp_path)

    assert [file.path for file in files] == [
        "CMakeLists.txt",
        "main/main.c",
        "main/startup.S",
    ]
    assert files[1].content == "// 中文\nvoid app_main(void) {}\n"


@pytest.mark.parametrize(
    "directory_name",
    [
        ".git",
        ".vscode",
        ".idea",
        ".hidden",
        "build",
        "build_esp32",
        "managed_components",
        "__pycache__",
    ],
)
def test_read_project_files_does_not_enter_excluded_directories(
    tmp_path: Path,
    directory_name: str,
) -> None:
    excluded = tmp_path / directory_name
    excluded.mkdir()
    (excluded / "ignored.c").write_text("ignored", encoding="utf-8")
    (tmp_path / "kept.h").write_text("kept", encoding="utf-8")

    files = LocalWorkspaceAdapter().read_project_files(tmp_path)

    assert [file.path for file in files] == ["kept.h"]


@pytest.mark.parametrize(
    "file_name",
    ["notes.txt", "sdkconfig", "dependencies.lock", "firmware.bin"],
)
def test_read_project_files_omits_files_outside_allowlist(
    tmp_path: Path,
    file_name: str,
) -> None:
    (tmp_path / file_name).write_text("ignored", encoding="utf-8")
    (tmp_path / "main.cpp").write_text("kept", encoding="utf-8")

    files = LocalWorkspaceAdapter().read_project_files(tmp_path)

    assert [file.path for file in files] == ["main.cpp"]


def test_read_project_files_rejects_nul_bytes(tmp_path: Path) -> None:
    (tmp_path / "main.c").write_bytes(b"before\x00after")

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter().read_project_files(tmp_path)

    assert captured.value.category == "invalid_encoding"
    assert captured.value.retryable is False


def test_read_project_files_rejects_invalid_utf8(tmp_path: Path) -> None:
    (tmp_path / "main.c").write_bytes(b"\xff\xfe")

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter().read_project_files(tmp_path)

    assert captured.value.category == "invalid_encoding"
    assert captured.value.retryable is False


def test_read_project_files_enforces_actual_file_byte_limit(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.c").write_text("1234", encoding="utf-8")

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter(max_file_bytes=3).read_project_files(tmp_path)

    assert captured.value.category == "file_too_large"
    assert captured.value.retryable is False


def test_read_project_files_counts_utf8_bytes_not_characters(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.c").write_text("中", encoding="utf-8")

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter(max_file_bytes=2).read_project_files(tmp_path)

    assert captured.value.category == "file_too_large"


def test_read_project_files_enforces_total_byte_limit(tmp_path: Path) -> None:
    (tmp_path / "a.c").write_text("123", encoding="utf-8")
    (tmp_path / "b.c").write_text("456", encoding="utf-8")

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter(max_total_bytes=5).read_project_files(tmp_path)

    assert captured.value.category == "context_too_large"
    assert captured.value.retryable is False


def test_read_project_files_rejects_nonexistent_project_without_path_leak(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "SECRET_PROJECT_PATH" / "missing"

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter().read_project_files(missing)

    assert captured.value.category == "invalid_project"
    assert captured.value.retryable is False
    assert str(missing) not in captured.value.message


def test_read_project_files_rejects_file_as_project_root(
    tmp_path: Path,
) -> None:
    project_file = tmp_path / "main.c"
    project_file.write_text("source", encoding="utf-8")

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter().read_project_files(project_file)

    assert captured.value.category == "invalid_project"


def _create_directory_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlink unavailable: {type(error).__name__}")


def _create_file_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=False)
    except OSError as error:
        pytest.skip(f"file symlink unavailable: {type(error).__name__}")


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


def test_read_project_files_rejects_symlink_project_root(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "main.c").write_text("source", encoding="utf-8")
    link = tmp_path / "project-link"
    _create_directory_symlink_or_skip(link, target)

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter().read_project_files(link)

    assert captured.value.category == "unsafe_path"


def test_read_project_files_rejects_allowlisted_file_symlink(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = tmp_path / "main.c"
    _create_file_symlink_or_skip(link, outside)

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter().read_project_files(tmp_path)

    assert captured.value.category == "unsafe_path"


def test_read_project_files_rejects_symlink_directory_component(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "main.c").write_text("outside", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    link = project / "main"
    _create_directory_symlink_or_skip(link, outside)

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter().read_project_files(project)

    assert captured.value.category == "unsafe_path"


def test_read_project_files_rejects_junction_project_root(
    tmp_path: Path,
) -> None:
    target = tmp_path / "junction-target"
    target.mkdir()
    (target / "main.c").write_text("source", encoding="utf-8")
    junction = tmp_path / "project-junction"
    _create_junction_or_skip(junction, target)

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter().read_project_files(junction)

    assert captured.value.category == "unsafe_path"


def test_read_project_files_rejects_junction_directory_component(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "junction-outside"
    outside.mkdir()
    (outside / "main.c").write_text("outside", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    junction = project / "main"
    _create_junction_or_skip(junction, outside)

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter().read_project_files(project)

    assert captured.value.category == "unsafe_path"
