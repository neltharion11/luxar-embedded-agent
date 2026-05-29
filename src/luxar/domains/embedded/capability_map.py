from __future__ import annotations


CAPABILITY_MAP: dict[str, tuple[str, ...]] = {
    "bringup": ("i2c", "spi", "uart", "oled", "sensor", "display", "screen"),
    "integration": ("status", "ui", "display state", "rgb", "app"),
    "recovery": ("fail", "dark", "nack", "no response", "error", "recovery"),
    "workspace": ("build", "flash", "monitor", "probe"),
}
