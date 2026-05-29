from __future__ import annotations

from luxar.domains.embedded.capability_map import CAPABILITY_MAP


def classify_embedded_task(task: str) -> dict[str, object]:
    task_lower = task.lower()
    capabilities: list[str] = []
    for capability, keywords in CAPABILITY_MAP.items():
        if any(keyword in task_lower for keyword in keywords):
            capabilities.append(capability)
    if not capabilities:
        capabilities.append("workspace")
    return {
        "domain": "embedded",
        "capabilities": capabilities,
        "primary_capability": capabilities[0],
    }
