from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from luxar.adapters.local_driver_library import LocalDriverLibrary
from luxar.database.persistence import TransientPersistence
from luxar.web import create_app


def _project(root: Path) -> None:
    component = root / "display" / "components" / "ssd1306"
    component.mkdir(parents=True)
    (root / "display" / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n"
        "include($ENV{IDF_PATH}/tools/cmake/project.cmake)\n"
        "project(display)\n",
        encoding="utf-8",
    )
    (component / "ssd1306.c").write_text("void init_display(void) {}\n", encoding="utf-8")
    (component / "CMakeLists.txt").write_text(
        'idf_component_register(SRCS "ssd1306.c")\n', encoding="utf-8"
    )


def test_driver_library_http_publish_list_and_read(tmp_path: Path) -> None:
    _project(tmp_path)
    library = LocalDriverLibrary(tmp_path / "public-drivers")
    client = TestClient(create_app(
        projects_roots=[tmp_path],
        persistence=TransientPersistence(),
        driver_library_service=library,
    ))

    published = client.post("/api/projects/display/drivers", json={
        "driver_id": "generic.ssd1306.i2c",
        "version": "1.0.0",
        "name": "SSD1306 I2C",
        "vendor": "Generic",
        "hardware": "SSD1306",
        "protocols": ["I2C"],
        "targets": ["ESP32"],
        "description": "display driver",
        "file_paths": [
            "components/ssd1306/CMakeLists.txt",
            "components/ssd1306/ssd1306.c",
        ],
    })
    listed = client.get("/api/drivers?hardware=SSD1306&protocol=i2c")
    detail = client.get(
        "/api/drivers/generic.ssd1306.i2c?include_source=true"
    )

    assert published.status_code == 201
    assert published.json()["driver"]["verification"]["quality"] == "draft"
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert listed.json()["drivers"][0]["protocols"] == ["i2c"]
    assert detail.status_code == 200
    assert "init_display" in detail.json()["sources"]["components/ssd1306/ssd1306.c"]


def test_driver_library_http_rejects_source_outside_project(tmp_path: Path) -> None:
    _project(tmp_path)
    library = LocalDriverLibrary(tmp_path / "public-drivers")
    client = TestClient(create_app(
        projects_roots=[tmp_path],
        persistence=TransientPersistence(),
        driver_library_service=library,
    ))

    response = client.post("/api/projects/display/drivers", json={
        "driver_id": "generic.bad.i2c",
        "version": "1.0.0",
        "name": "Bad",
        "hardware": "Bad",
        "protocols": ["i2c"],
        "file_paths": ["../outside.c"],
    })

    assert response.status_code == 422
