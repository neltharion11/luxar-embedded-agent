from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from luxar.adapters.local_workspace import LocalWorkspaceAdapter
from luxar.domain.repairs import FileReplacement, RepairPlan
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


def test_read_project_files_reports_per_file_sha256_of_disk_bytes(
    tmp_path: Path,
) -> None:
    """回归：read_project 每个文件必须带磁盘原始字节的 SHA-256（小写 hex），
    且与 apply_change_bundle 的 expected_sha256 校验基准（transactional_
    code_executor._content_hash 对原始字节）一致，让代理无需向用户索要哈希。"""
    import hashlib

    main_directory = tmp_path / "main"
    main_directory.mkdir()
    raw = "void app_main(void) {}\n".encode("utf-8")
    (main_directory / "main.c").write_bytes(raw)

    files = LocalWorkspaceAdapter().read_project_files(tmp_path)

    entry = next(file for file in files if file.path == "main/main.c")
    expected = hashlib.sha256(raw).hexdigest()
    assert entry.sha256 == expected
    assert entry.sha256 == entry.sha256.lower()
    # 与事务执行器对同一文件的字节哈希一致（直接引用其基准函数）
    from luxar.adapters.transactional_code_executor import _content_hash

    assert entry.sha256 == _content_hash(raw)


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


def _make_repair(
    *replacements: tuple[str, str],
) -> RepairPlan:
    return RepairPlan(
        diagnosis="修复编译错误",
        replacements=[
            FileReplacement(path=path, content=content)
            for path, content in replacements
        ],
    )


def _staging_files(project: Path) -> list[Path]:
    return list(project.rglob(".luxar-*.tmp"))


def test_apply_repair_replaces_one_existing_file(tmp_path: Path) -> None:
    source = tmp_path / "main.c"
    source.write_bytes(b"broken source")
    repair = _make_repair(("main.c", "fixed source"))

    changed_files = LocalWorkspaceAdapter().apply_repair(tmp_path, repair)

    assert changed_files == ["main.c"]
    assert source.read_bytes() == b"fixed source"
    assert _staging_files(tmp_path) == []


def test_apply_repair_preserves_plan_order_and_unlisted_files(
    tmp_path: Path,
) -> None:
    main_directory = tmp_path / "main"
    main_directory.mkdir()
    first = main_directory / "first.c"
    second = main_directory / "second.h"
    untouched = main_directory / "untouched.cpp"
    first.write_bytes(b"old first")
    second.write_bytes(b"old second")
    untouched.write_bytes(b"keep me")
    repair = _make_repair(
        ("main/second.h", "new second"),
        ("main/first.c", "new first"),
    )

    changed_files = LocalWorkspaceAdapter().apply_repair(tmp_path, repair)

    assert changed_files == ["main/second.h", "main/first.c"]
    assert first.read_bytes() == b"new first"
    assert second.read_bytes() == b"new second"
    assert untouched.read_bytes() == b"keep me"
    assert _staging_files(tmp_path) == []


def test_apply_repair_does_not_create_missing_target(tmp_path: Path) -> None:
    repair = _make_repair(("main.c", "new source"))

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter().apply_repair(tmp_path, repair)

    assert captured.value.category == "invalid_project"
    assert not (tmp_path / "main.c").exists()
    assert _staging_files(tmp_path) == []


def test_apply_repair_rejects_directory_as_target(tmp_path: Path) -> None:
    (tmp_path / "component.c").mkdir()
    repair = _make_repair(("component.c", "new source"))

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter().apply_repair(tmp_path, repair)

    assert captured.value.category == "invalid_project"
    assert _staging_files(tmp_path) == []


@pytest.mark.parametrize(
    "relative_path",
    [
        "notes.txt",
        "sdkconfig",
        "dependencies.lock",
        "managed_components/vendor/main.c",
        "build/generated.c",
        ".hidden/secret.c",
    ],
)
def test_apply_repair_rejects_unsupported_or_excluded_target(
    tmp_path: Path,
    relative_path: str,
) -> None:
    target = tmp_path.joinpath(*relative_path.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"original")
    repair = _make_repair((relative_path, "replacement"))

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter().apply_repair(tmp_path, repair)

    assert captured.value.category == "unsupported_file"
    assert target.read_bytes() == b"original"
    assert _staging_files(tmp_path) == []


