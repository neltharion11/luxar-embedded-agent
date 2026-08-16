"""真实 ESP-IDF 冒烟测试：仅在显式授权且环境可用时运行。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from luxar.adapters.espidf_cli import EspIdfCliAdapter


def _write_minimal_espidf_project(project: Path) -> None:
    """在 pytest 临时目录中创建一个没有托管依赖的最小工程。"""

    main_directory = project / "main"
    main_directory.mkdir()

    (project / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n"
        "include($ENV{IDF_PATH}/tools/cmake/project.cmake)\n"
        "project(luxar_smoke)\n",
        encoding="utf-8",
    )
    (main_directory / "CMakeLists.txt").write_text(
        'idf_component_register(SRCS "main.c" INCLUDE_DIRS ".")\n',
        encoding="utf-8",
    )
    (main_directory / "main.c").write_text(
        "void app_main(void) {}\n",
        encoding="utf-8",
    )


def _resolve_launcher() -> tuple[str, ...]:
    """解析可执行的 idf.py 启动器。

    激活脚本把 idf.py 注册成 PowerShell 函数而不是可执行文件，
    因此本机已知的 venv python + 脚本安装优先。
    """

    venv_python = (
        r"F:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe"
    )
    idf_script = r"F:\esp\v6.0.2\esp-idf\tools\idf.py"

    if Path(venv_python).is_file() and Path(idf_script).is_file():
        return (venv_python, idf_script)

    return ("idf.py",)


def test_real_espidf_build_is_explicitly_opt_in(tmp_path: Path) -> None:
    """显式开关和环境可用时运行真实构建。"""

    if os.environ.get("LUXAR_RUN_ESPIDF_SMOKE") != "1":
        pytest.skip("set LUXAR_RUN_ESPIDF_SMOKE=1 to run the real ESP-IDF smoke")

    _write_minimal_espidf_project(tmp_path)

    evidence = EspIdfCliAdapter(
        idf_command=_resolve_launcher(),
        allow_dependency_downloads=False,
    ).build(tmp_path)

    assert evidence.success is True
    assert evidence.command == ["idf.py", "build"]
    assert evidence.return_code == 0
    assert evidence.error_category is None
