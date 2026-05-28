"""Driver library indexing and metadata extraction."""
from __future__ import annotations
import hashlib
import re
from pathlib import Path

from luxar.core.driver_library import DriverLibrary
from luxar.models.schemas import DriverMetadata

# ── Hardcoded whitelists (priority match) ──
_PROTO_WHITELIST = {
    "I2C": [r"\bI2C\b"],
    "SPI": [r"\bSPI\b"],
    "UART": [r"\bUART\b", r"\bUSART\b"],
    "GPIO": [r"\bGPIO\b"],
}
_VENDOR_WHITELIST = {
    "ST": [r"\bSTM32\b"],
    "WCH": [r"\bCH32\b", r"\bCH1116\b"],
}
_CHIP_WHITELIST = [
    r"\bCH1116\b", r"\bSH1106\b", r"\bSSD1306\b",
    r"\bHC.SR04\b", r"\bWS2812B?\b", r"\bEMB.001\b",
    r"\bSTM32F\d+\b", r"\bAHT20\b",
]
_DEVICE_WHITELIST = [
    (r"OLED|display", "Display"),
    (r"ultrasonic|distance|sensor", "Sensor"),
    (r"LED|RGB", "LED"),
    (r"UART|serial|comm", "Communication"),
    (r"temperature|humidity|sensor|AHT", "Sensor"),
]

# ── Generic detection patterns ──
_GENERIC_CHIP_RE = re.compile(r"\b([A-Z]{2,}\d+[A-Za-z]?)\b")  # e.g. BMP280, AHT20, CH1116
_HEADER_INCLUDE_RE = {
    "I2C": re.compile(r'#include\s+[<\"].*i2c.*\.h[>\"]', re.IGNORECASE),
    "SPI": re.compile(r'#include\s+[<\"].*spi.*\.h[>\"]', re.IGNORECASE),
    "UART": re.compile(r'#include\s+[<\"].*(?:uart|usart).*\.h[>\"]', re.IGNORECASE),
    "GPIO": re.compile(r'#include\s+[<\"].*gpio.*\.h[>\"]', re.IGNORECASE),
}
_VENDOR_HAL_RE = {
    "ST": re.compile(r'#include\s+[<\"].*stm32.*(?:hal|Hal).*\.h[>\"]', re.IGNORECASE),
    "WCH": re.compile(r'#include\s+[<\"].*ch32.*\.h[>\"]', re.IGNORECASE),
    "TI": re.compile(r'#include\s+[<\"].*tiva\|tm4c.*\.h[>\"]', re.IGNORECASE),
}
_FUNC_PREFIX_RE = re.compile(r"\b(\w+?)_(?:Init|Write|Read|Send|Transmit)\b")


def _whitelist_detect(text: str, patterns: dict[str, list[str]]) -> str:
    for name, pats in patterns.items():
        for pat in pats:
            if re.search(pat, text):
                return name
    return ""


def _detect_chip(header_text: str, brief: str, dir_name: str) -> str:
    """Detect chip name: whitelist > function prefix > generic pattern > dir name."""
    search_text = brief + " " + header_text[:2000]
    # 1) Whitelist
    for pat in _CHIP_WHITELIST:
        m = re.search(pat, search_text, re.IGNORECASE)
        if m:
            return m.group(0).upper()
    # 2) Function prefix based (e.g., CH1116_Init → CH1116)
    for m in _FUNC_PREFIX_RE.finditer(header_text[:3000]):
        prefix = m.group(1).upper()
        if re.match(r"^[A-Z]{2,}\d+", prefix):  # looks like a chip name
            return prefix
    # 3) Generic pattern
    m = _GENERIC_CHIP_RE.search(search_text)
    if m:
        return m.group(1).upper()
    # 4) Fallback to directory name
    return dir_name.upper()