def test_apply_repair_rejects_allowlisted_file_symlink(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    link = tmp_path / "main.c"
    _create_file_symlink_or_skip(link, outside)
    repair = _make_repair(("main.c", "replacement"))

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter().apply_repair(tmp_path, repair)

    assert captured.value.category == "unsafe_path"
    assert outside.read_bytes() == b"outside"


def test_apply_repair_rejects_junction_directory_component(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside-component"
    outside.mkdir()
    outside_source = outside / "main.c"
    outside_source.write_bytes(b"outside")
    project = tmp_path / "project"
    project.mkdir()
    junction = project / "main"
    _create_junction_or_skip(junction, outside)
    repair = _make_repair(("main/main.c", "replacement"))

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter().apply_repair(project, repair)

    assert captured.value.category == "unsafe_path"
    assert outside_source.read_bytes() == b"outside"


def test_apply_repair_enforces_replacement_file_byte_limit(
    tmp_path: Path,
) -> None:
    source = tmp_path / "main.c"
    source.write_bytes(b"old")
    repair = _make_repair(("main.c", "1234"))

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter(max_file_bytes=3).apply_repair(
            tmp_path,
            repair,
        )

    assert captured.value.category == "file_too_large"
    assert source.read_bytes() == b"old"


def test_apply_repair_counts_replacement_utf8_bytes(tmp_path: Path) -> None:
    source = tmp_path / "main.c"
    source.write_bytes(b"old")
    repair = _make_repair(("main.c", "中"))

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter(max_file_bytes=2).apply_repair(
            tmp_path,
            repair,
        )

    assert captured.value.category == "file_too_large"
    assert source.read_bytes() == b"old"


def test_apply_repair_enforces_replacement_total_byte_limit(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.c").write_bytes(b"old")
    (tmp_path / "b.c").write_bytes(b"old")
    repair = _make_repair(("a.c", "123"), ("b.c", "456"))

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter(max_total_bytes=5).apply_repair(
            tmp_path,
            repair,
        )

    assert captured.value.category == "context_too_large"
    assert (tmp_path / "a.c").read_bytes() == b"old"
    assert (tmp_path / "b.c").read_bytes() == b"old"


def test_apply_repair_enforces_original_file_byte_limit(
    tmp_path: Path,
) -> None:
    source = tmp_path / "main.c"
    source.write_bytes(b"1234")
    repair = _make_repair(("main.c", "new"))

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter(max_file_bytes=3).apply_repair(
            tmp_path,
            repair,
        )

    assert captured.value.category == "file_too_large"
    assert source.read_bytes() == b"1234"


def test_apply_repair_rejects_binary_original_file(tmp_path: Path) -> None:
    source = tmp_path / "main.c"
    source.write_bytes(b"before\x00after")
    repair = _make_repair(("main.c", "new source"))

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter().apply_repair(tmp_path, repair)

    assert captured.value.category == "invalid_encoding"
    assert source.read_bytes() == b"before\x00after"


def test_apply_repair_rejects_nul_in_replacement(tmp_path: Path) -> None:
    source = tmp_path / "main.c"
    source.write_bytes(b"original")
    repair = _make_repair(("main.c", "before\x00after"))

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter().apply_repair(tmp_path, repair)

    assert captured.value.category == "invalid_encoding"
    assert source.read_bytes() == b"original"


def test_apply_repair_validates_every_target_before_staging(
    tmp_path: Path,
) -> None:
    valid = tmp_path / "valid.c"
    valid.write_bytes(b"original")
    repair = _make_repair(
        ("valid.c", "changed"),
        ("missing.c", "new file"),
    )

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter().apply_repair(tmp_path, repair)

    assert captured.value.category == "invalid_project"
    assert valid.read_bytes() == b"original"
    assert not (tmp_path / "missing.c").exists()
    assert _staging_files(tmp_path) == []


def test_apply_repair_rollback_restores_committed_files_in_reverse_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.c"
    second = tmp_path / "second.c"
    third = tmp_path / "third.c"
    first.write_bytes(b"old first")
    second.write_bytes(b"old second")
    third.write_bytes(b"old third")
    repair = _make_repair(
        ("first.c", "new first"),
        ("second.c", "new second"),
        ("third.c", "new third"),
    )
    real_replace = os.replace
    destination_names: list[str] = []
    sensitive_marker = "SECRET_OS_FAILURE_123"

    def fail_third_replace(
        source: str | Path,
        destination: str | Path,
    ) -> None:
        destination_names.append(Path(destination).name)
        if len(destination_names) == 3:
            raise OSError(sensitive_marker)
        real_replace(source, destination)

    monkeypatch.setattr(
        "luxar.adapters.local_workspace.os.replace",
        fail_third_replace,
    )

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter().apply_repair(tmp_path, repair)

    assert captured.value.category == "io"
    assert captured.value.retryable is True
    assert sensitive_marker not in captured.value.message
    assert first.read_bytes() == b"old first"
    assert second.read_bytes() == b"old second"
    assert third.read_bytes() == b"old third"
    assert destination_names == [
        "first.c",
        "second.c",
        "third.c",
        "second.c",
        "first.c",
    ]
    assert _staging_files(tmp_path) == []


def test_apply_repair_reports_sanitized_rollback_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.c"
    second = tmp_path / "second.c"
    first.write_bytes(b"old first")
    second.write_bytes(b"old second")
    repair = _make_repair(
        ("first.c", "new first"),
        ("second.c", "new second"),
    )
    real_replace = os.replace
    replace_calls = 0
    sensitive_marker = "SECRET_ROLLBACK_FAILURE_456"

    def fail_commit_and_rollback(
        source: str | Path,
        destination: str | Path,
    ) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls >= 2:
            raise OSError(sensitive_marker)
        real_replace(source, destination)

    monkeypatch.setattr(
        "luxar.adapters.local_workspace.os.replace",
        fail_commit_and_rollback,
    )

    with pytest.raises(WorkspaceError) as captured:
        LocalWorkspaceAdapter().apply_repair(tmp_path, repair)

    assert captured.value.category == "rollback_failed"
    assert captured.value.retryable is False
    assert sensitive_marker not in captured.value.message
    assert str(tmp_path) not in captured.value.message
    assert _staging_files(tmp_path) == []
