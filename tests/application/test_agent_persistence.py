from luxar.application.agent_persistence import (
    load_agent_interactions,
    load_agent_snapshot,
    save_agent_snapshot,
)
from luxar.database import TransientPersistence
from luxar.domain.agent.capabilities import ProjectCapability
from luxar.domain.agent.changes import CapabilityChange, ChangeSet
from luxar.domain.agent.code_changes import ChangeBundleValidation
from luxar.domain.agent.objectives import ProjectObjective


def test_agent_snapshot_bridge_round_trips_typed_state_and_interaction() -> None:
    persistence = TransientPersistence()
    state = {
        "objective": ProjectObjective(
            objective_id="obj-bridge",
            title="t2",
            description="add P33",
            revision=2,
        ),
        "change_set": ChangeSet(
            changes=[
                CapabilityChange(
                    operation="add",
                    capability_id="gpio.output:P33",
                    desired_state={"pin": 33, "level": 1},
                )
            ]
        ),
        "capabilities": [
            ProjectCapability(
                capability_id="gpio.output:P13",
                kind="gpio.output",
                parameters={"pin": 13, "level": 1},
            )
        ],
        "change_validations": {
            "obj-bridge:code:add:gpio.output_P33": ChangeBundleValidation(
                before_fingerprint="a" * 64,
                after_fingerprint="b" * 64,
                changed_files=["main/t2.c"],
                diff_summary=["modify: main/t2.c"],
            )
        },
    }

    save_agent_snapshot(
        persistence,
        "0:t2",
        state,
        interaction={
            "interaction_id": "i-1",
            "kind": "change_objective",
            "payload": {"message": "新增 P33 高电平"},
        },
    )

    restored = load_agent_snapshot(persistence, "0:t2")
    interactions = load_agent_interactions(persistence, "0:t2")
    assert restored["objective"].revision == 2
    assert restored["change_set"].changes[0].capability_id == "gpio.output:P33"
    assert restored["capabilities"][0].capability_id == "gpio.output:P13"
    assert {item.kind for item in interactions} == {
        "change_objective",
        "change_applied",
    }
    change_record = next(item for item in interactions if item.kind == "change_applied")
    assert change_record.payload["changed_files"] == ["main/t2.c"]
