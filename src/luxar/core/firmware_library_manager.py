from __future__ import annotations

import re
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

    def resolve_stm32_package_for_mcu(self, mcu: str) -> Path | None:
        family = self.infer_stm32_family(mcu)
        if family == "UNKNOWN":
            return None
        return self.resolve_stm32_package(f"STM32Cube_FW_{family}")

    def list_stm32_packages(self) -> list[str]:
        root = self.firmware_root / "stm32"
        if not root.exists():
            return []
        return sorted(path.name for path in root.iterdir() if path.is_dir())

    def infer_stm32_family(self, mcu: str) -> str:
        normalized = self._normalize_mcu(mcu)
        if normalized.startswith("STM32") and len(normalized) >= 8:
            family = normalized[5:7]
            if family.startswith("W"):
                family = normalized[5:8]
            return family
        return "UNKNOWN"

    def describe_stm32_package(self, package_path: str | Path, family: str | None = None) -> dict:
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
            "validation_errors": self.validate_stm32_package(package, family=family),
        }

    def validate_stm32_package(self, package_path: str | Path, family: str | None = None) -> list[str]:
        package = Path(package_path).resolve()
        errors: list[str] = []
        if self._is_placeholder_package(package):
            errors.append(f"Firmware package '{package.name}' is a placeholder; use a real STM32Cube package.")

        family_upper = (family or self._infer_family_from_package_name(package.name) or "F1").upper()
        device_dir_name = f"STM32{family_upper}xx"
        hal_dir_name = f"STM32{family_upper}xx_HAL_Driver"
        required_files = [
            package / "Drivers" / "CMSIS" / "Core" / "Include" / self._core_header_for_family(family_upper),
            package / "Drivers" / "CMSIS" / "Device" / "ST" / device_dir_name / "Include" / f"stm32{family_upper.lower()}xx.h",
            package / "Drivers" / hal_dir_name / "Inc" / f"stm32{family_upper.lower()}xx_hal.h",
            package / "Drivers" / hal_dir_name / "Inc" / f"stm32{family_upper.lower()}xx_hal_gpio.h",
            package / "Drivers" / hal_dir_name / "Src" / f"stm32{family_upper.lower()}xx_hal.c",
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

    def build_stm32_profile(self, mcu: str, package_path: str | Path | None = None) -> dict[str, str]:
        normalized = self._normalize_mcu(mcu)
        family = self.infer_stm32_family(normalized)
        if family == "UNKNOWN":
            raise ValueError(f"Cannot infer STM32 family from MCU '{mcu}'.")

        package = Path(package_path).resolve() if package_path else self.resolve_stm32_package_for_mcu(normalized)
        if package is None:
            expected = f"STM32Cube_FW_{family}"
            raise FileNotFoundError(
                f"No STM32Cube firmware package found for {mcu}. "
                f"Expected {expected} under {self.firmware_root / 'stm32'}."
            )

        validation_errors = self.validate_stm32_package(package, family=family)
        if validation_errors:
            raise ValueError("; ".join(validation_errors))

        device_define = self.stm32_device_define_from_mcu(normalized)
        startup_file = self.find_startup_file(package, family, device_define)
        linker_script = self.find_linker_script(package, family, device_define)
        system_file = self.find_system_file(package, family)
        if startup_file is None:
            raise FileNotFoundError(f"Startup file not found for {mcu} ({device_define}) in {package}.")
        if linker_script is None:
            raise FileNotFoundError(f"Linker script not found for {mcu} ({device_define}) in {package}.")
        if system_file is None:
            raise FileNotFoundError(f"System source file not found for STM32{family.upper()}xx in {package}.")

        cpu_flags = self.cpu_flags_for_family(family)
        return {
            "mcu": normalized,
            "family": family.upper(),
            "firmware_package": str(package),
            "device_define": device_define,
            "cmsis_device_dir": f"STM32{family.upper()}xx",
            "hal_driver_dir": f"STM32{family.upper()}xx_HAL_Driver",
            "startup_source": str(startup_file),
            "startup_file": startup_file.name,
            "linker_source": str(linker_script),
            "linker_script": linker_script.name,
            "system_source": str(system_file),
            "system_file": system_file.name,
            "cpu_flags": cpu_flags,
            "freertos_port": self.freertos_port_for_cpu_flags(cpu_flags),
        }

    def stm32_device_define_from_mcu(self, mcu: str) -> str:
        normalized = self._normalize_mcu(mcu)
        family = self.infer_stm32_family(normalized)
        if family == "F1":
            for prefix in ("STM32F100", "STM32F101", "STM32F102", "STM32F103"):
                if normalized.startswith(prefix):
                    flash_code_match = re.match(rf"{prefix}[A-Z]([0-9A-Z])", normalized)
                    if flash_code_match:
                        code = flash_code_match.group(1)
                        if code in {"4", "6"}:
                            return f"{prefix}x6"
                        if code in {"8", "B"}:
                            return f"{prefix}xB"
                        if code in {"C", "D", "E"}:
                            return f"{prefix}xE"
                        if code in {"F", "G"}:
                            return f"{prefix}xG"
                    return f"{prefix}xB"
            if normalized.startswith("STM32F105"):
                return "STM32F105xC"
            if normalized.startswith("STM32F107"):
                return "STM32F107xC"
        base_match = re.match(r"(STM32[A-Z][0-9][0-9][0-9])", normalized)
        if base_match:
            return f"{base_match.group(1)}xx"
        raise ValueError(f"Cannot infer STM32 device define from MCU '{mcu}'.")

    def find_startup_file(self, package_path: str | Path, family: str, device_define: str) -> Path | None:
        device_dir = self._device_dir(package_path, family)
        stem = device_define.lower()
        candidates = list(device_dir.rglob(f"startup_{stem}.s"))
        if candidates:
            return self._prefer_gcc_template(candidates).resolve()
        generic = re.sub(r"x[0-9a-z]$", "xx", stem)
        candidates = list(device_dir.rglob(f"startup_{generic}.s"))
        return self._prefer_gcc_template(candidates).resolve() if candidates else None

    def find_linker_script(self, package_path: str | Path, family: str, device_define: str) -> Path | None:
        device_dir = self._device_dir(package_path, family)
        upper = device_define.upper()
        candidates = list(device_dir.rglob(f"{upper}_FLASH.ld"))
        if candidates:
            return self._prefer_gcc_template(candidates).resolve()
        generic = re.sub(r"X[0-9A-Z]$", "XX", upper)
        candidates = list(device_dir.rglob(f"{generic}_FLASH.ld"))
        return self._prefer_gcc_template(candidates).resolve() if candidates else None

    def find_system_file(self, package_path: str | Path, family: str) -> Path | None:
        device_dir = self._device_dir(package_path, family)
        candidates = list(device_dir.rglob(f"system_stm32{family.lower()}xx.c"))
        return sorted(candidates, key=lambda p: len(str(p)))[0].resolve() if candidates else None

    def cpu_flags_for_family(self, family: str) -> str:
        family = family.upper()
        if family in {"F0", "G0", "L0", "C0"}:
            return "-mcpu=cortex-m0plus -mthumb -msoft-float"
        if family in {"F1", "F2", "L1", "W1"}:
            return "-mcpu=cortex-m3 -mthumb -msoft-float"
        if family in {"F3", "F4", "G4", "L4", "L4P", "WB", "WL"}:
            return "-mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard"
        if family == "F7":
            return "-mcpu=cortex-m7 -mthumb -mfpu=fpv5-sp-d16 -mfloat-abi=hard"
        if family == "H7":
            return "-mcpu=cortex-m7 -mthumb -mfpu=fpv5-d16 -mfloat-abi=hard"
        if family in {"H5", "L5", "U5", "WBA"}:
            return "-mcpu=cortex-m33 -mthumb -msoft-float"
        raise ValueError(f"Unsupported STM32 family '{family}' for GCC CPU flags.")

    def freertos_port_for_cpu_flags(self, cpu_flags: str) -> str:
        if "cortex-m0" in cpu_flags:
            return "ARM_CM0"
        if "cortex-m3" in cpu_flags:
            return "ARM_CM3"
        if "cortex-m4" in cpu_flags:
            return "ARM_CM4F" if "-mfloat-abi=hard" in cpu_flags else "ARM_CM4_MPU"
        if "cortex-m7" in cpu_flags:
            return "ARM_CM7/r0p1"
        if "cortex-m33" in cpu_flags:
            return "ARM_CM33/non_secure"
        raise ValueError(f"Unsupported FreeRTOS port for CPU flags '{cpu_flags}'.")

    def stage_stm32_firmware_package(
        self,
        package_path: str | Path,
        project_path: str | Path,
        mcu: str = "",
    ) -> list[str]:
        package = Path(package_path).resolve()
        project = Path(project_path).resolve()
        created: list[str] = []

        family = self.infer_stm32_family(mcu) if mcu else self._infer_family_from_package_name(package.name) or "F1"
        validation_errors = self.validate_stm32_package(package, family=family)
        if validation_errors:
            raise ValueError("; ".join(validation_errors))

        drivers_src = package / "Drivers"
        if not drivers_src.exists():
            return created

        drivers_dst = project / "Drivers"
        drivers_dst.mkdir(parents=True, exist_ok=True)

        for child_name in ("CMSIS",):
            src = drivers_src / child_name
            dst = drivers_dst / child_name
            if src.exists() and not dst.exists():
                shutil.copytree(src, dst)
                created.append(str(dst))

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

        device_src = drivers_src / "CMSIS" / "Device" / "ST" / f"STM32{family.upper()}xx"
        if device_src.exists():
            device_dst = drivers_dst / "CMSIS" / "Device" / "ST" / device_src.name
            if not device_dst.exists():
                shutil.copytree(device_src, device_dst)
                created.append(str(device_dst))

        hal_src = drivers_src / f"STM32{family.upper()}xx_HAL_Driver"
        if hal_src.exists():
            dst = drivers_dst / hal_src.name
            if not dst.exists():
                shutil.copytree(hal_src, dst)
                created.append(str(dst))

        return created

    def collect_stm32_build_context(self, package_path: str | Path, family: str) -> dict:
        package = Path(package_path).resolve()
        drivers_dir = package / "Drivers"
        cmsis_dir = drivers_dir / "CMSIS"
        family_upper = family.upper()
        hal_dir = drivers_dir / f"STM32{family_upper}xx_HAL_Driver"
        device_include = cmsis_dir / "Device" / "ST" / f"STM32{family_upper}xx" / "Include"
        hal_inc = hal_dir / "Inc"
        hal_src = hal_dir / "Src"

        return {
            "cmsis_include": str((cmsis_dir / "Include").resolve()) if (cmsis_dir / "Include").exists() else "",
            "device_include": str(device_include.resolve()) if device_include.exists() else "",
            "hal_include": str(hal_inc.resolve()) if hal_inc.exists() else "",
            "hal_source_dir": str(hal_src.resolve()) if hal_src.exists() else "",
            "hal_driver_dir": str(hal_dir.resolve()) if hal_dir.exists() else "",
            "family_define": f"STM32{family_upper}xx" if family_upper != "UNKNOWN" else "",
        }

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

    def _infer_family_from_package_name(self, package: str) -> str | None:
        match = re.search(r"STM32Cube_FW_([A-Z0-9]+)", package, re.IGNORECASE)
        return match.group(1).upper() if match else None

    def _normalize_mcu(self, mcu: str) -> str:
        return mcu.upper().replace("-", "").replace("_", "").strip()

    def _core_header_for_family(self, family: str) -> str:
        family = family.upper()
        if family in {"F0", "G0", "L0", "C0"}:
            return "core_cm0plus.h"
        if family in {"F1", "F2", "L1", "W1"}:
            return "core_cm3.h"
        if family in {"F3", "F4", "G4", "L4", "L4P", "WB", "WL"}:
            return "core_cm4.h"
        if family in {"F7", "H7"}:
            return "core_cm7.h"
        if family in {"H5", "L5", "U5", "WBA"}:
            return "core_cm33.h"
        return "cmsis_gcc.h"

    def _device_dir(self, package_path: str | Path, family: str) -> Path:
        package = Path(package_path).resolve()
        return package / "Drivers" / "CMSIS" / "Device" / "ST" / f"STM32{family.upper()}xx"

    def _prefer_gcc_template(self, candidates: list[Path]) -> Path:
        return sorted(
            candidates,
            key=lambda p: (0 if any(part.lower() == "gcc" for part in p.parts) else 1, len(str(p))),
        )[0]
