"""CLI 展示边界测试：不调用真实 DeepSeek 或 ESP-IDF。"""

from __future__ import annotations

from pathlib import Path

import pytest

from luxar import cli
from luxar.application.state import WorkflowState


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["run"],
        ["run", "--project", "project", "--max-attempts", "0"],
        ["run", "--project", "project", "--max-attempts", "-1"],
        ["run", "--project", "project", "--unknown"],
    ],
)
def test_parser_rejects_invalid_command_lines(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as captured:
        cli.build_parser().parse_args(argv)

    assert captured.value.code == 2


def test_main_rejects_invalid_project_inputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing"
    assert cli.main(["run", "--project", str(missing), "--task", "build"]) == 2
    assert "项目路径不存在或不是目录" in capsys.readouterr().err

    file_path = tmp_path / "file"
    file_path.write_text("not a directory", encoding="utf-8")
    assert cli.main(["run", "--project", str(file_path), "--task", "build"]) == 2
    assert "项目路径不存在或不是目录" in capsys.readouterr().err


def test_json_mode_requires_task_without_calling_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "builtins.input",
        lambda _: (_ for _ in ()).throw(AssertionError("input must not run")),
    )

    result = cli.main(["run", "--project", str(tmp_path), "--json"])

    assert result == 2
    assert "JSON 模式必须提供 --task" in capsys.readouterr().err


def test_ordinary_mode_prompts_for_missing_task_and_builds_initial_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}
    context = object()

    monkeypatch.setattr("builtins.input", lambda prompt: "  闪烁 GPIO 2  ")

    def fake_bootstrap(**kwargs: object) -> object:
        calls["bootstrap"] = kwargs
        return context

    def fake_runner(**kwargs: object) -> WorkflowState:
        calls["runner"] = kwargs
        return WorkflowState(status="completed", attempts=1, trace=[])

    monkeypatch.setattr(cli, "build_deepseek_runtime_context", fake_bootstrap)
    monkeypatch.setattr(cli, "run_workflow", fake_runner)

    result = cli.main(
        ["run", "--project", str(tmp_path), "--max-attempts", "5"]
    )

    assert result == 0
    assert calls["bootstrap"] == {
        "project_path": tmp_path,
        "allow_dependency_downloads": False,
    }
    runner_call = calls["runner"]
    assert isinstance(runner_call, dict)
    assert runner_call["context"] is context
    assert runner_call["initial_state"] == {
        "task_text": "闪烁 GPIO 2",
        "attempts": 0,
        "max_attempts": 5,
        "trace": [],
    }
    assert runner_call["progress_reporter"] is not None


def test_explicit_dependency_authorization_reaches_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, object] = {}

    def fake_bootstrap(**kwargs: object) -> object:
        received.update(kwargs)
        return object()

    monkeypatch.setattr(cli, "build_deepseek_runtime_context", fake_bootstrap)
    monkeypatch.setattr(
        cli,
        "run_workflow",
        lambda **_: WorkflowState(status="completed", trace=[]),
    )

    result = cli.main(
        [
            "run",
            "--project",
            str(tmp_path),
            "--task",
            "build",
            "--allow-dependency-downloads",
        ]
    )

    assert result == 0
    assert received["allow_dependency_downloads"] is True


def test_known_startup_error_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_bootstrap(**_: object) -> object:
        raise ValueError("SECRET_CONFIGURATION_DETAIL")

    monkeypatch.setattr(cli, "build_deepseek_runtime_context", fail_bootstrap)

    result = cli.main(
        ["run", "--project", str(tmp_path), "--task", "build"]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "运行配置无效" in captured.err
    assert "SECRET_CONFIGURATION_DETAIL" not in captured.err


def test_keyboard_interrupt_returns_130(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "builtins.input",
        lambda _: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    result = cli.main(["run", "--project", str(tmp_path)])

    assert result == 130
    assert "操作已取消" in capsys.readouterr().err
