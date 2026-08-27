from pathlib import Path

from luxar.adapters.deepseek.fake_client import FakeJsonCompletionClient
from luxar.adapters.deepseek.planner import DeepSeekPlanner
from luxar.adapters.deepseek.project_analyzer import DeepSeekProjectAnalyzer
from luxar.adapters.deepseek.knowledge_task_parser import (
    DeepSeekKnowledgeTaskParser,
)
from luxar.adapters.deepseek.repair_planner import DeepSeekRepairPlanner
from luxar.adapters.deepseek.requirement_parser import DeepSeekRequirementParser
from luxar.adapters.deepseek.settings import DeepSeekSettings
from luxar.adapters.espidf_cli import EspIdfCliAdapter
from luxar.adapters.espidf_project import EspIdfProjectAdapter
from luxar.adapters.fake_espidf import FakeEspIdf
from luxar.adapters.fake_project_creator import FakeProjectCreator
from luxar.adapters.fake_workspace import FakeWorkspace
from luxar.adapters.local_workspace import LocalWorkspaceAdapter
from luxar.bootstrap import (
    build_deepseek_runtime_context,
    build_deepseek_specialized_runtime_context,
)
from luxar.model_config import ModelEndpoint


def test_specialized_bootstrap_does_not_construct_firmware_execution_ports() -> None:
    client = FakeJsonCompletionClient([])
    workspace = FakeWorkspace([])

    context = build_deepseek_specialized_runtime_context(
        project_path=Path("firmware"),
        workspace=workspace,
        settings=DeepSeekSettings(
            api_key="test-key",
            fast_model="fast-test",
            repair_model="repair-test",
        ),
        client=client,
    )

    assert context.workspace is workspace
    assert isinstance(context.project_analyzer, DeepSeekProjectAnalyzer)
    assert isinstance(context.knowledge_task_parser, DeepSeekKnowledgeTaskParser)
    assert context.project_analyzer._client is client
    assert context.knowledge_task_parser._client is client
    assert not hasattr(context, "espidf")
    assert not hasattr(context, "planner")
    assert not hasattr(context, "flasher")


def test_specialized_bootstrap_selects_pdf_concurrency_from_model_endpoint() -> None:
    local = build_deepseek_specialized_runtime_context(
        project_path=Path("firmware"),
        settings=ModelEndpoint(
            provider="local",
            base_url="http://127.0.0.1:11434/v1",
            model="local-model",
        ),
        client=FakeJsonCompletionClient([]),
    )
    online = build_deepseek_specialized_runtime_context(
        project_path=Path("firmware"),
        settings=ModelEndpoint(
            provider="deepseek",
            api_key="test-key",
            model="deepseek-chat",
        ),
        client=FakeJsonCompletionClient([]),
    )

    assert local.document_analyzer._max_workers == 1  # type: ignore[union-attr]
    assert online.document_analyzer._max_workers == 4  # type: ignore[union-attr]


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


def test_bootstrap_constructs_production_tool_adapters_by_default() -> None:
    context = build_deepseek_runtime_context(
        project_path=Path("firmware"),
        settings=DeepSeekSettings(api_key="test-key"),
        client=FakeJsonCompletionClient([]),
    )

    assert isinstance(context.espidf, EspIdfCliAdapter)
    assert isinstance(context.workspace, LocalWorkspaceAdapter)
    assert context.espidf.allow_dependency_downloads is False


def test_bootstrap_passes_explicit_espidf_authorization_and_command() -> None:
    context = build_deepseek_runtime_context(
        project_path=Path("firmware"),
        settings=DeepSeekSettings(api_key="test-key"),
        client=FakeJsonCompletionClient([]),
        allow_dependency_downloads=True,
        idf_command=("trusted-python", "trusted-idf.py"),
    )

    assert isinstance(context.espidf, EspIdfCliAdapter)
    assert context.espidf.allow_dependency_downloads is True
    assert context.espidf.idf_command == (
        "trusted-python",
        "trusted-idf.py",
    )


def test_bootstrap_uses_explicit_idf_root_for_relative_example_library(
    tmp_path: Path,
    monkeypatch,
) -> None:
    explicit = tmp_path / "selected-idf"
    (explicit / "examples").mkdir(parents=True)
    stale = tmp_path / "stale-idf"
    (stale / "examples").mkdir(parents=True)
    monkeypatch.setenv("IDF_PATH", str(stale))

    context = build_deepseek_runtime_context(
        project_path=Path("firmware"),
        settings=DeepSeekSettings(api_key="test-key"),
        client=FakeJsonCompletionClient([]),
        idf_path=explicit,
    )

    assert context.example_library is not None
    assert context.example_library._idf_path == explicit.resolve()


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