def _detect_protocol(header_text: str, brief: str) -> str:
    """Detect protocol: whitelist > includes > function suffixes > keywords."""
    search_text = brief + " " + header_text[:3000]
    # 1) Whitelist
    result = _whitelist_detect(search_text, _PROTO_WHITELIST)
    if result:
        return result
    # 2) Includes
    for proto, pat in _HEADER_INCLUDE_RE.items():
        if pat.search(header_text):
            return proto
    # 3) Function suffix hints: _I2C_ → I2C, _SPI_ → SPI
    if re.search(r"_I2C_\|I2C_", header_text):
        return "I2C"
    if re.search(r"_SPI_\|SPI_", header_text):
        return "SPI"
    if re.search(r"_UART_\|UART_\|_USART_", header_text):
        return "UART"
    # 4) Keyword fallback
    if re.search(r"\bI2C\b", search_text):
        return "I2C"
    if re.search(r"\bSPI\b", search_text):
        return "SPI"
    return ""


def _detect_vendor(header_text: str, brief: str) -> str:
    """Detect vendor: whitelist > HAL includes."""
    search_text = brief + " " + header_text[:3000]
    result = _whitelist_detect(search_text, _VENDOR_WHITELIST)
    if result:
        return result
    for vendor, pat in _VENDOR_HAL_RE.items():
        if pat.search(header_text):
            return vendor
    return ""


def _detect_device(header_text: str, brief: str, dir_name: str) -> str:
    """Detect device category."""
    search_text = brief + " " + header_text[:2000] + " " + dir_name
    for pat, cat in _DEVICE_WHITELIST:
        if re.search(pat, search_text, re.IGNORECASE):
            return cat
    return ""


def extract_driver_metadata(header_path: str | Path, source_path: str | Path = "") -> DriverMetadata:
    """Extract DriverMetadata from .h file content."""
    h_path = Path(header_path).resolve()
    header_text = h_path.read_text(encoding="utf-8", errors="replace")
    brief = ""
    m = re.search(r"@brief\s+(.+?)(?:\n|\*/)", header_text)
    if m:
        brief = m.group(1).strip()
    dir_name = h_path.parent.name
    chip = _detect_chip(header_text, brief, dir_name)
    protocol = _detect_protocol(header_text, brief)
    vendor = _detect_vendor(header_text, brief)
    device = _detect_device(header_text, brief, dir_name)
    return DriverMetadata(
        name=dir_name,
        protocol=protocol,
        chip=chip,
        vendor=vendor,
        device=device,
        path=str(h_path.parent),
        header_path=str(h_path),
        source_path=str(Path(source_path).resolve()) if source_path else "",
        review_passed=True,
        source_doc=brief,
    )


def hash_header(header_path: str | Path) -> str:
    """SHA256 hash of .h file content for dedup."""
    return hashlib.sha256(Path(header_path).read_bytes()).hexdigest()


def find_existing_driver(library_path: str | Path, chip: str, header_hash: str) -> DriverMetadata | None:
    """Check if a driver with same chip+hash already exists in library."""
    lib_path = Path(library_path).resolve()
    if not lib_path.exists():
        return None
    for h_file in lib_path.rglob("*.h"):
        if h_file.name.startswith(".") or "knowledge_base" in str(h_file):
            continue
        existing_hash = hashlib.sha256(h_file.read_bytes()).hexdigest()
        if existing_hash == header_hash:
            return extract_driver_metadata(str(h_file))
    return None


def find_chip_variants(library_path: str | Path, chip: str) -> list[str]:
    """List existing variant dirs for a given chip name."""
    lib_path = Path(library_path).resolve()
    if not lib_path.exists():
        return []
    chip_lower = chip.lower()
    variants: list[str] = []
    for subdir in lib_path.rglob("*"):
        if subdir.is_dir() and subdir.name.lower() == chip_lower:
            # chip dir itself — check its subdirs for variants
            for variant_dir in sorted(subdir.iterdir()):
                if variant_dir.is_dir() and list(variant_dir.glob("*.h")):
                    variants.append(f"{subdir.parent.name}/{subdir.name}/{variant_dir.name}")
            # if .h files directly in chip dir, that''s also a variant
            if list(subdir.glob("*.h")):
                variants.append(f"{subdir.parent.name}/{subdir.name}")
    return variants


