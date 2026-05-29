from __future__ import annotations

import re
from pathlib import Path


# ── MCU mapping from compiler flags ──
_CPU_TO_MCU: dict[str, str] = {
    "cortex-m0": "STM32F030C8",
    "cortex-m0plus": "STM32G030C8",
    "cortex-m3": "STM32F103C8",
    "cortex-m4": "STM32F407VG",
    "cortex-m7": "STM32H743ZI",
}

# STM32 family prefix from device defines
_DEFINE_FAMILY_PREFIX: dict[str, str] = {
    "STM32F0": "STM32F0",
    "STM32F1": "STM32F1",
    "STM32F2": "STM32F2",
    "STM32F3": "STM32F3",
    "STM32F4": "STM32F4",
    "STM32F7": "STM32F7",
    "STM32G0": "STM32G0",
    "STM32G4": "STM32G4",
    "STM32H7": "STM32H7",
    "STM32L0": "STM32L0",
    "STM32L1": "STM32L1",
    "STM32L4": "STM32L4",
    "STM32WB": "STM32WB",
}

_STM32_RE = re.compile(r"STM32[FLGWH]\d{2,3}", re.IGNORECASE)


def detect_project(project_path: str | Path) -> dict[str, str]:
    """Detect MCU, platform, runtime, and project_mode from project files.

    Returns a dict with keys: mcu, platform, runtime, project_mode.
    """
    proj = Path(project_path)
    if not proj.is_dir():
        return _defaults()

    mcu = _detect_mcu(proj)
    platform = _detect_platform(proj)
    runtime = _detect_runtime(proj)
    project_mode = "cubemx" if platform == "stm32cubemx" else "firmware"

    return {
        "mcu": mcu,
        "platform": platform,
        "runtime": runtime,
        "project_mode": project_mode,
    }


def _defaults() -> dict[str, str]:
    return {"mcu": "STM32F103C8", "platform": "stm32firmware", "runtime": "baremetal", "project_mode": "firmware"}


# ── MCU detection ──

def _detect_mcu(proj: Path) -> str:
    # 1. .ioc file
    for ioc in proj.glob("*.ioc"):
        mcu = _parse_ioc_mcu(ioc)
        if mcu:
            return mcu

    # 2. CMakeLists.txt
    cmake = proj / "CMakeLists.txt"
    if cmake.exists():
        mcu = _parse_cmake_mcu(cmake)
        if mcu:
            return mcu

    # 3. Source file names
    mcu = _detect_from_filenames(proj)
    if mcu:
        return mcu

    # 4. platformio.ini
    pio = proj / "platformio.ini"
    if pio.exists():
        mcu = _parse_platformio_mcu(pio)
        if mcu:
            return mcu

    # 5. Makefile
    makefile = proj / "Makefile"
    if makefile.exists():
        mcu = _parse_makefile_mcu(makefile)
        if mcu:
            return mcu

    return "STM32F103C8"


def _parse_ioc_mcu(ioc_path: Path) -> str | None:
    try:
        text = ioc_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    # Mcu.Name=STM32F103C(8-B)Tx or ProjectManager.DeviceId=STM32F103C8Tx
    mcu_name = None
    for line in text.splitlines():
        if line.startswith("ProjectManager.DeviceId="):
            return line.split("=", 1)[1].strip()
        if line.startswith("Mcu.Name=") and mcu_name is None:
            raw = line.split("=", 1)[1].strip()
            # Get the number/letter parts inside parentheses to reconstruct
            # e.g. STM32F103C(8-B)Tx -> STM32F103C8Tx
            inner = re.search(r"\(([^)]+)\)", raw)
            if inner:
                parts = inner.group(1).split("-")
                raw = raw[:inner.start()] + parts[0] + raw[inner.end():]
            cleaned = raw.strip("-_ ")
            mcu_name = cleaned
    if mcu_name:
        return mcu_name
    return None


def _parse_cmake_mcu(cmake_path: Path) -> str | None:
    try:
        text = cmake_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    cpu = None
    for m in re.finditer(r"-mcpu=cortex-m\d\+?", text):
        cpu = m.group(0).split("=", 1)[1]
        break

    # Check for STM32 define
    stm_defines: list[str] = []
    for m in re.finditer(r"-DSTM32(\w+)", text):
        stm_defines.append(f"STM32{m.group(1)}")

    # Check for family references
    families_found: list[str] = []
    for key in _DEFINE_FAMILY_PREFIX:
        if key.lower() in text.lower():
            families_found.append(key)

    # Specific device from define like STM32F103xB
    for d in stm_defines:
        if d.endswith("xB") or d.endswith("x8") or d.endswith("xE") or d.endswith("xC"):
            base = d[:-2]  # strip xB suffix
            suffix = d[-1]  # B/8/E/C
            return f"{base}{'C8' if suffix in '8B' else suffix + suffix}"

    if families_found:
        fam = families_found[0]
        if fam == "STM32F1":
            return "STM32F103C8"
        elif fam == "STM32F4":
            return "STM32F407VG"
        elif fam == "STM32F0":
            return "STM32F030C8"
        else:
            return fam + "xx"

    if cpu:
        return _CPU_TO_MCU.get(cpu, "STM32F103C8")

    return None


def _detect_from_filenames(proj: Path) -> str | None:
    family = None
    device = None

    for f in proj.rglob("system_stm32*.c"):
        m = re.search(r"system_stm32(f\d)\w*\.c", f.name, re.IGNORECASE)
        if m:
            family = f"STM32{m.group(1).upper()}"
            break

    for f in proj.rglob("startup_stm32*.s"):
        m = re.search(r"startup_stm32(f\d{2,3})\w*\.s", f.name, re.IGNORECASE)
        if m:
            device = f"STM32{m.group(1).upper()}"
            break

    if family and device:
        return device
    if family:
        if family == "STM32F1":
            return "STM32F103C8"
        elif family == "STM32F4":
            return "STM32F407VG"
        else:
            return family + "xx"
    if device:
        return device

    return None


def _parse_platformio_mcu(pio_path: Path) -> str | None:
    try:
        text = pio_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("board_build.mcu"):
            return line.split("=", 1)[1].strip()
        if line.startswith("board = "):
            board = line.split("=", 1)[1].strip()
            # Some common platformio board names map to MCUs
            return board
    return None


def _parse_makefile_mcu(makefile_path: Path) -> str | None:
    try:
        text = makefile_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    # Look for -mcpu and -D defines
    stm_defines = []
    for m in re.finditer(r"-DSTM32(\w+)", text):
        stm_defines.append(f"STM32{m.group(1)}")

    for d in stm_defines:
        if d.endswith("xB") or d.endswith("x8") or d.endswith("xE"):
            base = d[:-2]
            return f"{base}C8"

    return None


# ── Platform detection ──

def _detect_platform(proj: Path) -> str:
    if any(proj.glob("*.ioc")):
        return "stm32cubemx"
    return "stm32firmware"


# ── Runtime detection ──

def _detect_runtime(proj: Path) -> str:
    freertos_indicators = [
        "FreeRTOS",
        "freertos.c",
        "FreeRTOSConfig.h",
        "FreeRTOS.h",
    ]
    for pattern in freertos_indicators:
        if list(proj.rglob(pattern)):
            return "freertos"
    # Also check CMakeLists.txt for FreeRTOS references
    cmake = proj / "CMakeLists.txt"
    if cmake.exists():
        try:
            text = cmake.read_text(encoding="utf-8", errors="replace").lower()
            if "freertos" in text:
                return "freertos"
        except Exception:
            pass
    return "baremetal"
