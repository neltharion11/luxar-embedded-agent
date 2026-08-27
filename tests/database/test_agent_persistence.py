from pathlib import Path

from luxar.database import SQLitePersistence, TransientPersistence
from luxar.domain.agent.capabilities import ProjectCapability
from luxar.domain.agent.changes import CapabilityChange, ChangeSet
from luxar.domain.agent.objectives import ProjectObjective


def _agent_payload() -> tuple[dict[str, object], dict[str, object], list[dict[str, object]]]:
    objective = ProjectObjective(
        objective_id="obj-1",
        title="environment node",
        description="keep P13 and add P33",
        source_message_ids=["m-1"],
        revision=2,
    )
    change_set = ChangeSet(
        changes=[
            CapabilityChange(
                operation="add",
                capability_id="gpio.output:P33",
                desired_state={"pin": 33, "level": 1},
            ),
            CapabilityChange(
                operation="preserve",
                capability_id="gpio.output:P13",
            ),
        ]
    )
    capabilities = [
        ProjectCapability(
            capability_id="gpio.output:P13",
            kind="gpio.output",
            parameters={"pin": 13, "level": 1},
            evidence_ids=["source:main/t2.c:gpio13"],
            source_paths=["main/t2.c"],
        ).model_dump(mode="json")
    ]
    return objective.model_dump(mode="json"), change_set.model_dump(mode="json"), capabilities


def _assert_round_trip(repository) -> None:
    objective, change_set, capabilities = _agent_payload()
    repository.save_agent_project(
        project_key="0:t2",
        objective=objective,
        change_set=change_set,
        revision=2,
        capabilities=capabilities,
    )
    repository.append_agent_interaction(
        interaction_id="interaction-1",
        project_key="0:t2",
        objective_id="obj-1",
        kind="change_objective",
        payload={"message": "新增 P33 高电平"},
    )
    # 同一交互 ID 重放不产生重复记录。
    repository.append_agent_interaction(
        interaction_id="interaction-1",
        project_key="0:t2",
        objective_id="obj-1",
        kind="change_objective",
        payload={"message": "duplicate"},
    )

    record = repository.get_agent_project("0:t2")
    interactions = repository.get_agent_interactions("0:t2")

    assert record is not None
    assert record.revision == 2
    assert record.objective["objective_id"] == "obj-1"
    assert record.change_set["changes"][0]["capability_id"] == "gpio.output:P33"
    assert record.capabilities[0]["capability_id"] == "gpio.output:P13"
    assert len(interactions) == 1
    assert interactions[0].payload == {"message": "新增 P33 高电平"}


def test_transient_agent_project_round_trip() -> None:
    _assert_round_trip(TransientPersistence())


def test_sqlite_agent_project_survives_reopen(tmp_path: Path) -> None:
    database = tmp_path / "luxar-agent.sqlite3"
    _assert_round_trip(SQLitePersistence(database))

    reopened = SQLitePersistence(database)
    record = reopened.get_agent_project("0:t2")
    assert record is not None
    assert record.revision == 2
    assert [item.interaction_id for item in reopened.get_agent_interactions("0:t2")] == [
        "interaction-1"
    ]


def test_sqlite_agent_project_update_replaces_capability_snapshot(tmp_path: Path) -> None:
    repository = SQLitePersistence(tmp_path / "luxar-agent-update.sqlite3")
    objective, change_set, capabilities = _agent_payload()
    repository.save_agent_project(
        project_key="0:t2",
        objective=objective,
        change_set=change_set,
        revision=2,
        capabilities=capabilities,
    )
    repository.save_agent_project(
        project_key="0:t2",
        objective={**objective, "revision": 3},
        change_set=change_set,
        revision=3,
        capabilities=[],
    )

    record = repository.get_agent_project("0:t2")
    assert record is not None
    assert record.revision == 3
    assert record.capabilities == []


def test_transient_workbench_snapshot_tracks_latest_workflow_family() -> None:
    repository = TransientPersistence()
    repository.save_workbench_snapshot(
        project_key="0:t2",
        workflow_family="knowledge_task",
        thread_id="knowledge-1",
        snapshot={"status": "running"},
    )
    repository.save_workbench_snapshot(
        project_key="0:t2",
        workflow_family="supervisor_firmware",
        thread_id="firmware-2",
        snapshot={},
    )

    record = repository.get_workbench_snapshot("0:t2")

    assert record is not None
    assert record.workflow_family == "supervisor_firmware"
    assert record.thread_id == "firmware-2"


def test_sqlite_workbench_snapshot_survives_reopen(tmp_path: Path) -> None:
    database = tmp_path / "luxar-workbench.sqlite3"
    repository = SQLitePersistence(database)
    repository.save_workbench_snapshot(
        project_key="0:t2",
        workflow_family="knowledge_task",
        thread_id="knowledge-1",
        snapshot={"status": "awaiting_user"},
    )

    record = SQLitePersistence(database).get_workbench_snapshot("0:t2")

    assert record is not None
    assert record.workflow_family == "knowledge_task"
    assert record.thread_id == "knowledge-1"
    assert record.snapshot == {"status": "awaiting_user"}
