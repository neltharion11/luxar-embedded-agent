from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from luxar.domain.agent.code_changes import (
    ChangeBundle,
    ChangeBundleError,
    FileChange,
    apply_bundle_to_snapshot,
    validate_change_bundle,
)
from luxar.domain.repairs import ProjectFile


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_change_bundle_applies_create_modify_and_delete_in_memory() -> None:
    original = [
        ProjectFile(path="main.c", content="old"),
        ProjectFile(path="remove.h", content="remove"),
    ]
    bundle = ChangeBundle(
        bundle_id="bundle-1",
        task_id="task-1",
        description="更新源码",
        allowed_paths=["main.c", "generated/**"],
        changes=[
            FileChange(
                operation="modify",
                path="main.c",
                content="new",
                expected_sha256=_sha256("old"),
            ),
            FileChange(
                operation="create",
                path="generated/new.c",
                content="generated",
            ),
        ],
    )

    updated = apply_bundle_to_snapshot(original, bundle)

    assert [(item.path, item.content) for item in updated] == [
        ("generated/new.c", "generated"),
        ("main.c", "new"),
        ("remove.h", "remove"),
    ]


def test_nested_glob_does_not_match_sibling_prefix() -> None:
    with pytest.raises(ValidationError):
        ChangeBundle(
            bundle_id="bundle-1",
            task_id="task-1",
            description="越界路径",
            allowed_paths=["components/**"],
            changes=[
                FileChange(
                    operation="create",
                    path="components_backup/main.c",
                    content="unsafe",
                )
            ],
        )


def test_validate_change_bundle_rejects_preserved_gpio_removal() -> None:
    original = [
        ProjectFile(
            path="main.c",
            content="gpio_config_t config = {0};\n"
            "config.pin_bit_mask = 1ULL << GPIO_NUM_13;\n"
            "config.mode = GPIO_MODE_OUTPUT;\n",
        )
    ]
    replacement = (
        "gpio_config_t config = {0};\n"
        "config.pin_bit_mask = 1ULL << GPIO_NUM_33;\n"
        "config.mode = GPIO_MODE_OUTPUT;\n"
    )
    bundle = ChangeBundle(
        bundle_id="bundle-1",
        task_id="task-1",
        description="迁移 GPIO",
        allowed_paths=["main.c"],
        preserves=["gpio.output:P13"],
        changes=[
            FileChange(
                operation="modify",
                path="main.c",
                content=replacement,
                expected_sha256=_sha256(original[0].content),
            )
        ],
    )

    with pytest.raises(ChangeBundleError) as captured:
        validate_change_bundle(original, bundle)

    assert captured.value.category == "preserve_violation"
    assert captured.value.details == ("gpio.output:P13",)
