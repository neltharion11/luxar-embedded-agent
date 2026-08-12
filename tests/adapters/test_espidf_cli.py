from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from luxar.adapters.espidf_cli import EspIdfCliAdapter
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
