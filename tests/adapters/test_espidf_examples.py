from pathlib import Path

import pytest
from pydantic import ValidationError

from luxar.adapters.espidf_examples import LocalEspIdfExampleLibrary
from luxar.database.persistence import KnowledgeMatch
from luxar.domain.idf_examples import EspIdfExampleReference
from luxar.domain.requirements import FirmwareRequirement


def make_example(root: Path, relative: str, readme: str, source: str) -> None:
    project = root / "examples" / relative
    (project / "main").mkdir(parents=True)
    (project / "CMakeLists.txt").write_text(
        "include($ENV{IDF_PATH}/tools/cmake/project.cmake)\nproject(example)\n",
        encoding="utf-8",
    )
    (project / "README.md").write_text(readme, encoding="utf-8")
    (project / "main" / "example.c").write_text(source, encoding="utf-8")


def test_example_reference_rejects_machine_absolute_path() -> None:
    with pytest.raises(ValidationError):
        EspIdfExampleReference(
            path=r"F:\esp\v6.0.2\esp-idf\examples\get-started\blink",
            score=1,
        )


def test_search_ranks_matching_official_example_and_reads_bounded_sources(
    tmp_path: Path,
) -> None:
    make_example(
        tmp_path,
        "get-started/blink",
        "# Blink Example\nBlink an LED with the GPIO driver.",
        "void app_main(void) { gpio_set_level(2, 1); }",
    )
    make_example(
        tmp_path,
        "protocols/http_server/simple",
        "# HTTP server\nServe an HTTP response over Wi-Fi.",
        "void app_main(void) {}",
    )
    library = LocalEspIdfExampleLibrary(tmp_path)
    requirement = FirmwareRequirement(
        target="esp32",
        goal="gpio_blink",
        peripherals=[{"kind": "gpio", "purpose": "blink LED"}],
    )

    references = library.search(requirement, limit=2)
    files = library.read(references[0])

    assert references[0].path == "get-started/blink"
    assert references[0].matched_terms == ["blink", "gpio", "led"]
    assert any(item.path.endswith("main/example.c") for item in files)
    assert all(item.path.startswith("examples/get-started/blink/") for item in files)


def test_search_returns_empty_when_sdk_has_no_examples(tmp_path: Path) -> None:
    library = LocalEspIdfExampleLibrary(tmp_path)
    requirement = FirmwareRequirement(target="esp32", goal="gpio_blink")

    assert library.search(requirement) == []


def test_sdk_rag_maps_chinese_goal_to_official_example_path(tmp_path: Path) -> None:
    make_example(
        tmp_path,
        "peripherals/ledc/ledc_fade",
        "# LEDC fade example\nFade an LED by changing PWM duty cycle.",
        "void app_main(void) { ledc_fade_start(); }",
    )

    class FakeKnowledge:
        def synchronized(self, version: str) -> bool:
            return True

        def search(self, *, version: str, query: str, limit: int):
            assert query == "实现呼吸灯"
            return [
                KnowledgeMatch(
                    document_id="ledc",
                    title="LEDC fade example",
                    source_uri=(
                        "espidf-example://peripherals/ledc/ledc_fade"
                    ),
                    ordinal=0,
                    content="ledc fade pwm duty cycle",
                    score=0.9,
                )
            ]

    library = LocalEspIdfExampleLibrary(
        tmp_path,
        knowledge=FakeKnowledge(),  # type: ignore[arg-type]
    )

    references = library.search(
        FirmwareRequirement(target="esp32", goal="实现呼吸灯")
    )

    assert references[0].path == "peripherals/ledc/ledc_fade"
    assert "sdk-rag" in references[0].matched_terms
