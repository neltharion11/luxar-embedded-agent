from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from luxar.adapters.local_workspace import LocalWorkspaceAdapter
from luxar.adapters.transactional_code_executor import LocalChangeBundleExecutor
from luxar.domain.agent.code_changes import ChangeBundle, ChangeBundleError, FileChange
from luxar.ports.workspace_errors import WorkspaceError


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _bundle(
    old_content: str,
    new_content: str,
    *,
    include_create: bool = True,
) -> ChangeBundle:
    changes = [
        FileChange(
            operation="modify",
            path="main/main.c",
            content=new_content,
            expected_sha256=_sha256(old_content),
        )
    ]
    if include_create:
        changes.append(
            FileChange(
                operation="create",
                path="components/generated/generated.c",
                content="void generated(void) {}\n",
            )
        )
    return ChangeBundle(
        bundle_id="bundle-1",
        task_id="code-change-1",
        description="应用代码变更",
        allowed_paths=["main/*", "components/**"],
        changes=changes,
    )


def test_executor_commits_create_and_modify_as_one_bundle(tmp_path: Path) -> None:
    main = tmp_path / "main"
    main.mkdir()
    source = main / "main.c"
    source.write_bytes(b"old source\n")

    validation = LocalChangeBundleExecutor().execute(
        tmp_path,
        _bundle("old source\n", "new source\n"),
    )

    assert source.read_text(encoding="utf-8") == "new source\n"
    assert (
        tmp_path / "components" / "generated" / "generated.c"
    ).read_text(encoding="utf-8") == "void generated(void) {}\n"
    assert validation.changed_files == [
        "main/main.c",
        "components/generated/generated.c",
    ]
    assert not list(tmp_path.rglob(".luxar-change-*.tmp"))


def test_executor_rejects_preserve_violation_before_writing(tmp_path: Path) -> None:
    main = tmp_path / "main"
    main.mkdir()
    source = main / "main.c"
    original = (
        "gpio_config_t config = {0};\n"
        "config.pin_bit_mask = 1ULL << GPIO_NUM_13;\n"
        "config.mode = GPIO_MODE_OUTPUT;\n"
    )
    source.write_bytes(original.encode("utf-8"))
    bundle = _bundle(original, "GPIO_NUM_33 GPIO_MODE_OUTPUT\n", include_create=False).model_copy(
        update={"preserves": ["gpio.output:P13"]}
    )

    with pytest.raises(ChangeBundleError) as captured:
        LocalChangeBundleExecutor().execute(tmp_path, bundle)

    assert captured.value.category == "preserve_violation"
    assert source.read_text(encoding="utf-8") == original


def test_executor_rolls_back_all_committed_files_on_commit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main = tmp_path / "main"
    main.mkdir()
    source = main / "main.c"
    source.write_bytes(b"old source\n")
    real_replace = os.replace
    commit_replace_calls = 0

    def fail_once(source_path: str | Path, target_path: str | Path) -> None:
        nonlocal commit_replace_calls
        commit_replace_calls += 1
        if commit_replace_calls == 2:
            raise OSError("SECRET_COMMIT_FAILURE")
        real_replace(source_path, target_path)

    monkeypatch.setattr(
        "luxar.adapters.transactional_code_executor.os.replace",
        fail_once,
    )

    with pytest.raises(WorkspaceError) as captured:
        LocalChangeBundleExecutor().execute(
            tmp_path,
            _bundle("old source\n", "new source\n"),
        )

    assert captured.value.category == "io"
    assert "SECRET_COMMIT_FAILURE" not in captured.value.message
    assert source.read_text(encoding="utf-8") == "old source\n"
    assert not (tmp_path / "components").exists()
    assert not list(tmp_path.rglob(".luxar-*.tmp"))


def test_executor_rejects_stale_hash_without_writing(tmp_path: Path) -> None:
    source = tmp_path / "main.c"
    source.write_bytes(b"actual\n")
    bundle = ChangeBundle(
        bundle_id="bundle-1",
        task_id="task-1",
        description="过期快照",
        allowed_paths=["main.c"],
        changes=[
            FileChange(
                operation="modify",
                path="main.c",
                content="new\n",
                expected_sha256=_sha256("old\n"),
            )
        ],
    )

    with pytest.raises(ChangeBundleError) as captured:
        LocalChangeBundleExecutor(
            LocalWorkspaceAdapter()
        ).execute(tmp_path, bundle)

    assert captured.value.category == "stale_snapshot"
    assert source.read_text(encoding="utf-8") == "actual\n"