def test_bootstrap_injects_project_creator_and_target_chip() -> None:
    creator = FakeProjectCreator([])
    context = build_deepseek_runtime_context(
        espidf=FakeEspIdf([]),
        workspace=FakeWorkspace([]),
        project_creator=creator,
        target_chip="esp32s3",
        project_path=Path("firmware"),
        settings=DeepSeekSettings(api_key="test-key"),
        client=FakeJsonCompletionClient([]),
    )

    assert context.project_creator is creator
    assert context.target_chip == "esp32s3"


def test_bootstrap_constructs_project_adapter_with_shared_launcher() -> None:
    context = build_deepseek_runtime_context(
        project_path=Path("firmware"),
        settings=DeepSeekSettings(api_key="test-key"),
        client=FakeJsonCompletionClient([]),
        idf_command=("trusted-python", "trusted-idf.py"),
    )

    assert isinstance(context.project_creator, EspIdfProjectAdapter)
    assert context.project_creator.idf_command == (
        "trusted-python",
        "trusted-idf.py",
    )
    assert context.target_chip is None


def test_bootstrap_injects_flasher_serial_port_and_checkpointer() -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    from luxar.adapters.fake_flasher import FakeFlasher

    flasher = FakeFlasher([])
    checkpointer = InMemorySaver()
    context = build_deepseek_runtime_context(
        espidf=FakeEspIdf([]),
        workspace=FakeWorkspace([]),
        project_path=Path("firmware"),
        flasher=flasher,
        serial_port="COM3",
        checkpointer=checkpointer,
        settings=DeepSeekSettings(api_key="test-key"),
        client=FakeJsonCompletionClient([]),
    )

    assert context.flasher is flasher
    assert context.serial_port == "COM3"
    assert context.checkpointer is checkpointer


def test_bootstrap_constructs_device_adapter_and_memory_checkpointer() -> None:
    from langgraph.checkpoint.memory import InMemorySaver

    from luxar.adapters.espidf_device import EspIdfDeviceAdapter

    context = build_deepseek_runtime_context(
        project_path=Path("firmware"),
        settings=DeepSeekSettings(api_key="test-key"),
        client=FakeJsonCompletionClient([]),
    )

    assert isinstance(context.flasher, EspIdfDeviceAdapter)
    assert context.monitor is context.flasher
    assert isinstance(context.checkpointer, InMemorySaver)
    assert context.serial_port is None
    assert context.monitor_timeout_seconds == 10


def test_bootstrap_injects_monitor_analyst_and_window() -> None:
    from luxar.adapters.deepseek.log_analyst import DeepSeekLogAnalyst
    from luxar.adapters.fake_log_analyst import FakeLogAnalyst
    from luxar.adapters.fake_monitor import FakeMonitor

    monitor = FakeMonitor([])
    analyst = FakeLogAnalyst([])
    context = build_deepseek_runtime_context(
        espidf=FakeEspIdf([]),
        workspace=FakeWorkspace([]),
        project_path=Path("firmware"),
        monitor=monitor,
        log_analyst=analyst,
        monitor_timeout_seconds=30,
        settings=DeepSeekSettings(api_key="test-key"),
        client=FakeJsonCompletionClient([]),
    )

    assert context.monitor is monitor
    assert context.log_analyst is analyst
    assert context.monitor_timeout_seconds == 30
    # 未注入分析师时默认使用修复级模型。
    default_context = build_deepseek_runtime_context(
        project_path=Path("firmware"),
        settings=DeepSeekSettings(api_key="test-key"),
        client=FakeJsonCompletionClient([]),
    )
    assert isinstance(default_context.log_analyst, DeepSeekLogAnalyst)
    assert default_context.log_analyst._model == "deepseek-v4-pro"


def test_discover_serial_ports_uses_default_device_adapter(
    monkeypatch,
) -> None:
    from luxar.bootstrap import discover_serial_ports

    class RecordingAdapter:
        def __init__(self) -> None:
            self.calls = 0

        def discover_serial_ports(self):
            self.calls += 1
            return []

    adapter = RecordingAdapter()
    monkeypatch.setattr(
        "luxar.bootstrap.EspIdfDeviceAdapter",
        lambda **kwargs: adapter,
    )

    ports = discover_serial_ports()

    assert ports == []
    assert adapter.calls == 1


def test_resolve_idf_command_prefers_known_install(monkeypatch) -> None:
    from luxar.bootstrap import resolve_idf_command

    monkeypatch.setenv("IDF_PATH", r"F:\esp\v6.0.2\esp-idf")
    monkeypatch.setenv(
        "IDF_PYTHON_ENV_PATH",
        r"F:\Espressif\tools\python\v6.0.2\venv",
    )

    command = resolve_idf_command()

    assert command[0].endswith("python.exe")
    assert command[1].endswith("idf.py")


def test_resolve_idf_command_falls_back_to_path(monkeypatch) -> None:
    from luxar.bootstrap import resolve_idf_command

    monkeypatch.delenv("IDF_PATH", raising=False)
    monkeypatch.delenv("IDF_PYTHON_ENV_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda command: "C:/tools/idf.py")

    command = resolve_idf_command()

    assert command == ("idf.py",)
