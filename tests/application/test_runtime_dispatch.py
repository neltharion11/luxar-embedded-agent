from luxar.application.runtime_dispatch import dispatch_runtime
from luxar.application.runtime_mode import select_firmware_runtime


def test_firmware_dispatch_uses_selected_runtime() -> None:
    supervisor = select_firmware_runtime({}, override="supervisor")
    legacy = select_firmware_runtime({}, override="legacy")

    assert dispatch_runtime("firmware", supervisor).workflow_family == (
        "supervisor_firmware"
    )
    assert dispatch_runtime("firmware", supervisor).uses_supervisor is True
    assert dispatch_runtime("firmware", legacy).workflow_family == (
        "legacy_firmware_rollback"
    )


def test_dedicated_workflows_are_not_counted_as_legacy_rollbacks() -> None:
    selection = select_firmware_runtime({}, override="supervisor")

    inspection = dispatch_runtime("inspection", selection)
    knowledge = dispatch_runtime("knowledge", selection)

    assert inspection.workflow_family == "project_inspection"
    assert knowledge.workflow_family == "knowledge_task"
    assert inspection.uses_supervisor is False
    assert knowledge.uses_supervisor is False
