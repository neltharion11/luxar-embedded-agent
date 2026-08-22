from pathlib import Path

import pytest

from luxar.web_projects import WebProjectCatalog, WebProjectError


def make_project(root: Path, name: str) -> Path:
    project = root / name
    project.mkdir()
    (project / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n"
        "include($ENV{IDF_PATH}/tools/cmake/project.cmake)\n"
        f"project({name})\n",
        encoding="utf-8",
    )
    return project


def test_catalog_lists_only_sorted_direct_espidf_projects(tmp_path: Path) -> None:
    make_project(tmp_path, "zeta")
    make_project(tmp_path, "alpha")
    (tmp_path / "not-project").mkdir()
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    generic = tmp_path / "generic-cmake"
    generic.mkdir()
    (generic / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.22)\nproject(generic)\n",
        encoding="utf-8",
    )
    nested = tmp_path / "group"
    nested.mkdir()
    make_project(nested, "hidden-nested-project")

    catalog = WebProjectCatalog(tmp_path)

    assert [
        project.model_dump(exclude_none=True)
        for project in catalog.list_projects()
    ] == [
        {"name": "alpha", "platform": "espidf", "root_index": 0},
        {"name": "zeta", "platform": "espidf", "root_index": 0},
    ]


@pytest.mark.parametrize(
    "name",
    ["", ".", "..", "../outside", "group/project", "group\\project", "C:evil"],
)
def test_catalog_rejects_path_syntax(tmp_path: Path, name: str) -> None:
    catalog = WebProjectCatalog(tmp_path)

    with pytest.raises(WebProjectError):
        catalog.resolve(name)


def test_catalog_resolves_one_existing_project(tmp_path: Path) -> None:
    project = make_project(tmp_path, "blink")

    assert WebProjectCatalog(tmp_path).resolve("blink") == project.resolve()


def test_catalog_reads_project_target_from_sdkconfig_defaults(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path, "blink")
    (project / "sdkconfig.defaults").write_text(
        "CONFIG_IDF_TARGET=esp32s3\n",
        encoding="utf-8",
    )

    catalog = WebProjectCatalog(tmp_path)

    assert catalog.target_chip("blink") == "esp32s3"
    assert catalog.list_projects()[0].target_chip == "esp32s3"


def test_catalog_rejects_missing_or_non_espidf_project(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    catalog = WebProjectCatalog(tmp_path)

    with pytest.raises(WebProjectError):
        catalog.resolve("missing")
    with pytest.raises(WebProjectError):
        catalog.resolve("plain")
