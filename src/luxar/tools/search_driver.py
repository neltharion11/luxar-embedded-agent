from __future__ import annotations

from pathlib import Path

from luxar.core.config_manager import AgentConfig
from luxar.core.driver_library import DriverLibrary


def run_search_driver(
    config: AgentConfig,
    project_root: str,
    keyword: str = "",
    protocol: str = "",
    vendor: str = "",
    limit: int = 20,
):
    root = Path(project_root).resolve()
    library = DriverLibrary(root / config.agent.driver_library)
    results = library.search_drivers(
        keyword=keyword,
        protocol=protocol,
        vendor=vendor,
        limit=limit,
    )
    return {
        "keyword": keyword,
        "protocol": protocol,
        "vendor": vendor,
        "limit": limit,
        "results": [item.model_dump(mode="json") for item in results],
        "stats": library.stats(),
    }