def publish_driver_to_library(
    library_path: str | Path,
    header_path: str | Path,
    source_path: str | Path,
    variant: str = "",
    force: bool = False,
) -> dict:
    """Copy driver files into library with dedup and variant support.

    Returns {"success": bool, "message": str, "target_path": str, "metadata": ...}
    """
    h_src = Path(header_path).resolve()
    c_src = Path(source_path).resolve() if source_path else None
    lib = Path(library_path).resolve()

    if not h_src.exists():
        return {"success": False, "message": f"Header not found: {h_src}"}

    metadata = extract_driver_metadata(str(h_src), str(c_src) if c_src else "")
    h_hash = hash_header(str(h_src))

    if not force:
        existing = find_existing_driver(str(lib), metadata.chip, h_hash)
        if existing:
            return {
                "success": False,
                "message": f"Identical driver already published at {existing.path}. Use force=true to override.",
                "target_path": existing.path,
                "existing": True,
            }
        # Check for same chip, different implementation
        if not variant and metadata.chip:
            chip_lower = metadata.chip.lower()
            for h_file in lib.rglob("*.h"):
                if h_file.name.startswith("."):
                    continue
                existing_md = extract_driver_metadata(str(h_file))
                if existing_md.chip.lower() == chip_lower:
                    existing_hash = hashlib.sha256(h_file.read_bytes()).hexdigest()
                    if existing_hash != h_hash:
                        variants = find_chip_variants(str(lib), metadata.chip)
                        return {
                            "success": False,
                            "message": (
                                f"Same chip ({metadata.chip}) but different implementation. "
                                f"Existing variants: {variants or 'none'}. "
                                f"Specify variant name (e.g., ''128x64'', ''128x32'') to publish as a variant."
                            ),
                            "needs_variant": True,
                            "existing_variants": variants,
                        }

    # Determine target path
    vendor = metadata.vendor.lower() or "generic"
    chip = metadata.chip.lower() or h_src.parent.name.lower()
    var = (variant or chip).lower().replace(" ", "_")
    target_dir = lib / vendor / chip / var
    target_dir.mkdir(parents=True, exist_ok=True)

    # Copy files
    t_h = target_dir / f"{var}.h"
    t_c = target_dir / f"{var}.c" if c_src else None
    t_h.write_bytes(h_src.read_bytes())
    if t_c and c_src and c_src.exists():
        t_c.write_bytes(c_src.read_bytes())

    # Update metadata for stored record
    stored_md = DriverMetadata(
        name=var,
        protocol=metadata.protocol,
        chip=metadata.chip,
        vendor=metadata.vendor or "generic",
        device=metadata.device,
        path=str(target_dir),
        header_path=str(t_h),
        source_path=str(t_c) if t_c else "",
        review_passed=True,
        source_doc=metadata.source_doc,
    )

    library = DriverLibrary(lib)
    library.store_driver(stored_md)

    return {
        "success": True,
        "message": f"Driver published to {target_dir}",
        "target_path": str(target_dir),
        "header_path": str(t_h),
        "source_path": str(t_c) if t_c else "",
        "chip": metadata.chip,
        "protocol": metadata.protocol,
        "vendor": metadata.vendor,
        "variant": var,
    }


def index_driver_library(library_path: str | Path) -> dict:
    """Scan library directory and index all drivers."""
    lib_path = Path(library_path).resolve()
    library = DriverLibrary(lib_path)
    stored, details = 0, []
    for subdir in sorted(lib_path.iterdir()):
        if not subdir.is_dir() or subdir.name.startswith(".") or subdir.name == "knowledge_base":
            continue
        h_files = list(subdir.glob("*.h"))
        if not h_files:
            # maybe nested: vendor/chip/variant
            for nested in sorted(subdir.rglob("*.h")):
                if nested.name.startswith("."):
                    continue
                md = extract_driver_metadata(str(nested))
                library.store_driver(md)
                stored += 1
                details.append({"name": md.name, "chip": md.chip, "protocol": md.protocol, "device": md.device})
            continue
        c_files = list(subdir.glob("*.c"))
        md = extract_driver_metadata(str(h_files[0]), str(c_files[0]) if c_files else "")
        library.store_driver(md)
        stored += 1
        details.append({"name": md.name, "chip": md.chip, "protocol": md.protocol, "device": md.device})
    return {"indexed": stored, "stats": library.stats(), "drivers": details}
