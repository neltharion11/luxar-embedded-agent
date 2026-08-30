from __future__ import annotations

from pathlib import Path

from luxar.adapters.continuous_agent_tools import create_core_tool_registry
from luxar.adapters.local_driver_library import LocalDriverLibrary
from luxar.database.persistence import TransientPersistence
from luxar.domain.continuous_agent.steps import ToolCall
from luxar.ports.agent_tool import AgentToolExecutionContext


def test_driver_tools_search_read_and_approval_gated_publish(tmp_path: Path) -> None:
    component = tmp_path / "components" / "sensor"
    component.mkdir(parents=True)
    (component / "sensor.c").write_text("void sensor_init(void) {}\n", encoding="utf-8")
    library = LocalDriverLibrary(tmp_path / "public-library")
    persistence = TransientPersistence()
    registry = create_core_tool_registry(
        driver_library=library,
        persistence=persistence,
    )
    context = AgentToolExecutionContext(
        session_id="session-1",
        turn_id="turn-1",
        project_key="0:sensor",
        project_path=tmp_path,
    )
    publish = ToolCall(
        call_id="publish-1",
        tool_name="driver.publish",
        arguments={
            "driver_id": "acme.sensor.i2c",
            "version": "1.0.0",
            "name": "ACME Sensor",
            "vendor": "ACME",
            "hardware": "SENSOR1",
            "protocols": ["i2c"],
            "targets": ["esp32"],
            "description": "sensor driver",
            "file_paths": ["components/sensor/sensor.c"],
        },
    )

    waiting = registry.dispatch(publish, context)
    completed = registry.dispatch(publish, context, approved=True)
    search = registry.dispatch(ToolCall(
        call_id="search-1",
        tool_name="driver.search",
        arguments={"hardware": "SENSOR1", "protocol": "i2c"},
    ), context)
    read = registry.dispatch(ToolCall(
        call_id="read-1",
        tool_name="driver.read",
        arguments={"driver_id": "acme.sensor.i2c"},
    ), context)

    assert waiting.pending_approval is not None
    assert waiting.pending_approval.risk == "write"
    assert completed.call.status == "succeeded"
    assert search.call.result["count"] == 1  # type: ignore[index]
    assert "sensor_init" in read.call.result["sources"]["components/sensor/sensor.c"]  # type: ignore[index]
