from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from luxar.adapters.espidf_project import EspIdfProjectAdapter
from luxar.domain.projects import ProjectEvidence
from luxar.ports.espidf_errors import EspIdfError


class FakeCompletedProcess:
    def __init__(
        self,
        returncode: int,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _allow_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "luxar.adapters.espidf_common.shutil.which",
        lambda command: f"C:/tools/{command}",
    )


def _run_create(
    monkeypatch: pytest.MonkeyPatch,
    result: FakeCompletedProcess,
) -> None:
    monkeypatch.setattr(
        "luxar.adapters.espidf_project.subprocess.run",
        lambda *args, **kwargs: result,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("create_timeout_seconds", 0),
        ("max_summary_chars", True),
        ("max_summary_chars", -5),
    ],
)
def test_constructor_rejects_invalid_positive_integer_limits(
    field: str,
    value: int | bool,
) -> None:
    with pytest.raises(ValueError, match=f"{field} must be a positive integer"):
        EspIdfProjectAdapter(**{field: value})  # type: ignore[arg-type]


@pytest.mark.parametrize("command", [(), [], [""], ["python", "  "]])
def test_constructor_rejects_empty_command(
    command: list[str] | tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="idf_command"):
        EspIdfProjectAdapter(idf_command=command)


def test_constructor_copies_idf_command() -> None:
    command = ["python", "idf.py"]
    adapter = EspIdfProjectAdapter(idf_command=command)
    command.append("unsafe")
    assert adapter.idf_command == ("python", "idf.py")


def test_create_project_runs_command_and_writes_target_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> FakeCompletedProcess:
        captured["args"] = args
        captured["kwargs"] = kwargs

        # 模拟 idf.py 真的创建了项目目录。
        parent = Path(kwargs["cwd"])
        (parent / "blink").mkdir()
        (parent / "blink" / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.16)\n",
            encoding="utf-8",
        )
        return FakeCompletedProcess(0, stdout="Created project blink\n")

    monkeypatch.setattr(
        "luxar.adapters.espidf_project.subprocess.run",
        fake_run,
    )
    parent = tmp_path / "parent"
    parent.mkdir()

    evidence = EspIdfProjectAdapter().create_project(
        parent,
        "blink",
        "esp32s3",
    )

    assert evidence == ProjectEvidence(
        success=True,
        command=["idf.py", "create-project", "blink"],
        return_code=0,
        created_dir="blink",
        stdout_summary="Created project blink\n",
    )
    args = captured["args"][0]
    # IDF v5+/v6:--path 是项目要直接创建的目录,NAME 是主源文件名。
    assert list(args) == [
        "idf.py",
        "create-project",
        "--path",
        str(parent / "blink"),
        "blink",
    ]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["cwd"] == parent

    config = (parent / "blink" / "sdkconfig.defaults").read_text(
        encoding="utf-8"
    )
    assert "CONFIG_IDF_TARGET=esp32s3" in config


def test_create_project_timeout_becomes_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    parent = tmp_path / "parent"
    parent.mkdir()

    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(
            ["idf.py", "create-project"],
            120,
            output=b"slow output",
            stderr=b"C:\\tools\\SECRET_LOCATION\\slow",
        )

    monkeypatch.setattr(
        "luxar.adapters.espidf_project.subprocess.run",
        raise_timeout,
    )

    evidence = EspIdfProjectAdapter().create_project(
        parent,
        "blink",
        "esp32",
    )

    assert evidence.success is False
    assert evidence.return_code == -1
    assert evidence.error_category == "timeout"
    assert "slow output" in evidence.stdout_summary
    # 输出中的绝对路径已被脱敏。
    assert "SECRET_LOCATION" not in evidence.stderr_summary
    assert "<external-path>" in evidence.stderr_summary


def test_create_project_unsupported_command_is_sanitized_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    parent = tmp_path / "parent"
    parent.mkdir()
    _run_create(
        monkeypatch,
        FakeCompletedProcess(
            2,
            stderr="idf.py: error: argument actions: invalid choice: "
            "'create-project'",
        ),
    )

    with pytest.raises(EspIdfError) as captured:
        EspIdfProjectAdapter().create_project(parent, "blink", "esp32")

    assert captured.value.category == "environment"
    assert "invalid choice" not in captured.value.message


def test_create_project_failure_becomes_environment_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    parent = tmp_path / "parent"
    parent.mkdir()
    _run_create(
        monkeypatch,
        FakeCompletedProcess(
            1,
            stderr="could not find idf_path\nC:\\tools\\SECRET_LOCATION",
        ),
    )

    evidence = EspIdfProjectAdapter().create_project(
        parent,
        "blink",
        "esp32",
    )

    assert evidence.success is False
    assert evidence.return_code == 1
    assert evidence.error_category == "environment"
    assert "SECRET_LOCATION" not in evidence.stderr_summary
    assert "<external-path>" in evidence.stderr_summary


