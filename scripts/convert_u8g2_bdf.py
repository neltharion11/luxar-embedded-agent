"""把 u8g2 BDF 字体源（MIT）转换为 LUXAR 内嵌点阵字体数据模块。

用法：python scripts/convert_u8g2_bdf.py <bdf_dir> <output.py>
只提取 ASCII 32-127（与 u8g2 *_tf 字体一致的可打印字符集）。
"""

from __future__ import annotations

import sys
from pathlib import Path


def parse_bdf(path: Path) -> dict[int, dict]:
    glyphs: dict[int, dict] = {}
    current: dict | None = None
    for raw in path.read_text(encoding="latin-1").splitlines():
        line = raw.strip()
        if line.startswith("STARTCHAR"):
            current = {"rows": [], "in_bitmap": False}
        elif line.startswith("ENCODING") and current is not None:
            current["code"] = int(line.split()[1])
        elif line.startswith("DWIDTH") and current is not None:
            current["adv"] = int(line.split()[1])
        elif line.startswith("BBX") and current is not None:
            parts = line.split()
            current["w"] = int(parts[1])
            current["h"] = int(parts[2])
        elif line == "BITMAP" and current is not None:
            current["in_bitmap"] = True
        elif current is not None and current["in_bitmap"] and line != "ENDCHAR":
            # BDF BITMAP 行可能含多个空格分隔的 hex 字节（如 10x20 每行 2 字节）
            current["rows"].append(int(line.replace(" ", ""), 16))
        elif line == "ENDCHAR" and current is not None:
            glyphs[current["code"]] = {
                "adv": current.get("adv", current.get("w", 0)),
                "w": current.get("w", 0),
                "h": current.get("h", 0),
                "rows": tuple(current["rows"]),
            }
            current = None
    return glyphs


def format_glyph_data(code: int, g: dict) -> str:
    rows = ", ".join(f"0x{v:02X}" for v in g["rows"])
    return f"        {code}: ({g['adv']}, {g['w']}, {g['h']}, ({rows})),"


def main() -> int:
    bdf_dir = Path(sys.argv[1])
    output = Path(sys.argv[2])
    fonts = {
        "u8g2_5x7": "5x7.bdf",
        "u8g2_6x10": "6x10.bdf",
        "u8g2_8x13": "8x13.bdf",
        "u8g2_10x20": "10x20.bdf",
    }
    lines = [
        '"""U8g2 点阵字体内置数据（自动生成，勿手改）。',
        "",
        "来源：olikraus/u8g2 (MIT License) tools/font/bdf 目录的 BDF 字体源，",
        "本模块提取 ASCII 32-127 可打印字符集，与 u8g2 *_tf 字体一致。",
        "每个字形： (advance_px, bitmap_width, bitmap_height, rows_bytes)，",
        "rows_bytes 为位图行（MSB 在前），行数与 bitmap_height 相同。",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "U8G2_FONTS: dict[str, dict] = {",
    ]
    for font_name, filename in fonts.items():
        source = bdf_dir / filename
        if not source.is_file():
            print(f"missing {source}")
            return 1
        glyphs = parse_bdf(source)
        ascii_glyphs = {c: g for c, g in glyphs.items() if 32 <= c <= 127}
        if len(ascii_glyphs) < 90:
            print(f"unexpected glyph count for {filename}: {len(ascii_glyphs)}")
            return 1
        height = max(g["h"] for g in ascii_glyphs.values())
        width = max(g["w"] for g in ascii_glyphs.values())
        lines.append(f"    {font_name!r}: {{")
        lines.append(f"        'height': {height},")
        lines.append(f"        'width': {width},")
        lines.append("        'glyphs': {")
        for code in sorted(ascii_glyphs):
            lines.append(format_glyph_data(code, ascii_glyphs[code]))
        lines.append("        },")
        lines.append("    },")
    lines.append("}")
    lines.append("")
    lines.append("__all__ = ['U8G2_FONTS']")
    lines.append("")
    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {output} ({output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
