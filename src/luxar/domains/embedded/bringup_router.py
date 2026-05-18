from __future__ import annotations


def bringup_hint(task: str) -> dict[str, object]:
    return {
        "route": "bringup",
        "task": task,
        "message": "Prefer a minimal bring-up harness before application integration.",
    }
