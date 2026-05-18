from __future__ import annotations

from luxar.domains.embedded.task_classifier import classify_embedded_task


def build_runtime_plan(task: str) -> dict[str, object]:
    classification = classify_embedded_task(task)
    return {
        "task": task,
        "classification": classification,
        "loop": [
            "observe",
            "classify",
            "search_skills",
            "search_lessons",
            "select_harness",
            "act",
            "validate",
            "record_evidence",
            "patch_or_promote",
            "continue_or_escalate",
        ],
    }
