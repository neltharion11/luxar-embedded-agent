"""CLI 展示边界测试：不调用真实 DeepSeek 或 ESP-IDF。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from luxar import cli
from luxar.application.runner import WorkflowRunResult
from luxar.application.state import WorkflowState
from luxar.domain.devices import ApprovalRequest
from luxar.domain.errors import WorkflowError
from luxar.domain.evidence import BuildEvidence
from luxar.domain.requirements import FirmwareRequirement


def _run_result(state: WorkflowState) -> WorkflowRunResult:
    return WorkflowRunResult(state=state, thread_id="test-thread")


@pytest.mark.parametrize(
    "argv",
    [
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


def test_parser_allows_bare_invocation() -> None:
    args = cli.build_parser().parse_args([])

    assert args.command is None


def _clear_luxar_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    skip_dotenv: bool = True,
) -> None:
    # 隔离仓库真实 .env:loader 会读当前目录的 .env,测试必须从干净环境开始。
    if skip_dotenv:
        monkeypatch.setenv("LUXAR_SKIP_DOTENV", "1")
    else:
        monkeypatch.delenv("LUXAR_SKIP_DOTENV", raising=False)
    for name in (
        "LUXAR_PROJECTS_ROOT",
        "LUXAR_SERIAL_PORT",
        "LUXAR_TARGET_CHIP",
        "LUXAR_WEB_PORT",
        "LUXAR_PYTHON",
    ):
        monkeypatch.delenv(name, raising=False)


def test_bare_luxar_starts_web_with_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_luxar_env(monkeypatch)
    received: dict[str, object] = {}

    def fake_serve(**kwargs: object) -> int:
        received.update(kwargs)
        return 0

    monkeypatch.setattr("luxar.web.serve", fake_serve)

    result = cli.main([])

    assert result == 0
    assert received["projects_roots"] == [Path("projects")]
    assert received["serial_port"] is None
    assert received["target_chip"] is None
    assert received["port"] == 8000


def test_bare_luxar_reads_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_luxar_env(monkeypatch)
    received: dict[str, object] = {}

    def fake_serve(**kwargs: object) -> int:
        received.update(kwargs)
        return 0

    monkeypatch.setattr("luxar.web.serve", fake_serve)
    second_root = tmp_path / "second"
    second_root.mkdir()
    monkeypatch.setenv(
        "LUXAR_PROJECTS_ROOT",
        f"{tmp_path}{os.pathsep}{second_root}",
    )
    monkeypatch.setenv("LUXAR_SERIAL_PORT", "COM4")
    monkeypatch.setenv("LUXAR_TARGET_CHIP", "esp32s3")
    monkeypatch.setenv("LUXAR_WEB_PORT", "9000")

    result = cli.main([])

    assert result == 0
    assert received["projects_roots"] == [tmp_path, second_root]
    assert received["serial_port"] == "COM4"
    assert received["target_chip"] == "esp32s3"
    assert received["port"] == 9000


def test_bare_luxar_ignores_invalid_web_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_luxar_env(monkeypatch)
    received: dict[str, object] = {}

    def fake_serve(**kwargs: object) -> int:
        received.update(kwargs)
        return 0

    monkeypatch.setattr("luxar.web.serve", fake_serve)
    monkeypatch.setenv("LUXAR_WEB_PORT", "not-a-port")

    result = cli.main([])

    assert result == 0
    assert received["port"] == 8000


def test_main_loads_repo_dotenv_when_environment_unset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_luxar_env(monkeypatch, skip_dotenv=False)
    received: dict[str, object] = {}

    def fake_serve(**kwargs: object) -> int:
        received.update(kwargs)
        return 0

    (tmp_path / ".env").write_text(
        "LUXAR_WEB_PORT=9000\n"
        "LUXAR_SERIAL_PORT=COM4\n"
        "# 注释行被忽略\n"
        "DEEPSEEK_API_KEY=sk-test\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("luxar.web.serve", fake_serve)

    result = cli.main([])

    assert result == 0
    assert received["port"] == 9000
    assert received["serial_port"] == "COM4"


def test_real_environment_beats_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_luxar_env(monkeypatch, skip_dotenv=False)
    received: dict[str, object] = {}

    def fake_serve(**kwargs: object) -> int:
        received.update(kwargs)
        return 0

    (tmp_path / ".env").write_text(
        "LUXAR_WEB_PORT=9000\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LUXAR_WEB_PORT", "7000")
    monkeypatch.setattr("luxar.web.serve", fake_serve)

    cli.main([])

    assert received["port"] == 7000


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
        lambda **_: _run_result(WorkflowState(status="completed", trace=[])),
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

    def fake_runner(**kwargs: object) -> WorkflowRunResult:
        calls["runner"] = kwargs
        return _run_result(
            WorkflowState(status="completed", attempts=1, trace=[])
        )

    monkeypatch.setattr(cli, "build_deepseek_runtime_context", fake_bootstrap)
    monkeypatch.setattr(cli, "run_workflow", fake_runner)

    result = cli.main(
        ["run", "--project", str(tmp_path), "--max-attempts", "5"]
    )

    assert result == 0
    assert calls["bootstrap"] == {
        "project_path": tmp_path,
        "target_chip": None,
        "serial_port": None,
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
        lambda **_: _run_result(WorkflowState(status="completed", trace=[])),
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
    monkeypatch.setattr(cli, "run_workflow", lambda **_: _run_result(state))

    result = cli.main(["run", "--project", str(tmp_path), "--task", "build"])

    captured = capsys.readouterr()
    assert result == expected_code
    assert all(text in captured.out for text in expected_text)


def test_ordinary_progress_uses_stderr_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_runner(**kwargs: object) -> WorkflowRunResult:
        reporter = kwargs["progress_reporter"]
        assert callable(reporter)
        reporter(cli.WorkflowProgress("requirement", "需求分析完成", 0))
        return _run_result(
            WorkflowState(status="completed", attempts=0, trace=[])
        )

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

    def fake_runner(**kwargs: object) -> WorkflowRunResult:
        assert kwargs["progress_reporter"] is None
        return _run_result(
            WorkflowState(
                status="completed",
                attempts=1,
                build_evidence=evidence,
                changed_files=[],
                trace=["analyze_requirement", "completed"],
                task_text="SECRET_TASK_MUST_NOT_SERIALIZE",
            )
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
        "created_project": None,
        "build_evidence": evidence.model_dump(mode="json"),
        "flash_evidence": None,
        "monitor_evidence": None,
        "device_diagnosis": None,
        "approval_status": "not_requested",
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
        lambda **_: _run_result(
            WorkflowState(status="failed", error=error, trace=["failed"])
        ),
    )

    result = cli.main(
        ["run", "--project", str(tmp_path), "--task", "build", "--json"]
    )

    captured = capsys.readouterr()
    assert result == 4
    assert captured.err == ""
    assert json.loads(captured.out)["error"] == error.model_dump(mode="json")


def approval_request() -> ApprovalRequest:
    return ApprovalRequest(
        project_name="blink",
        port="COM3",
        target_chip="esp32",
        summary="即将向串口设备烧录固件，请确认目标芯片与串口",
        step_description="flash_project",
        attempts=0,
    )


def test_ports_lists_discovered_devices(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from luxar.domain.devices import SerialPortInfo

    monkeypatch.setattr(
        cli,
        "discover_serial_ports",
        lambda: [
            SerialPortInfo(
                name="COM3",
                description="USB Serial",
                hardware_id="USB VID:PID=1A86:7523",
            )
        ],
    )

    result = cli.main(["ports"])

    captured = capsys.readouterr()
    assert result == 0
    assert "COM3" in captured.out
    assert "1A86:7523" in captured.out


def test_ports_reports_empty_list(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "discover_serial_ports", lambda: [])

    result = cli.main(["ports"])

    captured = capsys.readouterr()
    assert result == 0
    assert "未发现可用串口设备" in captured.err


@pytest.mark.parametrize("port", ["COM0", "com3", "COM3;rm", "/dev/ttyS0"])
def test_cli_rejects_invalid_port_values(
    tmp_path: Path,
    port: str,
) -> None:
    with pytest.raises(SystemExit) as captured:
        cli.build_parser().parse_args(
            ["run", "--project", str(tmp_path), "--port", port]
        )

    assert captured.value.code == 2


def test_port_reaches_bootstrap(
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
        lambda **_: _run_result(WorkflowState(status="completed", trace=[])),
    )

    result = cli.main(
        ["run", "--project", str(tmp_path), "--task", "build", "--port", "COM3"]
    )

    assert result == 0
    assert received["serial_port"] == "COM3"


def test_interactive_approval_accepts_yes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    seen: list[ApprovalRequest] = []

    def fake_runner(**kwargs: object) -> WorkflowRunResult:
        handler = kwargs["approval_handler"]
        assert callable(handler)
        request = approval_request()
        seen.append(request)
        decided = handler(request)
        return _run_result(
            WorkflowState(
                status="completed" if decided else "failed",
                approval_status="approved" if decided else "rejected",
                trace=[],
            )
        )

    monkeypatch.setattr(cli, "build_deepseek_runtime_context", lambda **_: object())
    monkeypatch.setattr(cli, "run_workflow", fake_runner)

    result = cli.main(["run", "--project", str(tmp_path), "--task", "flash"])

    captured = capsys.readouterr()
    assert result == 0
    assert len(seen) == 1
    assert seen[0].port == "COM3"
    assert "烧录审批" in captured.err
    assert "COM3" in captured.err


def test_interactive_approval_rejects_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    seen: list[ApprovalRequest] = []

    def fake_runner(**kwargs: object) -> WorkflowRunResult:
        handler = kwargs["approval_handler"]
        assert callable(handler)
        request = approval_request()
        seen.append(request)
        decided = handler(request)
        error = None
        if not decided:
            error = WorkflowError(
                stage="flash",
                category="approval_rejected",
                message="烧录申请被用户拒绝",
                retryable=False,
                user_suggestion="确认目标芯片和串口后重新运行任务",
            )
        return _run_result(
            WorkflowState(
                status="completed" if decided else "failed",
                error=error,
                trace=[],
            )
        )

    monkeypatch.setattr(cli, "build_deepseek_runtime_context", lambda **_: object())
    monkeypatch.setattr(cli, "run_workflow", fake_runner)

    result = cli.main(["run", "--project", str(tmp_path), "--task", "flash"])

    assert result == 4
    assert len(seen) == 1
    assert "烧录申请被用户拒绝" in capsys.readouterr().out


def test_json_mode_with_approve_flag_preauthorizes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler_decisions: list[bool] = []

    def fake_runner(**kwargs: object) -> WorkflowRunResult:
        handler = kwargs["approval_handler"]
        assert callable(handler)
        handler_decisions.append(handler(approval_request()))
        return _run_result(WorkflowState(status="completed", trace=[]))

    monkeypatch.setattr(cli, "build_deepseek_runtime_context", lambda **_: object())
    monkeypatch.setattr(cli, "run_workflow", fake_runner)

    result = cli.main(
        [
            "run",
            "--project",
            str(tmp_path),
            "--task",
            "flash",
            "--json",
            "--approve-flash",
        ]
    )

    assert result == 0
    assert handler_decisions == [True]


def test_json_mode_without_approve_flag_terminates_on_pause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_runner(**kwargs: object) -> WorkflowRunResult:
        assert kwargs["approval_handler"] is None
        return WorkflowRunResult(
            state=WorkflowState(status="planned", trace=[]),
            thread_id="t1",
            pending_approval=approval_request(),
        )

    monkeypatch.setattr(cli, "build_deepseek_runtime_context", lambda **_: object())
    monkeypatch.setattr(cli, "run_workflow", fake_runner)

    result = cli.main(
        ["run", "--project", str(tmp_path), "--task", "flash", "--json"]
    )

    captured = capsys.readouterr()
    assert result == 4
    assert captured.out == ""
    assert "--approve-flash" in captured.err


def test_approve_flag_requires_json_mode(tmp_path: Path) -> None:
    result = cli.main(
        [
            "run",
            "--project",
            str(tmp_path),
            "--task",
            "flash",
            "--approve-flash",
        ]
    )

    assert result == 2


def test_web_subcommand_forwards_to_serve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, object] = {}

    def fake_serve(**kwargs: object) -> int:
        received.update(kwargs)
        return 0

    monkeypatch.setattr("luxar.web.serve", fake_serve)

    result = cli.main(
        [
            "web",
            "--projects-root",
            str(tmp_path),
            "--serial-port",
            "COM4",
            "--target",
            "esp32s3",
            "--max-concurrent-workflows",
            "4",
        ]
    )

    assert result == 0
    assert received == {
        "projects_roots": [tmp_path],
        "host": "127.0.0.1",
        "port": 8000,
        "serial_port": "COM4",
        "target_chip": "esp32s3",
        "max_concurrent_workflows": 4,
    }


@pytest.mark.parametrize(
    "argv",
    [
        ["web", "--projects-root", "x", "--serial-port", "COM0"],
        ["web", "--projects-root", "x", "--serial-port", "COM4;rm"],
        ["web", "--projects-root", "x", "--target", "ESP32"],
        ["web", "--projects-root", "x", "--port", "0"],
    ],
)
def test_web_subcommand_rejects_invalid_options(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as captured:
        cli.build_parser().parse_args(argv)

    assert captured.value.code == 2


def test_setup_subcommand_runs_bundled_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_call(command: list[str]) -> int:
        calls.append(command)
        return 0

    monkeypatch.setattr("subprocess.call", fake_call)

    result = cli.main(["setup"])

    assert result == 0
    assert len(calls) == 1
    assert calls[0][0] == "powershell"
    assert "setup.ps1" in calls[0][-1]
