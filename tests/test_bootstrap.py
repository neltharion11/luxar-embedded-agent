from pathlib import Path

from luxar.adapters.deepseek.fake_client import FakeJsonCompletionClient
from luxar.adapters.deepseek.planner import DeepSeekPlanner
from luxar.adapters.deepseek.repair_planner import DeepSeekRepairPlanner
from luxar.adapters.deepseek.requirement_parser import DeepSeekRequirementParser
from luxar.adapters.deepseek.settings import DeepSeekSettings
from luxar.adapters.fake_espidf import FakeEspIdf
from luxar.adapters.fake_workspace import FakeWorkspace
from luxar.bootstrap import build_deepseek_runtime_context


def test_bootstrap_injects_ports_models_and_one_shared_client() -> None:
    settings = DeepSeekSettings(
        api_key="test-key",
        fast_model="fast-test",
        repair_model="repair-test",
    )
    client = FakeJsonCompletionClient([])
    espidf = FakeEspIdf([])
    workspace = FakeWorkspace([])
    project_path = Path("firmware")

    context = build_deepseek_runtime_context(
        espidf=espidf,
        workspace=workspace,
        project_path=project_path,
        settings=settings,
        client=client,
    )

    assert context.espidf is espidf
    assert context.workspace is workspace
    assert context.project_path is project_path

    assert isinstance(context.requirement_parser, DeepSeekRequirementParser)
    assert isinstance(context.planner, DeepSeekPlanner)
    assert isinstance(context.repair_planner, DeepSeekRepairPlanner)

    assert context.requirement_parser._client is client
    assert context.planner._client is client
    assert context.repair_planner._client is client
    assert context.requirement_parser._model == "fast-test"
    assert context.planner._model == "fast-test"
    assert context.repair_planner._model == "repair-test"


def test_bootstrap_constructs_one_client_when_none_is_injected(
    monkeypatch,
) -> None:
    settings = DeepSeekSettings(api_key="test-key")
    created_with: list[DeepSeekSettings] = []
    client = FakeJsonCompletionClient([])

    def make_client(received_settings: DeepSeekSettings):
        created_with.append(received_settings)
        return client

    monkeypatch.setattr(
        "luxar.bootstrap.DeepSeekJsonClient",
        make_client,
    )

    context = build_deepseek_runtime_context(
        espidf=FakeEspIdf([]),
        workspace=FakeWorkspace([]),
        project_path=Path("firmware"),
        settings=settings,
    )

    assert created_with == [settings]
    assert context.requirement_parser._client is client
    assert context.planner._client is client
    assert context.repair_planner._client is client