def test_create_project_process_failure_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    parent = tmp_path / "parent"
    parent.mkdir()

    def raise_oserror(*args: object, **kwargs: object) -> None:
        raise OSError("SECRET_PROCESS_DETAIL")

    monkeypatch.setattr(
        "luxar.adapters.espidf_project.subprocess.run",
        raise_oserror,
    )

    with pytest.raises(EspIdfError) as captured:
        EspIdfProjectAdapter().create_project(parent, "blink", "esp32")

    assert captured.value.category == "process"
    assert "SECRET_PROCESS_DETAIL" not in captured.value.message


def test_create_project_rejects_missing_or_file_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    adapter = EspIdfProjectAdapter()

    with pytest.raises(EspIdfError) as captured:
        adapter.create_project(tmp_path / "missing", "blink", "esp32")
    assert captured.value.category == "invalid_project"

    file_parent = tmp_path / "file"
    file_parent.write_text("not a directory", encoding="utf-8")
    with pytest.raises(EspIdfError) as captured:
        adapter.create_project(file_parent, "blink", "esp32")
    assert captured.value.category == "invalid_project"


@pytest.mark.parametrize(
    ("project_name", "target_chip"),
    [
        ("../evil", "esp32"),
        ("a/b", "esp32"),
        ("-x", "esp32"),
        ("blink", "ESP32"),
        ("blink", "esp32;rm"),
        ("blink", ""),
    ],
)
def test_create_project_rejects_invalid_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    project_name: str,
    target_chip: str,
) -> None:
    _allow_launcher(monkeypatch)
    parent = tmp_path / "parent"
    parent.mkdir()

    with pytest.raises(EspIdfError) as captured:
        EspIdfProjectAdapter().create_project(
            parent,
            project_name,
            target_chip,
        )

    assert captured.value.category == "invalid_project"


def test_existing_espidf_project_is_reused_without_recreation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    parent = tmp_path / "parent"
    project = parent / "blink"
    project.mkdir(parents=True)
    (project / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n",
        encoding="utf-8",
    )
    (project / "sdkconfig.defaults").write_text(
        "CONFIG_IDF_TARGET=esp32\n",
        encoding="utf-8",
    )

    def fail_if_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess must not run for existing project")

    monkeypatch.setattr(
        "luxar.adapters.espidf_project.subprocess.run",
        fail_if_run,
    )

    evidence = EspIdfProjectAdapter().create_project(
        parent,
        "blink",
        "esp32",
    )

    assert evidence.success is True
    assert evidence.already_existed is True
    assert evidence.created_dir == "blink"
    # 已有配置未被改写。
    assert (project / "sdkconfig.defaults").read_text(
        encoding="utf-8"
    ) == "CONFIG_IDF_TARGET=esp32\n"


def test_existing_project_target_can_be_bound_without_idf_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    project = parent / "blink"
    project.mkdir(parents=True)
    (project / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "luxar.adapters.espidf_common.shutil.which",
        lambda _: None,
    )

    evidence = EspIdfProjectAdapter().create_project(
        parent,
        "blink",
        "esp32c3",
    )

    assert evidence.already_existed is True
    assert (project / "sdkconfig.defaults").read_text(
        encoding="utf-8"
    ).endswith("CONFIG_IDF_TARGET=esp32c3\n")


def test_existing_project_with_conflicting_target_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    parent = tmp_path / "parent"
    project = parent / "blink"
    project.mkdir(parents=True)
    (project / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n",
        encoding="utf-8",
    )
    (project / "sdkconfig.defaults").write_text(
        "CONFIG_IDF_TARGET=esp32\n",
        encoding="utf-8",
    )

    with pytest.raises(EspIdfError) as captured:
        EspIdfProjectAdapter().create_project(
            parent,
            "blink",
            "esp32s3",
        )

    assert captured.value.category == "invalid_project"


def test_existing_directory_without_cmake_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_launcher(monkeypatch)
    parent = tmp_path / "parent"
    (parent / "blink").mkdir(parents=True)

    with pytest.raises(EspIdfError) as captured:
        EspIdfProjectAdapter().create_project(parent, "blink", "esp32")

    assert captured.value.category == "invalid_project"


def test_create_project_rejects_missing_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    monkeypatch.setattr(
        "luxar.adapters.espidf_common.shutil.which",
        lambda _: None,
    )

    with pytest.raises(EspIdfError) as captured:
        EspIdfProjectAdapter().create_project(parent, "blink", "esp32")

    assert captured.value.category == "environment"
