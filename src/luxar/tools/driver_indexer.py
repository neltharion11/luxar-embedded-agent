"""Driver library indexing."""
from __future__ import annotations
import re
from pathlib import Path
from luxar.core.driver_library import DriverLibrary
from luxar.models.schemas import DriverMetadata

_PROTO = {"I2C": [r"\bI2C\b"], "SPI": [r"\bSPI\b"], "UART": [r"\bUART\b", r"\bUSART\b"], "GPIO": [r"\bGPIO\b"]}
_VENDORS = {"ST": [r"\bSTM32\b"], "WCH": [r"\bCH32\b", r"\bCH1116\b"]}
_CHIPS = [r"\bCH1116\b", r"\bSH1106\b", r"\bSSD1306\b", r"\bHC.SR04\b", r"\bWS2812B?\b", r"\bEMB.001\b", r"\bSTM32F\d+\b"]
_DEVS = [(r"OLED|display", "Display"), (r"ultrasonic|distance|sensor", "Sensor"), (r"LED|RGB", "LED"), (r"UART|serial|comm", "Communication")]

def _detect(text, patterns):
    for name, pats in patterns.items():
        for pat in pats:
            if re.search(pat, text):
                return name
    return ""

def index_driver_library(library_path):
    lib_path = Path(library_path).resolve()
    library = DriverLibrary(lib_path)
    stored, details = 0, []
    for subdir in sorted(lib_path.iterdir()):
        if not subdir.is_dir():
            continue
        h_files = list(subdir.glob("*.h"))
        if not h_files:
            continue
        c_files = list(subdir.glob("*.c"))
        h_path = h_files[0]
        c_path = c_files[0] if c_files else None
        header = h_path.read_text(encoding="utf-8", errors="replace")
        st = header[:1000]
        brief = ""
        m = re.search(r"@brief\s+(.+?)(?:\n|\*/)", header)
        if m:
            brief = m.group(1).strip()
        chip = ""
        for pat in _CHIPS:
            m = re.search(pat, brief + " " + st, re.IGNORECASE)
            if m:
                chip = m.group(0)
                break
        protocol = _detect(brief + " " + st, _PROTO)
        vendor = _detect(brief + " " + st, _VENDORS)
        device = ""
        for pat, cat in _DEVS:
            if re.search(pat, brief + " " + subdir.name, re.IGNORECASE):
                device = cat
                break
        md = DriverMetadata(name=subdir.name, protocol=protocol, chip=chip, vendor=vendor, device=device, path=str(subdir), header_path=str(h_path), source_path=str(c_path) if c_path else "", review_passed=True, source_doc=brief)
        library.store_driver(md)
        stored += 1
        details.append(dict(name=subdir.name, chip=chip, protocol=protocol, device=device))
    return dict(indexed=stored, stats=library.stats(), drivers=details)
