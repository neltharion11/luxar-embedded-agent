"""确定性显示屏字模（取模）引擎。

LLM 手写字体位图几乎必然出错（字形、位序、行列顺序都对不上）。本引擎
用 PyMuPDF（已是 LUXAR 硬依赖，无需新增依赖）把 TrueType/OpenType 字形
确定性光栅化为 1-bit 位图，再按目标显示控制器的内存布局打包成可直接嵌入
C 驱动库的数组：逐行/逐列（横向/纵向取模）、MSB/LSB 位序（顺向/逆向）、
阳码/阴码（是否取反）。

同一字体文件 + 同一参数必然产生同一输出，模型只需要描述需求，不再手写
任何位图字节。
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import zlib
from dataclasses import dataclass, field, replace
from pathlib import Path

from luxar.adapters.u8g2_fonts import U8G2_FONTS
from luxar.specs import available_controllers, find_chip_skill

_WINDOWS_FONTS_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

#: 已知字体名 -> Windows 字体目录下的文件名（跨平台可替换为绝对路径）。
#: 粗体变体（*bd / *b）小字号下笔画更粗、'e'/'w' 等闭合曲率字形更清晰，
#: 是嵌入式低分辨率显示接近点阵字体观感的推荐选择。
KNOWN_FONTS: dict[str, str] = {
    "consola": "consola.ttf",
    "consolas": "consola.ttf",
    "consolab": "consolab.ttf",
    "consolas_bold": "consolab.ttf",
    "cascadia_mono": "CascadiaMono.ttf",
    "arial": "arial.ttf",
    "arialbd": "arialbd.ttf",
    "arial_bold": "arialbd.ttf",
    "simhei": "simhei.ttf",
    "黑体": "simhei.ttf",
    "simsun": "simsun.ttc",
    "宋体": "simsun.ttc",
    "simsunb": "simsunb.ttf",
    "simsun_bold": "simsunb.ttf",
    "宋体粗体": "simsunb.ttf",
    "msyh": "msyh.ttc",
    "msyhbd": "msyhbd.ttc",
    "msyh_bold": "msyhbd.ttc",
    "微软雅黑": "msyh.ttc",
    "微软雅黑粗体": "msyhbd.ttc",
    "noto_sans_sc": "NotoSansSC-VF.ttf",
}

#: 常见显示控制器的默认内存布局 -> (scan, bit_order, invert)。
#: scan: row=逐行式(横向取模) / column=逐列式(纵向取模)
#: bit_order: msb=高位在前 / lsb=低位在前
#: invert: False=阳码(点亮位为 1) / True=阴码(点亮位为 0)
#:
#: 注意：SSD1306/SSD1315/SH1106 的页寻址（page mode）下，每个数据字节对应
#: 同一列中 8 个竖直像素，且 **位 0（LSB）位于该页顶部**——这是 Adafruit 与
#: u8g2 驱动一致采用的硬件约定（pixel = 1 << (y & 7)）。历史版本误配为
#: msb（位 7=页顶），导致每个 8 行块内上下颠倒：屏幕上字形全部乱码但位置
#: 正确（oled9 实机确认）。其余控制器（PCD8544/ST7735 等）为逐行式布局，
#: 位序按各数据手册约定保留 msb；新控制器接入前必须先真机核对位序。
#:
#: 本表是**回退/迁移对照表**：resolve_layout 优先从芯片规格（src/luxar/specs/
#: chips/*.yaml，每芯片含 layout/screen/init/验证状态）读取布局；规格缺失时才
#: 回退本表。新增芯片请新建规格 YAML（见 docs/superpowers/specs/
#: 2026-08-29-luxar-chip-skill-spec-schema-design.md），无需改本表。
CONTROLLER_LAYOUTS: dict[str, tuple[str, str, bool]] = {
    "ssd1306": ("column", "lsb", False),
    "ssd1315": ("column", "lsb", False),
    "sh1106": ("column", "lsb", False),
    "pcd8544": ("row", "msb", False),
    "nokia5110": ("row", "msb", False),
    "st7735": ("row", "msb", False),
    "st7789": ("row", "msb", False),
    "ili9341": ("row", "msb", False),
    "ili9488": ("row", "msb", False),
    "hd44780": ("row", "msb", False),
}

_ALIGNMENTS = ("center", "left")
_ARRAY_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_FONT_SUFFIXES = {".ttf", ".ttc", ".otf"}
_ALLOWED_HEADER_SUFFIXES = {".h", ".hpp", ".c", ".cpp"}

_RENDER_SCALE = 8  # 超采样倍数：小字号字形用高倍渲染再降采样，边缘更清晰

#: 超采样降采样的点亮判定阈值：源像素墨迹覆盖率 ≥ 该比例才保留目标像素。
#: 0.5 对 8px 级小字号 TTF 字形过严：细笔画（横杠/斜腿）在 8x8 块内覆盖率
#: 只有 20-45%，会被整段丢弃，A/B/C/D/e 等字形退化成稀疏乱码。0.3 经实测
#: 可完整保留横杠与双腿（oled6 乱码根因）。CJK 等粗笔画字形不受影响。
_INK_COVERAGE_THRESHOLD = 0.3


class FontBitmapError(RuntimeError):
    """取模引擎的确定性错误，携带稳定 code 供工具层映射。"""

    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class FontLayout:
    scan: str  # "row" | "column"
    bit_order: str  # "msb" | "lsb"
    invert: bool  # 阴码=True

    @property
    def human(self) -> str:
        scan_label = "逐列式(纵向取模)" if self.scan == "column" else "逐行式(横向取模)"
        bit_label = "低位在前" if self.bit_order == "lsb" else "高位在前"
        code_label = "阴码(点亮为0)" if self.invert else "阳码(点亮为1)"
        return f"{scan_label} / {bit_label} / {code_label}"


@dataclass(frozen=True)
class GlyphBitmap:
    codepoint: int
    char: str
    width: int
    height: int
    rows: tuple[tuple[bool, ...], ...]  # 逻辑行优先位图，True=点亮

    def preview(self) -> str:
        lines = []
        for row in self.rows:
            lines.append("".join("#" if pixel else "." for pixel in row))
        return "\n".join(lines)


@dataclass(frozen=True)
class FontBitmapResult:
    font_name: str
    font_path: str
    width: int
    height: int
    layout: FontLayout
    array_name: str
    glyphs: list[GlyphBitmap]
    bytes_per_glyph: int
    c_code: str
    text: str
    #: 每字形实际像素宽（ascii_half_width 混合模式下 ASCII 为半宽）
    glyph_widths: list[int] = field(default_factory=list)
    #: 每字形在扁平数组中的字节偏移，长度 = len(glyphs) + 1（含末尾）
    glyph_offsets: list[int] = field(default_factory=list)
    total_bytes: int = 0
    #: True 表示启用了 ASCII 半宽混合字库
    ascii_half_width: bool = False
    #: 混合字库的 ASCII 取模字体（u8g2 内置名）；None = ASCII 用主字体渲染
    ascii_font: str | None = None
    #: True 表示纯 ASCII 且未显式指定尺寸：按字体实际字宽比例排版
    proportional_ascii: bool = False
    #: 每字形打包字节的 CRC32（zlib 标准，hex 8 位）——设备侧自检锚点
    glyph_crc32s: list[str] = field(default_factory=list)
    #: 全部字形字节的 SHA-256（hex）——文件完整性锚点
    data_sha256: str = ""
    #: 全部字形字节的 CRC32（hex 8 位）
    data_crc32: str = ""
    warnings: list[str] = field(default_factory=list)


def _glyph_bytes(glyph: GlyphBitmap, layout: FontLayout) -> bytes:
    return bytes(pack_glyph(glyph, layout))


def glyph_crc32(glyph: GlyphBitmap, layout: FontLayout) -> str:
    """单个字形打包字节的 zlib CRC32（hex 8 位），固件侧可直接对比。"""
    return f"{zlib.crc32(_glyph_bytes(glyph, layout)) & 0xFFFFFFFF:08X}"


def render_frame_bytes(
    *,
    data: bytes,
    glyph_offsets: list[int],
    glyph_widths: list[int],
    codepoints: list[int],
    glyph_height: int,
    text: str,
    x: int,
    y: int,
    screen_width: int = 128,
    screen_height: int = 64,
) -> bytes:
    """按页寻址把字模数据重建为屏幕帧字节流（SSD1306/SH1106 布局）。

    帧布局与常见驱动一致：``framebuffer[page * screen_width + col]`` 一字节
    = 第 page 页、第 col 列的 8 个竖直像素，位序按字模 layout（MSB 或 LSB
    在上，见头部 layout 行）。字形数据按逐列（纵向）打包：每列
    ``ceil(glyph_height / 8)`` 字节，页序从上到下。
    设备侧对同一字模文件 + 同一绘制坐标重建同一帧，CRC32 必须一致。

    要求 y 为 8 的倍数（页寻址显示的自然约束）。
    """
    if x < 0 or y < 0:
        raise FontBitmapError("frame_coords_negative", "绘制坐标不能为负")
    if y % 8 != 0:
        raise FontBitmapError(
            "frame_y_not_page_aligned",
            "页寻址显示要求 y 为 8 的倍数（字符须整页对齐）",
            details={"y": y},
        )
    if screen_width <= 0 or screen_height <= 0 or screen_height % 8 != 0:
        raise FontBitmapError(
            "frame_screen_invalid",
            "屏幕尺寸必须是正整数且高度为 8 的倍数",
            details={"screen_width": screen_width, "screen_height": screen_height},
        )
    pages_total = screen_height // 8
    glyph_pages = (glyph_height + 7) // 8
    frame = bytearray(pages_total * screen_width)
    cursor_x = x
    for ch in text:
        try:
            index = codepoints.index(ord(ch))
        except ValueError:
            raise FontBitmapError(
                "glyph_not_in_font",
                f"字模中没有字符 {ch!r}（U+{ord(ch):04X}），无法重建预期帧",
                details={"char": ch, "codepoint": ord(ch)},
            ) from None
        offset = glyph_offsets[index]
        width = glyph_widths[index]
        for col in range(width):
            target_col = cursor_x + col
            if target_col >= screen_width:
                continue
            for page in range(glyph_pages):
                target_page = y // 8 + page
                if target_page >= pages_total:
                    continue
                frame[target_page * screen_width + target_col] = data[
                    offset + col * glyph_pages + page
                ]
        cursor_x += width
    return bytes(frame)


def frame_crc32(frame: bytes) -> str:
    """屏幕帧字节流的 zlib CRC32（hex 8 位）。"""
    return f"{zlib.crc32(frame) & 0xFFFFFFFF:08X}"


def frame_sha256(frame: bytes) -> str:
    """屏幕帧字节流的 SHA-256（hex）。"""
    return hashlib.sha256(frame).hexdigest()


def default_cell_size(text: str) -> tuple[int, int]:
    """未显式指定大小时：纯 ASCII 取 8x16，含宽字符（CJK）取 16x16。"""
    has_wide = any(ord(ch) > 0x7F for ch in text)
    return (16, 16) if has_wide else (8, 16)


def resolve_font_path(font: str, project_path: Path | None = None) -> Path:
    """把字体名/路径解析为实际字体文件；只允许 Windows 字体目录与项目目录。"""
    raw = font.strip()
    if not raw:
        raise FontBitmapError("font_empty", "未指定字体")
    candidate = Path(raw)
    if candidate.is_absolute() or ("\\" in raw or "/" in raw):
        resolved = candidate.expanduser()
        if not resolved.is_absolute():
            raise FontBitmapError(
                "font_path_not_allowed",
                "字体相对路径不受支持，请使用字体名或绝对路径",
            )
        resolved = resolved.resolve()
        inside_windows_fonts = _is_within(resolved, _WINDOWS_FONTS_DIR)
        inside_project = project_path is not None and _is_within(
            resolved, project_path.resolve()
        )
        if not (inside_windows_fonts or inside_project):
            raise FontBitmapError(
                "font_path_outside_boundary",
                "字体文件必须位于 Windows 字体目录或当前项目目录内",
                details={"font": raw},
            )
        if resolved.suffix.lower() not in _ALLOWED_FONT_SUFFIXES:
            raise FontBitmapError(
                "font_suffix_not_allowed",
                "字体文件只支持 .ttf/.ttc/.otf",
                details={"font": str(resolved)},
            )
        if not resolved.is_file():
            raise FontBitmapError(
                "font_not_found",
                f"字体文件不存在：{resolved}",
                details={"font": str(resolved)},
            )
        return resolved
    known = KNOWN_FONTS.get(raw.lower())
    if known is None:
        raise FontBitmapError(
            "font_unknown",
            f"未知字体：{raw}；可用字体名：{', '.join(sorted(KNOWN_FONTS))}；"
            "u8g2 内置点阵字体（纯 ASCII）：u8g2_5x7/u8g2_6x10/u8g2_8x13/u8g2_10x20",
            details={"font": raw, "available": sorted(KNOWN_FONTS)},
        )
    resolved = (_WINDOWS_FONTS_DIR / known).resolve()
    if not resolved.is_file():
        raise FontBitmapError(
            "font_not_found",
            f"系统字体缺失：{resolved}",
            details={"font": raw},
        )
    return resolved


def _is_within(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def resolve_layout(
    controller: str | None,
    scan: str | None,
    bit_order: str | None,
    invert: bool | None,
) -> FontLayout:
    if controller is not None:
        layout_scan, layout_bit, layout_invert = _layout_preset(controller)
    else:
        layout_scan, layout_bit, layout_invert = "row", "msb", False
    if scan is not None:
        layout_scan = scan
    if bit_order is not None:
        layout_bit = bit_order
    if invert is not None:
        layout_invert = invert
    return FontLayout(scan=layout_scan, bit_order=layout_bit, invert=layout_invert)


def _layout_preset(controller: str) -> tuple[str, str, bool]:
    """按控制器解析默认布局：优先芯片规格 YAML，回退 CONTROLLER_LAYOUTS。"""
    key = controller.strip().lower()
    # 规格层优先（新芯片加 YAML 即接入，无需改引擎代码）
    skill = find_chip_skill(key)
    if skill is not None and skill.display is not None:
        layout = skill.display.layout
        return (layout.scan, layout.bit_order, layout.invert)
    # 回退：迁移对照表
    preset = CONTROLLER_LAYOUTS.get(key)
    if preset is not None:
        return preset
    known = sorted(set(available_controllers()) | set(CONTROLLER_LAYOUTS))
    raise FontBitmapError(
        "controller_unknown",
        "未知控制器，可用芯片："
        + ", ".join(known)
        + "。若目标芯片不在其中：新建芯片规格 YAML（src/luxar/specs/chips/"
        "<controller>.yaml，schema 见 docs/superpowers/specs/2026-08-29-"
        "luxar-chip-skill-spec-schema-design.md）即自动接入，无需改引擎代码；"
        "或在取模参数中显式传 scan=row|column、bit_order=msb|lsb、"
        "invert=true|false 且不传 controller。位序/行序约定必须真机验证"
        "（CRC 自检无法发现约定错误）",
        details={
            "controller": controller,
            "available": known,
        },
    )


def _normalize_text(text: str) -> str:
    cleaned = "".join(ch for ch in text if ch not in "\r\n\t")
    if not cleaned.strip():
        raise FontBitmapError(
            "text_empty",
            "text 中没有可提取的字符（空白字符不生成字形）；取模必须有明确的字符集，"
            "请先用 ask_user 向用户询问需要显示的具体字符，不得自行猜测",
        )
    # 按首次出现顺序去重，避免重复字形浪费 Flash
    seen: set[str] = set()
    ordered: list[str] = []
    for ch in cleaned:
        if ch not in seen:
            seen.add(ch)
            ordered.append(ch)
    return "".join(ordered)


def _rasterize_glyph(
    font_path: Path,
    char: str,
    width: int,
    height: int,
    align: str,
    use_advance: bool = False,
    baseline_align: bool = False,
) -> GlyphBitmap | None:
    """把单个字形渲染成 width x height 的 1-bit 位图；字体缺字形时返回 None。

    use_advance=True 且字符为 ASCII 时，单元宽度按字体实际字宽（advance）
    计算并强制左对齐——用于混合中英文字库的比例排版：'l' 窄、'w' 宽，
    拼接成单词时间距均匀自然，字形也不会被固定半宽单元压扁。

    baseline_align=True 时垂直方向按行高区间做基线对齐（所有字母底边落在
    同一条基线上，'g'/'p' 等降部向下伸）；False 时（如 CJK 方块字）等比
    缩放并居中。
    """
    import pymupdf  # 延迟导入，避免增加 Web 启动开销

    codepoint = ord(char)
    font = pymupdf.Font(fontfile=str(font_path))
    if not font.has_glyph(codepoint):
        return None
    scale = _RENDER_SCALE
    font_size = height * scale
    if use_advance and codepoint <= 0x7F:
        advance_px = font.text_length(char, fontsize=font_size) / scale
        width = max(1, round(advance_px))
    pad = 2 * scale
    page_w = width * scale + 2 * pad
    page_h = height * scale + 2 * pad
    doc = pymupdf.open()
    page = doc.new_page(width=page_w, height=page_h)
    baseline = pad + font.ascender * font_size
    page.insert_text(
        (pad, baseline),
        char,
        fontsize=font_size,
        fontname="F0",
        fontfile=str(font_path),
    )
    pix = page.get_pixmap()  # dpi=72 -> 1pt = 1px
    samples = pix.samples
    row_bytes = pix.n * pix.width
    ink = [[False] * pix.width for _ in range(pix.height)]
    x0, y0, x1, y1 = pix.width, pix.height, 0, 0
    has_ink = False
    for yy in range(pix.height):
        base = yy * row_bytes
        target = ink[yy]
        for xx in range(pix.width):
            if samples[base + xx * pix.n] < 128:  # 灰度阈值，抗锯齿取暗部
                target[xx] = True
                if not has_ink:
                    has_ink = True
                if xx < x0:
                    x0 = xx
                if xx > x1:
                    x1 = xx
                if yy < y0:
                    y0 = yy
                if yy > y1:
                    y1 = yy
    doc.close()
    if not has_ink:
        # 空白字形（如空格）：整格熄灭
        return GlyphBitmap(
            codepoint=codepoint,
            char=char,
            width=width,
            height=height,
            rows=tuple((False,) * width for _ in range(height)),
        )
    ink_w = x1 - x0 + 1
    ink_h = y1 - y0 + 1
    rows = [[False] * width for _ in range(height)]
    if baseline_align:
        # 垂直：把字体行高区间（ascender 顶 ~ descender 底）线性映射到整个
        # 单元，所有字母的基线（baseline）落在同一条线上，'g'/'p' 等降部
        # 字符向下伸——小写文本“贴行底”的标准排版。
        line_height = (font.ascender - font.descender) * font_size
        top_render = baseline - font.ascender * font_size
        v_scale = height / line_height
        if use_advance:
            # 比例模式：ink 按原尺寸放置（不拉伸），左对齐，右侧留白自然
            dest_w = max(1, round(ink_w / scale))
            off_x = 0
        else:
            dest_w = width
            off_x = (width - dest_w) // 2 if align == "center" else 0
        for dy in range(height):
            sy0 = top_render + dy / v_scale
            sy1 = top_render + (dy + 1) / v_scale
            y_lo = max(0, math.floor(sy0))
            y_hi = min(pix.height, math.ceil(sy1))
            for dx in range(dest_w):
                sx0 = x0 + dx * (ink_w / dest_w) if dest_w > 0 else x0
                sx1 = x0 + (dx + 1) * (ink_w / dest_w) if dest_w > 0 else x0
                x_lo = max(0, math.floor(sx0))
                x_hi = min(pix.width, math.ceil(sx1))
                total = 0
                lit = 0
                for yy in range(y_lo, y_hi):
                    row = ink[yy]
                    for xx in range(x_lo, x_hi):
                        total += 1
                        if row[xx]:
                            lit += 1
                on = total > 0 and lit / total >= _INK_COVERAGE_THRESHOLD
                rows[dy][off_x + dx] = on
    else:
        # 方块字形（CJK）与非基线模式：等比缩放并居中
        fit = min(width / ink_w, height / ink_h)
        dest_w = max(1, round(ink_w * fit))
        dest_h = max(1, round(ink_h * fit))
        off_x = 0 if (align == "left" or use_advance) else (width - dest_w) // 2
        off_y = (height - dest_h) // 2
        for dy in range(dest_h):
            sy0 = y0 + dy / fit
            sy1 = y0 + (dy + 1) / fit
            y_lo = max(0, math.floor(sy0))
            y_hi = min(pix.height, math.ceil(sy1))
            for dx in range(dest_w):
                sx0 = x0 + dx / fit
                sx1 = x0 + (dx + 1) / fit
                x_lo = max(0, math.floor(sx0))
                x_hi = min(pix.width, math.ceil(sx1))
                total = 0
                lit = 0
                for yy in range(y_lo, y_hi):
                    row = ink[yy]
                    for xx in range(x_lo, x_hi):
                        total += 1
                        if row[xx]:
                            lit += 1
                on = total > 0 and lit / total >= _INK_COVERAGE_THRESHOLD
                rows[off_y + dy][off_x + dx] = on
    return GlyphBitmap(
        codepoint=codepoint,
        char=char,
        width=width,
        height=height,
        rows=tuple(tuple(row) for row in rows),
    )


def pack_glyph(glyph: GlyphBitmap, layout: FontLayout) -> list[int]:
    """按控制器内存布局打包字形：逐行/逐列、MSB/LSB、阳码/阴码。"""
    width, height = glyph.width, glyph.height
    packed: list[int] = []
    if layout.scan == "row":
        bytes_per_unit = (width + 7) // 8
        for y in range(height):
            for unit in range(bytes_per_unit):
                byte = 0
                for bit in range(8):
                    x = unit * 8 + bit
                    if x < width and glyph.rows[y][x]:
                        position = 7 - bit if layout.bit_order == "msb" else bit
                        byte |= 1 << position
                packed.append(byte)
    else:  # column
        bytes_per_unit = (height + 7) // 8
        for x in range(width):
            for unit in range(bytes_per_unit):
                byte = 0
                for bit in range(8):
                    y = unit * 8 + bit
                    if y < height and glyph.rows[y][x]:
                        position = 7 - bit if layout.bit_order == "msb" else bit
                        byte |= 1 << position
                packed.append(byte)
    if layout.invert:
        packed = [value ^ 0xFF for value in packed]
    return packed


def _c_hex_bytes(bytes_per_line: int, data: list[int]) -> str:
    lines = []
    for start in range(0, len(data), bytes_per_line):
        chunk = data[start : start + bytes_per_line]
        lines.append("    " + ", ".join(f"0x{value:02X}" for value in chunk) + ",")
    return "\n".join(lines)


def format_c_header(
    *,
    result: FontBitmapResult,
    include_codepoints: bool,
) -> str:
    """生成可直接嵌入驱动库的头文件文本（含每个字形的 ASCII 预览注释）。"""
    glyphs = result.glyphs
    count = len(glyphs)
    bytes_per_glyph = result.bytes_per_glyph
    name = result.array_name
    mixed = result.ascii_half_width or result.proportional_ascii
    lines: list[str] = []
    lines.append("/*")
    lines.append(f" * Auto-generated by LUXAR font.extract - do not hand-edit.")
    lines.append(f" * font: {result.font_name}")
    if result.ascii_font:
        lines.append(f" * ascii_font: {result.ascii_font} (ASCII 取模字体，像素级 u8g2 点阵)")
    if result.ascii_half_width:
        lines.append(
            f" * cell: {result.width}x{result.height} (ASCII {result.width // 2}x"
            f"{result.height}, CJK {result.width}x{result.height})  "
            f"glyphs: {count}  total_bytes: {result.total_bytes}"
        )
    elif result.proportional_ascii:
        lines.append(
            f" * cell: 比例(按实际字宽) x{result.height}  glyphs: {count}  "
            f"total_bytes: {result.total_bytes}"
        )
    else:
        lines.append(f" * cell: {result.width}x{result.height}  glyphs: {count}  "
                     f"bytes_per_glyph: {bytes_per_glyph}")
    lines.append(f" * layout: {result.layout.human}")
    lines.append(
        f" * layout_scan={result.layout.scan} layout_bit_order="
        f"{result.layout.bit_order} layout_invert="
        f"{1 if result.layout.invert else 0}"
    )
    lines.append(f" * text: {result.text}")
    lines.append(" */")
    lines.append("#include <stdint.h>")
    lines.append("")
    lines.append(f"#define {name}_WIDTH {result.width}")
    lines.append(f"#define {name}_HEIGHT {result.height}")
    lines.append(f"#define {name}_GLYPH_COUNT {count}")
    lines.append(f"#define {name}_BYTES_PER_GLYPH {bytes_per_glyph}")
    lines.append(f"#define {name}_TOTAL_BYTES {result.total_bytes}")
    lines.append("")
    lines.append("/* 字形顺序 = text 中字符首次出现顺序；CODEPOINTS 表始终输出，")
    lines.append(" * 供驱动按 Unicode 码点查表（含纯 ASCII 字库）。 */")
    lines.append(f"const uint8_t {name}[] = {{")
    for index, glyph in enumerate(glyphs):
        width_label = (
            f" [{glyph.width}px]"
            if mixed and glyph.width != result.width
            else ""
        )
        crc_label = f" crc32={result.glyph_crc32s[index]}"
        lines.append(
            f"    /* [{index}] U+{glyph.codepoint:04X} '{glyph.char}'"
            f"{width_label}{crc_label} */"
        )
        lines.append(f"    /* {glyph.preview().replace(chr(10), chr(10) + '       ')} */")
        data = pack_glyph(glyph, result.layout)
        lines.append(_c_hex_bytes(8, data))
    lines.append("};")
    if mixed:
        widths = ", ".join(str(item) for item in result.glyph_widths)
        offsets = ", ".join(str(item) for item in result.glyph_offsets)
        lines.append("")
        if result.ascii_half_width:
            lines.append("/* 混合宽度字库：ASCII 按实际字宽比例排版，CJK 全宽。")
        else:
            lines.append("/* 比例字库：每个字形按字体实际字宽排版。")
        lines.append(" * 绘制/居中：对显示文案逐字符在 CODEPOINTS 中查表得索引 i，")
        lines.append(" *   字形数据 = &FONT[GLYPH_OFFSETS[i]]，像素宽 = GLYPH_WIDTHS[i]；")
        lines.append(" *   一行总宽 line_w = 各字符 GLYPH_WIDTHS 之和，")
        lines.append(" *   x0 = (屏幕宽 - line_w) / 2 即居中。 */")
        lines.append(f"const uint8_t {name}_GLYPH_WIDTHS[{count}] = {{ {widths} }};")
        lines.append(
            f"const uint16_t {name}_GLYPH_OFFSETS[{count + 1}] = {{ {offsets} }};"
        )
    if include_codepoints:
        codepoints = ", ".join(f"0x{g.codepoint:04X}" for g in glyphs)
        lines.append("")
        lines.append(f"const uint16_t {name}_CODEPOINTS[{count}] = {{ {codepoints} }};")
    lines.append("")
    bit_label = (
        "MSB(高位)在上"
        if result.layout.bit_order == "msb"
        else "LSB(低位)在上"
    )
    lines.append("/* 设备侧自检（可选）：本文件字形按页寻址逐列（纵向）布局，每字节")
    lines.append(f" * = 一页内 8 个竖直像素、{bit_label}（SSD1306 系列为位0=页顶，即低位在前）。")
    lines.append(" * 绘制后用 display.selfcheck 模板的")
    lines.append(" * CRC32 计算屏幕区域字节流，经 UART 打印 FONT_CHECK <name> <crc32>，")
    lines.append(" * 上位机用 display.verify 重建同一帧对比；每字形 crc32 锚点可用于")
    lines.append(" * 校验固件内数组与本文件一致。 */")
    lines.append("/* 驱动读取契约（列主序，务必照此实现，否则显示为乱码）：")
    lines.append(" * 字形 i 的起始字节 = &FONT[GLYPH_OFFSETS[i]]，像素宽 = GLYPH_WIDTHS[i]；")
    lines.append(" * 页数 pages = (字形高 + 7) / 8（本文件 ASCII 与 CJK 均 2 页）；")
    lines.append(" * 第 c 列（0..width-1）、第 p 页（0..pages-1，p=0 为上 8 像素，")
    lines.append(f" * {bit_label}）的字节索引为：FONT[GLYPH_OFFSETS[i] + c * pages + p]。")
    lines.append(" * 写屏：第 p 页把该页所有列按列地址 x+c 顺序写入。 */")
    lines.append(f"/* data_crc32={result.data_crc32} data_sha256={result.data_sha256} */")
    lines.append("")
    return "\n".join(lines)


def _u8g2_bitmap_rows(rows: tuple[int, ...], w: int, adv: int, height: int) -> tuple[tuple[bool, ...], ...]:
    """把 BDF 位图行（整数，MSB 在前）转换为单元 bool 行。"""
    row_bytes = (w + 7) // 8
    bool_rows: list[tuple[bool, ...]] = []
    for rv in rows:
        bits: list[bool] = []
        for byte_i in range(row_bytes):
            byte = (rv >> (8 * (row_bytes - 1 - byte_i))) & 0xFF
            for bit in range(8):
                bits.append(bool(byte & (0x80 >> bit)))
        bits = bits[:w]
        bits.extend([False] * (adv - len(bits)))
        bool_rows.append(tuple(bits))
    while len(bool_rows) < height:
        bool_rows.append(tuple(False for _ in range(adv)))
    return tuple(bool_rows[:height])


def _u8g2_cell_glyph(
    font: str,
    char: str,
    cell_w: int,
    cell_h: int,
) -> GlyphBitmap | None:
    """把 u8g2 内置点阵字形放入 ``cell_w x cell_h`` 单元，供混合字库使用。

    u8g2 字形本身是像素级精确的位图（不经过 TTF 降采样阈值），所以
    ``ascii_font`` 指定 u8g2 字体时，ASCII 直接取自这里，杜绝小字号
    细笔画被阈值丢弃的乱码问题。放置规则：水平左对齐（与 use_advance
    的混合路径一致），垂直底部对齐（基线落在单元底部，与 TTF 基线
    排版观感一致）。缺字形返回 None。
    """
    data = U8G2_FONTS[font]
    glyphs = data["glyphs"]
    codepoint = ord(char)
    if codepoint not in glyphs:
        return None
    adv, w, h, rows = glyphs[codepoint]
    font_height = int(data["height"])
    src_rows = _u8g2_bitmap_rows(rows, w, adv, font_height)
    out = [[False] * cell_w for _ in range(cell_h)]
    used_h = min(font_height, cell_h)
    used_w = min(adv, cell_w)
    off_y = cell_h - used_h
    for yy in range(used_h):
        row = src_rows[yy]
        for xx in range(used_w):
            if row[xx]:
                out[off_y + yy][xx] = True
    return GlyphBitmap(
        codepoint=codepoint,
        char=char,
        width=cell_w,
        height=cell_h,
        rows=tuple(tuple(row) for row in out),
    )


def _extract_u8g2_bitmap(
    *,
    text: str,
    font: str,
    layout: FontLayout,
    array_name: str | None,
) -> FontBitmapResult:
    """从内置 U8g2 点阵数据提取字形（纯 ASCII 32-127，等宽，自带基线布局）。"""
    data = U8G2_FONTS[font]
    glyphs = data["glyphs"]
    missing = [ch for ch in text if ord(ch) not in glyphs]
    if missing:
        raise FontBitmapError(
            "font_missing_glyphs",
            "u8g2 点阵字体仅支持 ASCII 32-127 可打印字符；"
            "包含中文等宽字符请改用 msyhbd/simhei/simsun 等 TTF 字体（font 参数）",
            details={
                "font": font,
                "missing": [f"U+{ord(ch):04X} {ch!r}" for ch in missing],
            },
        )
    font_height = int(data["height"])
    glyph_list: list[GlyphBitmap] = []
    advance_list: list[int] = []
    for ch in text:
        adv, w, h, rows = glyphs[ord(ch)]
        glyph_list.append(
            GlyphBitmap(
                codepoint=ord(ch),
                char=ch,
                width=adv,
                height=font_height,
                rows=_u8g2_bitmap_rows(rows, w, adv, font_height),
            )
        )
        advance_list.append(adv)
    glyph_offsets: list[int] = []
    cursor = 0
    for glyph in glyph_list:
        glyph_offsets.append(cursor)
        cursor += len(pack_glyph(glyph, layout))
    glyph_offsets.append(cursor)
    glyph_crc32s = [glyph_crc32(glyph, layout) for glyph in glyph_list]
    data_bytes = b"".join(_glyph_bytes(glyph, layout) for glyph in glyph_list)
    cell_w = max(advance_list)
    if layout.scan == "row":
        bytes_per_glyph = font_height * ((cell_w + 7) // 8)
    else:
        bytes_per_glyph = cell_w * ((font_height + 7) // 8)
    name = array_name or f"FONT_{cell_w}X{font_height}"
    result = FontBitmapResult(
        font_name=font,
        font_path="",
        width=cell_w,
        height=font_height,
        layout=layout,
        array_name=name,
        glyphs=glyph_list,
        bytes_per_glyph=bytes_per_glyph,
        c_code="",
        text=text,
        glyph_widths=advance_list,
        glyph_offsets=glyph_offsets,
        total_bytes=cursor,
        ascii_half_width=False,
        proportional_ascii=False,
        glyph_crc32s=glyph_crc32s,
        data_sha256=hashlib.sha256(data_bytes).hexdigest(),
        data_crc32=f"{zlib.crc32(data_bytes) & 0xFFFFFFFF:08X}",
    )
    return replace(
        result,
        c_code=format_c_header(result=result, include_codepoints=True),
    )


def extract_font_bitmap(
    *,
    text: str,
    font: str = "msyhbd",
    width: int | None = None,
    height: int | None = None,
    controller: str | None = None,
    scan: str | None = None,
    bit_order: str | None = None,
    invert: bool | None = None,
    align: str = "center",
    array_name: str | None = None,
    ascii_half_width: bool = False,
    ascii_font: str | None = None,
    project_path: Path | None = None,
) -> FontBitmapResult:
    """按控制器布局确定性提取字形位图并生成 C 代码（核心入口）。

    排版规则（默认字体 msyh，拉丁字形为比例字体）：

    - 显式指定 width/height 时：固定单元字模（等宽风格），字形按 align
      对齐，适合 6x8/8x16 等经典字库；
    - 未显式指定且文本含 ASCII 时：ASCII 按字体实际字宽（advance）比例
      排版并左对齐，'l' 窄、'w' 宽，拼接自然、不被压扁；
    - ascii_half_width=True 且文本同时含 ASCII 与宽字符（CJK）时：ASCII
      比例半宽，CJK 取全宽 cell_w，生成可混排的混合字库，驱动按
      GLYPH_WIDTHS 累加宽度即可正确居中排版；
    - ascii_font=u8g2 内置字体名（如 "u8g2_5x7"）时：混合字库的 ASCII
      字形改用 u8g2 像素级点阵（不经过 TTF 降采样阈值），小字号细笔画
      不会被丢弃成乱码；CJK 仍用主字体。默认 None = 沿用主字体渲染。
    """
    cleaned = _normalize_text(text)
    if align not in _ALIGNMENTS:
        raise FontBitmapError(
            "align_unknown",
            f"align 只支持 {'/'.join(_ALIGNMENTS)}",
            details={"align": align},
        )
    layout = resolve_layout(controller, scan, bit_order, invert)
    u8g2_key = font.strip().lower()
    if u8g2_key in U8G2_FONTS:
        # 内置点阵字体：不经过 TTF 光栅化，纯 ASCII 等宽，自带基线布局
        return _extract_u8g2_bitmap(
            text=cleaned,
            font=u8g2_key,
            layout=layout,
            array_name=array_name,
        )
    ascii_u8g2_key: str | None = None
    if ascii_font is not None and ascii_font.strip():
        candidate = ascii_font.strip().lower()
        if candidate not in U8G2_FONTS:
            raise FontBitmapError(
                "font_unknown",
                f"ascii_font 必须是 u8g2 内置点阵字体："
                + ", ".join(sorted(U8G2_FONTS)),
                details={
                    "ascii_font": ascii_font,
                    "available": sorted(U8G2_FONTS),
                },
            )
        ascii_u8g2_key = candidate
    cell_w, cell_h = default_cell_size(cleaned)
    if width is not None:
        cell_w = width
    if height is not None:
        cell_h = height
    font_path = resolve_font_path(font, project_path)
    if array_name is not None:
        if not _ARRAY_NAME_RE.match(array_name):
            raise FontBitmapError(
                "array_name_invalid",
                "array_name 必须是合法 C 标识符",
                details={"array_name": array_name},
            )
        name = array_name
    else:
        name = f"FONT_{cell_w}X{cell_h}"
    has_wide = any(ord(ch) > 0x7F for ch in cleaned)
    half_width = ascii_half_width and has_wide
    explicit_cell = width is not None or height is not None
    # 纯 ASCII 且未显式指定尺寸 -> 按实际字宽比例排版
    proportional_ascii = not has_wide and not explicit_cell
    use_advance = half_width or proportional_ascii
    glyphs: list[GlyphBitmap] = []
    missing: list[str] = []
    for char in cleaned:
        codepoint = ord(char)
        is_ascii = codepoint <= 0x7F
        unit_w = (cell_w + 1) // 2 if (half_width and is_ascii) else cell_w
        if half_width and is_ascii and ascii_u8g2_key is not None:
            # 混合字库：ASCII 走 u8g2 像素级点阵（不经过 TTF 降采样阈值，
            # 杜绝小字号细笔画被丢弃成乱码），CJK 仍用主字体渲染。
            glyph = _u8g2_cell_glyph(ascii_u8g2_key, char, unit_w, cell_h)
        else:
            glyph = _rasterize_glyph(
                font_path,
                char,
                unit_w,
                cell_h,
                align,
                use_advance=(use_advance and is_ascii),
                baseline_align=is_ascii,
            )
        if glyph is None:
            missing.append(char)
            continue
        glyphs.append(glyph)
    if missing:
        raise FontBitmapError(
            "font_missing_glyphs",
            "所选字体缺少以下字符的字形，请换用 simhei/simsun/msyh 等中文字体："
            + " ".join(repr(ch) for ch in missing),
            details={"missing": [f"U+{ord(ch):04X} {ch!r}" for ch in missing]},
        )
    if not glyphs:
        raise FontBitmapError("text_empty", "没有可提取的字形")
    glyph_widths = [glyph.width for glyph in glyphs]
    glyph_offsets: list[int] = []
    cursor = 0
    for glyph in glyphs:
        glyph_offsets.append(cursor)
        cursor += len(pack_glyph(glyph, layout))
    glyph_offsets.append(cursor)
    glyph_crc32s = [glyph_crc32(glyph, layout) for glyph in glyphs]
    data_bytes = b"".join(_glyph_bytes(glyph, layout) for glyph in glyphs)
    if layout.scan == "row":
        bytes_per_glyph = cell_h * ((cell_w + 7) // 8)
    else:
        bytes_per_glyph = cell_w * ((cell_h + 7) // 8)
    result = FontBitmapResult(
        font_name=font_path.stem,
        font_path=str(font_path),
        width=cell_w,
        height=cell_h,
        layout=layout,
        array_name=name,
        glyphs=glyphs,
        bytes_per_glyph=bytes_per_glyph,
        c_code="",
        text=cleaned,
        glyph_widths=glyph_widths,
        glyph_offsets=glyph_offsets,
        total_bytes=cursor,
        ascii_half_width=half_width,
        ascii_font=ascii_u8g2_key,
        proportional_ascii=proportional_ascii,
        glyph_crc32s=glyph_crc32s,
        data_sha256=hashlib.sha256(data_bytes).hexdigest(),
        data_crc32=f"{zlib.crc32(data_bytes) & 0xFFFFFFFF:08X}",
    )
    return replace(
        result,
        c_code=format_c_header(result=result, include_codepoints=True),
    )


__all__ = [
    "CONTROLLER_LAYOUTS",
    "FontBitmapError",
    "FontBitmapResult",
    "FontLayout",
    "GlyphBitmap",
    "KNOWN_FONTS",
    "default_cell_size",
    "extract_font_bitmap",
    "format_c_header",
    "frame_crc32",
    "frame_sha256",
    "glyph_crc32",
    "pack_glyph",
    "render_frame_bytes",
    "resolve_font_path",
    "resolve_layout",
]
