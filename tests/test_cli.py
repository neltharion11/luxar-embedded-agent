"""CLI 展示边界测试：不调用真实 DeepSeek 或 ESP-IDF。"""

from __future__ import annotations

from pathlib import Path

import pytest

from luxar import cli
from luxar.application.state import WorkflowState
from luxar.domain.errors import WorkflowError
from luxar.domain.evidence import BuildEvidence
from luxar.domain.requirements import FirmwareRequirement


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
    missing_parent = tmp_path / "missing-parent" / "blink"
    assert (
        cli.main(
            [
                "run",
                "--project",
                str(missing_parent),
                "--task",
                "build",
            ]
        )
        == 2
    )
    assert "项目父目录不存在或不是目录" in capsys.readouterr().err

    file_path = tmp_path / "file"
    file_path.write_text("not a directory", encoding="utf-8")
    assert cli.main(["run", "--project", str(file_path), "--task", "build"]) == 2
    assert "项目路径已存在但不是目录" in capsys.readouterr().err


def test_main_allows_not_yet_existing_project_for_creation_tasks(
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
            str(tmp_path / "blink"),
            "--task",
            "create blink",
            "--target",
            "esp32s3",
        ]
    )

    assert result == 0
    assert received["project_path"] == tmp_path / "blink"
    assert received["target_chip"] == "esp32s3"


@pytest.mark.parametrize(
    "target",
    ["ESP32", "esp32;idf.py", "esp-32", "esp32 "],
)
def test_cli_rejects_invalid_target_chip_values(
    tmp_path: Path,
    target: str,
) -> None:
    with pytest.raises(SystemExit) as captured:
        cli.build_parser().parse_args(
            ["run", "--project", str(tmp_path), "--target", target]
        )

    assert captured.value.code == 2


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


def test_main_rejects_whitespace_only_task_before_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "build_deepseek_runtime_context",
        lambda **_: (_ for _ in ()).throw(AssertionError("bootstrap must not run")),
    )

    result = cli.main(
        ["run", "--project", str(tmp_path), "--task", "   "]
    )

    assert result == 2
    assert "固件需求不能为空" in capsys.readouterr().err


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
        "target_chip": None,
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


@pytest.mark.parametrize(
    ("state", "expected_code", "expected_text"),
    [
        (
            WorkflowState(
                status="completed",
                attempts=2,
                changed_files=["main/main.c"],
                build_evidence=BuildEvidence(
                    success=True,
                    command=["idf.py", "build"],
                    return_code=0,
                ),
                trace=[],
            ),
            0,
            ["LUXAR 执行成功", "构建次数：2", "main/main.c", "idf.py build"],
        ),
        (
            WorkflowState(
                status="needs_clarification",
                requirement=FirmwareRequirement(
                    target="esp32",
                    feature="gpio_blink",
                    missing_fields=["gpio"],
                ),
                trace=[],
            ),
            3,
            ["LUXAR 需要更多信息", "缺少字段", "gpio"],
        ),
        (
            WorkflowState(
                status="failed",
                error=WorkflowError(
                    stage="build",
                    category="dependency",
                    message="项目依赖需要显式授权后才能解析",
                    retryable=False,
                    user_suggestion="请确认依赖来源后显式允许依赖下载",
                ),
                trace=[],
            ),
            4,
            ["LUXAR 执行失败", "阶段：build", "类别：dependency", "建议："],
        ),
    ],
)
def test_human_output_and_exit_code_by_final_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    state: WorkflowState,
    expected_code: int,
    expected_text: list[str],
) -> None:
    monkeypatch.setattr(cli, "build_deepseek_runtime_context", lambda **_: object())
    monkeypatch.setattr(cli, "run_workflow", lambda **_: state)

    result = cli.main(["run", "--project", str(tmp_path), "--task", "build"])

    captured = capsys.readouterr()
    assert result == expected_code
    assert all(text in captured.out for text in expected_text)


def test_ordinary_progress_uses_stderr_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_runner(**kwargs: object) -> WorkflowState:
        reporter = kwargs["progress_reporter"]
        assert callable(reporter)
        reporter(cli.WorkflowProgress("requirement", "需求分析完成", 0))
        return WorkflowState(status="completed", attempts=0, trace=[])

    monkeypatch.setattr(cli, "build_deepseek_runtime_context", lambda **_: object())
    monkeypatch.setattr(cli, "run_workflow", fake_runner)

    cli.main(["run", "--project", str(tmp_path), "--task", "build"])

    captured = capsys.readouterr()
    assert "[需求] 需求分析完成" in captured.err
    assert "[需求]" not in captured.out
    assert "LUXAR 执行成功" in captured.out


def test_json_mode_emits_one_stable_document_without_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    evidence = BuildEvidence(
        success=True,
        command=["idf.py", "build"],
        return_code=0,
        stdout_summary="safe summary",
    )

    def fake_runner(**kwargs: object) -> WorkflowState:
        assert kwargs["progress_reporter"] is None
        return WorkflowState(
            status="completed",
            attempts=1,
            build_evidence=evidence,
            changed_files=[],
            trace=["analyze_requirement", "completed"],
            task_text="SECRET_TASK_MUST_NOT_SERIALIZE",
        )

    monkeypatch.setattr(cli, "build_deepseek_runtime_context", lambda **_: object())
    monkeypatch.setattr(cli, "run_workflow", fake_runner)

    result = cli.main(
        ["run", "--project", str(tmp_path), "--task", "build", "--json"]
    )

    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert result == 0
    assert captured.err == ""
    assert document == {
        "status": "completed",
        "exit_code": 0,
        "attempts": 1,
        "requirement": None,
        "plan": None,
        "build_evidence": evidence.model_dump(mode="json"),
        "repair_plan": None,
        "changed_files": [],
        "error": None,
        "trace": ["analyze_requirement", "completed"],
    }
    assert "SECRET_TASK_MUST_NOT_SERIALIZE" not in captured.out
    assert captured.out.count("{") >= 1


def test_json_failed_state_is_stdout_business_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    error = WorkflowError(
        stage="build",
        category="environment",
        message="ESP-IDF 构建环境不可用",
        retryable=False,
        user_suggestion="请检查环境",
    )
    monkeypatch.setattr(cli, "build_deepseek_runtime_context", lambda **_: object())
    monkeypatch.setattr(
        cli,
        "run_workflow",
        lambda **_: WorkflowState(status="failed", error=error, trace=["failed"]),
    )

    result = cli.main(
        ["run", "--project", str(tmp_path), "--task", "build", "--json"]
    )

    captured = capsys.readouterr()
    assert result == 4
    assert captured.err == ""
    assert json.loads(captured.out)["error"] == error.model_dump(mode="json")
