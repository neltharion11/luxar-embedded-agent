from __future__ import annotations


def recovery_hint(task: str) -> dict[str, object]:
    return {
        "route": "recovery",
        "task": task,
        "message": "Repeated failures should create or patch a recovery harness instead of blind retries.",
    }
