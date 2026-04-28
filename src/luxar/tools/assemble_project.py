from __future__ import annotations

from luxar.core.assembler import Assembler
from luxar.core.firmware_library_manager import FirmwareLibraryManager
from luxar.core.driver_library import DriverLibrary
from luxar.models.schemas import ProjectConfig


def run_assemble_project(
    project: ProjectConfig,
    firmware_library_root: str,
    driver_library_root: str = "",
    drivers: list[str] | None = None,
) -> dict:
    assembler = Assembler()
    firmware_package_resolved = ""
    stm32_family = ""
    firmware_description: dict = {}
    installed_drivers: list[dict] = []
    if project.project_mode == "firmware":
        if not project.firmware_package:
            raise ValueError("Firmware project mode requires a firmware package.")
        manager = FirmwareLibraryManager(firmware_library_root)
        resolved = manager.resolve_stm32_package(project.firmware_package)
        if resolved is None:
            raise FileNotFoundError(
                f"Firmware package not found: {project.firmware_package}"
            )
        firmware_package_resolved = str(resolved)
        stm32_family = manager.infer_stm32_family(project.mcu)
        firmware_description = manager.describe_stm32_package(resolved)
        build_context = manager.collect_stm32_build_context(resolved, stm32_family)
        staged_firmware_paths = manager.stage_stm32_firmware_package(
            package_path=resolved,
            project_path=project.path,
            mcu=project.mcu,
        )
        created_files = assembler.assemble_stm32_firmware_project(
            project,
            firmware_package=firmware_package_resolved,
            stm32_family=stm32_family,
            build_context=build_context,
            staged_firmware_paths=staged_firmware_paths,
        )
    else:
        created_files = assembler.assemble_minimal_app(project)

    if drivers:
        if not driver_library_root:
            raise ValueError("Driver library root is required when assembling with drivers.")
        library = DriverLibrary(driver_library_root)
        resolved_drivers = []
        for query in drivers:
            resolved = library.resolve_driver(query)
            if resolved is None:
                raise FileNotFoundError(f"Stored driver not found for query: {query}")
            resolved_drivers.append(resolved)
        created_files.extend(assembler.install_driver_records(project, resolved_drivers))
        installed_drivers = [item.model_dump(mode="json") for item in resolved_drivers]

    return {
        "project": project.name,
        "project_path": project.path,
        "project_mode": project.project_mode,
        "firmware_package": firmware_package_resolved,
        "stm32_family": stm32_family,
        "firmware_description": firmware_description,
        "build_context": build_context if project.project_mode == "firmware" else {},
        "installed_drivers": installed_drivers,
        "created_files": created_files,
        "created_count": len(created_files),
    }


