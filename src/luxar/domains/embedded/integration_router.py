from __future__ import annotations


def integration_hint(task: str) -> dict[str, object]:
    return {
        "route": "integration",
        "task": task,
        "message": "Integrate business behavior only after the selected harness succeeds.",
    }
