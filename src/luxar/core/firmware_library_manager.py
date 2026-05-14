from __future__ import annotations

import shutil
from pathlib import Path


class FirmwareLibraryManager:
    def __init__(self, firmware_root: str | Path):
        self.firmware_root = Path(firmware_root).resolve()

    def resolve_stm32_package(self, package: str) -> Path | None:
        candidate = Path(package)
        if candidate.exists():
            resolved = candidate.resolve()
            if self._is_placeholder_package(resolved):
                replacement = self._find_versioned_package(resolved.name)
                return replacement or resolved
            return resolved

        named = self.firmware_root / "stm32" / package
        if named.exists():
            resolved = named.resolve()
            if self._is_placeholder_package(resolved):
                replacement = self._find_versioned_package(package)
                return replacement or resolved
            return resolved
        replacement = self._find_versioned_package(package)
        if replacement is not None:
            return replacement
        return None

    def list_stm32_packages(self) -> list[str]:
        root = self.firmware_root / "stm32"
        if not root.exists():
            return []
        return sorted(path.name for path in root.iterdir() if path.is_dir())

    def infer_stm32_family(self, mcu: str) -> str:
        normalized = mcu.upper().replace("-", "")
        if normalized.startswith("STM32") and len(normalized) >= 8:
            family = normalized[5:7]
            if family.startswith("W"):
                family = normalized[5:8]
            return family
        return "UNKNOWN"

    def describe_stm32_package(self, package_path: str | Path) -> dict:
        package = Path(package_path).resolve()
        drivers_dir = package / "Drivers"
        cmsis_dir = drivers_dir / "CMSIS"
        hal_candidates = sorted(drivers_dir.glob("STM32*HAL_Driver"))
        return {
            "package_path": str(package),
            "drivers_dir": str(drivers_dir),
            "cmsis_dir": str(cmsis_dir),
            "hal_drivers": [str(path) for path in hal_candidates],
            "has_drivers": drivers_dir.exists(),
            "has_cmsis": cmsis_dir.exists(),
            "has_hal_driver": bool(hal_candidates),
            "is_placeholder": self._is_placeholder_package(package),
            "validation_errors": self.validate_stm32_package(package),
        }

    def validate_stm32_package(self, package_path: str | Path) -> list[str]:
        package = Path(package_path).resolve()
        errors: list[str] = []
        if self._is_placeholder_package(package):
            errors.append(f"Firmware package '{package.name}' is a placeholder; use a real STM32Cube package.")
        required_files = [
            package / "Drivers" / "CMSIS" / "Core" / "Include" / "core_cm3.h",
            package / "Drivers" / "CMSIS" / "Device" / "ST" / "STM32F1xx" / "Include" / "stm32f1xx.h",
            package / "Drivers" / "STM32F1xx_HAL_Driver" / "Inc" / "stm32f1xx_hal.h",
            package / "Drivers" / "STM32F1xx_HAL_Driver" / "Inc" / "stm32f1xx_hal_gpio.h",
            package / "Drivers" / "STM32F1xx_HAL_Driver" / "Src" / "stm32f1xx_hal.c",
        ]
        for path in required_files:
            if not path.exists():
                errors.append(f"Required firmware file missing: {path}")
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")[:512]
            except Exception:
                text = ""
            if "Placeholder" in text or "placeholder" in text:
                errors.append(f"Required firmware file is a placeholder: {path}")
        return errors

    def stage_stm32_firmware_package(
        self,
        package_path: str | Path,
        project_path: str | Path,
        mcu: str = "",
    ) -> list[str]:
        package = Path(package_path).resolve()
        project = Path(project_path).resolve()
        created: list[str] = []

        validation_errors = self.validate_stm32_package(package)
        if validation_errors:
            raise ValueError("; ".join(validation_errors))

        drivers_src = package / "Drivers"
        if not drivers_src.exists():
            return created

        drivers_dst = project / "Drivers"
        drivers_dst.mkdir(parents=True, exist_ok=True)

        # Copy common firmware resources when present.
        for child_name in ("CMSIS",):
            src = drivers_src / child_name
            dst = drivers_dst / child_name
            if src.exists() and not dst.exists():
                shutil.copytree(src, dst)
                created.append(str(dst))

        # Also copy CMSIS Core headers (cmsis_gcc.h, cmsis_compiler.h etc.)
        # if the firmware package has a Core/ subdirectory.
        cmsis_core_include = drivers_src / "CMSIS" / "Core" / "Include"
        if cmsis_core_include.exists():
            dst_core = drivers_dst / "CMSIS" / "Core" / "Include"
            dst_core.mkdir(parents=True, exist_ok=True)
            for item in cmsis_core_include.iterdir():
                target = dst_core / item.name
                if item.is_file() and not target.exists():
                    shutil.copy2(item, target)
                elif item.is_dir() and not target.exists():
                    shutil.copytree(item, target)
            created.append(str(dst_core))

        # Copy CMSIS Device headers (stm32f1xx.h, system_stm32f1xx.h, startup)
        family = self.infer_stm32_family(mcu) if mcu else "F1"
        device_src_candidates = [
            drivers_src / "CMSIS" / "Device" / "ST" / f"STM32{family}xx",
        ]
        if family != "F1":
            device_src_candidates.append(
                drivers_src / "CMSIS" / "Device" / "ST" / f"STM32{family.upper()}xx"
            )
        for device_src in device_src_candidates:
            if device_src.exists():
                device_dst = drivers_dst / "CMSIS" / "Device" / "ST" / device_src.name
                if not device_dst.exists():
                    shutil.copytree(device_src, device_dst)
                    created.append(str(device_dst))
                break

        for hal_dir in drivers_src.glob("STM32*HAL_Driver"):
            dst = drivers_dst / hal_dir.name
            if hal_dir.is_dir() and not dst.exists():
                shutil.copytree(hal_dir, dst)
                created.append(str(dst))

        return created

    def _find_versioned_package(self, package: str) -> Path | None:
        root = self.firmware_root / "stm32"
        if not root.exists():
            return None
        prefix = package.rstrip("\\/")
        candidates = sorted(
            [path for path in root.iterdir() if path.is_dir() and path.name.startswith(f"{prefix}_V")],
            key=lambda path: path.name,
            reverse=True,
        )
        return candidates[0].resolve() if candidates else None

    def _is_placeholder_package(self, package: Path) -> bool:
        readme = package / "README.md"
        if not readme.exists():
            return False
        try:
            text = readme.read_text(encoding="utf-8", errors="ignore").lower()
        except Exception:
            return False
        return "placeholder" in text and "replace this folder" in text

    def collect_stm32_build_context(self, package_path: str | Path, family: str) -> dict:
        package = Path(package_path).resolve()
        drivers_dir = package / "Drivers"
        cmsis_dir = drivers_dir / "CMSIS"
        hal_dirs = sorted(drivers_dir.glob("STM32*HAL_Driver"))

        family_upper = family.upper()
        device_include_candidates = [
            cmsis_dir / "Device" / "ST" / f"STM32{family_upper}xx" / "Include",
            cmsis_dir / "Device" / "ST" / f"STM32{family_upper}XX" / "Include",
        ]
        device_include = next((p for p in device_include_candidates if p.exists()), None)

        hal_inc = None
        hal_src = None
        hal_dir = hal_dirs[0] if hal_dirs else None
        if hal_dir:
            inc = hal_dir / "Inc"
            src = hal_dir / "Src"
            hal_inc = inc if inc.exists() else None
            hal_src = src if src.exists() else None

        return {
            "cmsis_include": str((cmsis_dir / "Include").resolve()) if (cmsis_dir / "Include").exists() else "",
            "device_include": str(device_include.resolve()) if device_include else "",
            "hal_include": str(hal_inc.resolve()) if hal_inc else "",
            "hal_source_dir": str(hal_src.resolve()) if hal_src else "",
            "hal_driver_dir": str(hal_dir.resolve()) if hal_dir else "",
            "family_define": f"STM32{family_upper}xx" if family_upper != "UNKNOWN" else "",
        }
