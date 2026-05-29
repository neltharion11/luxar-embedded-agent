from __future__ import annotations


def explain_runtime_model() -> dict[str, object]:
    return {
        "runtime_model": "skill-first",
        "core_primitives": ["skills", "lessons", "memory", "workspace"],
        "policy": "controlled-autonomy",
        "notes": [
            "Harness is the runtime behavior system; skills are the only first-class procedural artifacts.",
            "Draft artifacts may be created automatically; validated promotion requires evidence.",
            "Hardware tasks should route through executable and recovery skills before app integration.",
        ],
    }
