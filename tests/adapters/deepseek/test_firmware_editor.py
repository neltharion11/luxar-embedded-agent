import json

from luxar.adapters.deepseek.fake_client import FakeJsonCompletionClient
from luxar.adapters.deepseek.firmware_editor import DeepSeekFirmwareEditor
from luxar.domain.idf_examples import EspIdfExampleReference
from luxar.domain.project_analysis import ProjectAnalysis
from luxar.domain.repairs import ProjectFile
from luxar.domain.requirements import FirmwareRequirement


def test_firmware_editor_receives_official_example_context() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "diagnosis": "reuse official blink example",
                "replacements": [
                    {"path": "main/main.c", "content": "void app_main(void) {}"}
                ],
            }
        ]
    )
    editor = DeepSeekFirmwareEditor(client, "deepseek-reasoner")

    editor.create_change(
        FirmwareRequirement(target="esp32", goal="gpio_blink"),
        ProjectAnalysis(
            project_exists=True,
            has_source_code=True,
            fingerprint="current",
            summary="empty project",
        ),
        [ProjectFile(path="main/main.c", content="void app_main(void) {}")],
        [
            EspIdfExampleReference(
                path="get-started/blink",
                score=18,
                matched_terms=["blink", "gpio"],
            )
        ],
        [
            ProjectFile(
                path="examples/get-started/blink/main/blink.c",
                content="gpio_set_level(2, 1);",
            )
        ],
    )

    system_prompt, user_prompt, model = client.calls[0]
    payload = json.loads(user_prompt)
    assert "必须优先采用" in system_prompt
    assert payload["official_example_references"][0]["path"] == "get-started/blink"
    assert payload["official_example_files"][0]["path"].endswith("main/blink.c")
    assert model == "deepseek-reasoner"
