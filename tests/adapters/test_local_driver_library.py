from __future__ import annotations

from pathlib import Path

import pytest

from luxar.adapters.local_driver_library import DriverLibraryError, LocalDriverLibrary
from luxar.domain.drivers import DriverPublishSpec, DriverVerification


def _project(root: Path) -> Path:
    project = root / "project"
    component = project / "components" / "ssd1306"
    (component / "include").mkdir(parents=True)
    (component / "ssd1306.c").write_text("void ssd1306_init(void) {}\n", encoding="utf-8")
    (component / "include" / "ssd1306.h").write_text(
        "void ssd1306_init(void);\n", encoding="utf-8"
    )
    (component / "CMakeLists.txt").write_text(
        'idf_component_register(SRCS "ssd1306.c" INCLUDE_DIRS "include")\n',
        encoding="utf-8",
    )
    return project


def _spec(version: str = "1.0.0") -> DriverPublishSpec:
    return DriverPublishSpec(
        driver_id="generic.ssd1306.i2c",
        version=version,
        name="SSD1306 I2C",
        vendor="Generic",
        hardware="SSD1306",
        protocols=["i2c"],
        targets=["esp32"],
        description="ESP-IDF SSD1306 display driver",
        file_paths=[
            "components/ssd1306/CMakeLists.txt",
            "components/ssd1306/ssd1306.c",
            "components/ssd1306/include/ssd1306.h",
        ],
    )


def test_public_driver_package_publish_search_read_and_idempotency(tmp_path: Path) -> None:
    project = _project(tmp_path)
    library = LocalDriverLibrary(tmp_path / "data" / "driver-library")
    verification = DriverVerification(
        quality="build_verified",
        build_verified=True,
        evidence_ids=["build:latest"],
    )

    first = library.publish(
        project_path=project,
        project_key="0:display",
        spec=_spec(),
        verification=verification,
    )
    repeated = library.publish(
        project_path=project,
        project_key="0:display",
        spec=_spec(),
        verification=verification,
    )
    matches = library.search(
        query="为 SSD1306 编写 I2C 驱动",
        hardware="ssd1306",
        protocol="I2C",
        target_chip="esp32",
    )
    package = library.read("generic.ssd1306.i2c")

    assert repeated.content_hash == first.content_hash
    assert matches[0].driver_id == "generic.ssd1306.i2c"
    assert matches[0].score >= 200
    assert package.manifest.verification.build_verified is True
    assert "ssd1306_init" in package.sources["components/ssd1306/ssd1306.c"]


def test_public_driver_version_is_immutable_and_paths_are_bounded(tmp_path: Path) -> None:
    project = _project(tmp_path)
    library = LocalDriverLibrary(tmp_path / "library")
    library.publish(
        project_path=project,
        project_key="0:display",
        spec=_spec(),
        verification=DriverVerification(),
    )
    (project / "components" / "ssd1306" / "ssd1306.c").write_text(
        "void ssd1306_init(void) { /* changed */ }\n", encoding="utf-8"
    )

    with pytest.raises(DriverLibraryError, match="内容不同"):
        library.publish(
            project_path=project,
            project_key="0:display",
            spec=_spec(),
            verification=DriverVerification(),
        )

    invalid = _spec().model_copy(update={"version": "2.0.0", "file_paths": ["../secret.c"]})
    with pytest.raises(DriverLibraryError, match="相对路径"):
        library.publish(
            project_path=project,
            project_key="0:display",
            spec=invalid,
            verification=DriverVerification(),
        )


def test_search_returns_only_latest_version_for_each_driver(tmp_path: Path) -> None:
    project = _project(tmp_path)
    library = LocalDriverLibrary(tmp_path / "library")
    first = library.publish(
        project_path=project,
        project_key="0:display",
        spec=_spec("1.0.0"),
        verification=DriverVerification(),
    )
    second = library.publish(
        project_path=project,
        project_key="0:display",
        spec=_spec("2.0.0"),
        verification=DriverVerification(build_verified=True, quality="build_verified"),
    )

    matches = library.search(limit=100)

    assert first.driver_id == second.driver_id
    assert [(item.driver_id, item.version) for item in matches] == [
        ("generic.ssd1306.i2c", "2.0.0")
    ]


def test_missing_source_is_reported_as_a_library_error(tmp_path: Path) -> None:
    project = _project(tmp_path)
    library = LocalDriverLibrary(tmp_path / "library")
    missing = _spec().model_copy(
        update={"file_paths": ["components/ssd1306/missing.c"]}
    )

    with pytest.raises(DriverLibraryError, match="不存在"):
        library.publish(
            project_path=project,
            project_key="0:display",
            spec=missing,
            verification=DriverVerification(),
        )
