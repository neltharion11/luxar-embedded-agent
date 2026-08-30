"""确定性显示屏字模（取模）引擎与 font.extract / font.export 工具测试。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from luxar.adapters.continuous_agent_tools import create_core_tool_registry
from luxar.adapters.font_bitmap import (
    CONTROLLER_LAYOUTS,
    FontBitmapError,
    FontLayout,
    GlyphBitmap,
    default_cell_size,
    extract_font_bitmap,
    pack_glyph,
    resolve_font_path,
    resolve_layout,
)
from luxar.adapters.u8g2_fonts import U8G2_FONTS
from luxar.domain.continuous_agent.steps import ToolCall
from luxar.ports.agent_tool import AgentToolExecutionContext

WINDOWS_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
HAS_CONSOLA = (WINDOWS_FONTS / "consola.ttf").is_file()
HAS_SIMSUN = (WINDOWS_FONTS / "simsun.ttc").is_file()
HAS_MSYH = (WINDOWS_FONTS / "msyh.ttc").is_file()

needs_consola = pytest.mark.skipif(
    not HAS_CONSOLA, reason="缺少系统字体 consola.ttf"
)
needs_simsun = pytest.mark.skipif(
    not HAS_SIMSUN, reason="缺少系统字体 simsun.ttc"
)
needs_msyh = pytest.mark.skipif(
    not HAS_MSYH, reason="缺少系统字体 msyh.ttc"
)


def _context(project_path: Path) -> AgentToolExecutionContext:
    return AgentToolExecutionContext(
        session_id="session-1",
        turn_id="turn-1",
        project_key="0:test4",
        project_path=project_path,
    )


# ---------------------------------------------------------------- 打包逻辑


def test_pack_row_major_msb() -> None:
    # 4x8：第 0 行 [1,0,1,0] -> 0b1010_0000 = 0xA0
    rows = tuple(
        tuple(bool(int(ch)) for ch in row)
        for row in ["1010", "0000", "1000", "0000", "0000", "0000", "0000", "0001"]
    )
    glyph = GlyphBitmap(codepoint=ord("X"), char="X", width=4, height=8, rows=rows)
    packed = pack_glyph(glyph, FontLayout(scan="row", bit_order="msb", invert=False))
    # 行7 "0001" -> MSB 在前：bit4=1 -> 0b0001_0000 = 0x10
    assert packed == [0xA0, 0x00, 0x80, 0x00, 0x00, 0x00, 0x00, 0x10]


def test_pack_column_major_msb() -> None:
    # 4x8 逐列：每列 8 位纵向打包，MSB=最上一行
    rows = tuple(
        tuple(bool(int(ch)) for ch in row)
        for row in ["1010", "0100", "1000", "0000", "0000", "0000", "0000", "0001"]
    )
    glyph = GlyphBitmap(codepoint=ord("X"), char="X", width=4, height=8, rows=rows)
    packed = pack_glyph(glyph, FontLayout(scan="column", bit_order="msb", invert=False))
    # 列0: 行0..7 = 1,0,1,0,0,0,0,0 -> 0b1010_0000=0xA0
    # 列1: 0,1,0,0,... -> 0b0100_0000=0x40
    # 列2: 1,0,0,0,... -> 0x80
    # 列3: 0,0,0,0,0,0,0,1 -> 0x01
    assert packed == [0xA0, 0x40, 0x80, 0x01]


def test_pack_lsb_bit_order() -> None:
    rows = tuple(
        tuple(bool(int(ch)) for ch in row)
        for row in ["1000", "0000", "0000", "0000", "0000", "0000", "0000", "0000"]
    )
    glyph = GlyphBitmap(codepoint=ord("X"), char="X", width=4, height=8, rows=rows)
    packed = pack_glyph(glyph, FontLayout(scan="row", bit_order="lsb", invert=False))
    # 第 0 行 [1,0,0,0] -> LSB 在前：bit0=1 -> 0x01
    assert packed == [0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]


def test_pack_invert_is_yin_code() -> None:
    rows = tuple(
        tuple(bool(int(ch)) for ch in row)
        for row in ["1000", "0000", "0000", "0000", "0000", "0000", "0000", "0000"]
    )
    glyph = GlyphBitmap(codepoint=ord("X"), char="X", width=4, height=8, rows=rows)
    packed = pack_glyph(glyph, FontLayout(scan="row", bit_order="msb", invert=True))
    assert packed[0] == 0x7F  # 0x80 取反
    assert packed[1:] == [0xFF] * 7


# ---------------------------------------------------------------- 布局与字体解析


def test_layout_presets_match_controllers() -> None:
    # SSD1306/SH1106 系列页寻址约定：位 0（LSB）= 页顶（Adafruit/u8g2 一致）。
    # 历史版本误配 msb 导致字形在 8 行块内上下颠倒（oled9 实机确认）。
    assert CONTROLLER_LAYOUTS["ssd1306"] == ("column", "lsb", False)
    assert CONTROLLER_LAYOUTS["ssd1315"] == ("column", "lsb", False)
    assert CONTROLLER_LAYOUTS["sh1106"] == ("column", "lsb", False)
    assert CONTROLLER_LAYOUTS["pcd8544"] == ("row", "msb", False)
    assert CONTROLLER_LAYOUTS["ili9341"] == ("row", "msb", False)
    layout = resolve_layout("ssd1306", None, None, None)
    assert layout == FontLayout(scan="column", bit_order="lsb", invert=False)


def test_layout_explicit_overrides_controller() -> None:
    layout = resolve_layout("ssd1306", "row", "lsb", True)
    assert layout == FontLayout(scan="row", bit_order="lsb", invert=True)


def test_layout_unknown_controller_raises() -> None:
    with pytest.raises(FontBitmapError) as exc:
        resolve_layout("nosuchchip", None, None, None)
    assert exc.value.code == "controller_unknown"
    assert "ssd1306" in str(exc.value)


def test_resolve_font_known_name(tmp_path: Path) -> None:
    if not HAS_CONSOLA:
        pytest.skip("缺少 consola.ttf")
    resolved = resolve_font_path("consola")
    assert resolved.is_file()
    assert resolved.suffix == ".ttf"


def test_resolve_font_unknown_name_raises() -> None:
    with pytest.raises(FontBitmapError) as exc:
        resolve_font_path("no-such-font")
    assert exc.value.code == "font_unknown"


def test_resolve_font_absolute_outside_boundary_raises(tmp_path: Path) -> None:
    outside = tmp_path / "evil.ttf"
    outside.write_bytes(b"x" * 8)
    with pytest.raises(FontBitmapError) as exc:
        resolve_font_path(str(outside))
    assert exc.value.code == "font_path_outside_boundary"


def test_resolve_font_absolute_within_project(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    custom = project / "myfont.ttf"
    custom.write_bytes(b"x" * 8)
    resolved = resolve_font_path(str(custom), project_path=project)
    assert resolved == custom.resolve()


def test_resolve_font_rejects_bad_suffix(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    bad = project / "font.txt"
    bad.write_text("not a font")
    with pytest.raises(FontBitmapError) as exc:
        resolve_font_path(str(bad), project_path=project)
    assert exc.value.code == "font_suffix_not_allowed"


# ---------------------------------------------------------------- 取模主流程


def test_default_cell_size_ascii_and_cjk() -> None:
    assert default_cell_size("ABC 123") == (8, 16)
    assert default_cell_size("你好") == (16, 16)
    assert default_cell_size("A好") == (16, 16)


@needs_consola
def test_extract_ascii_deterministic() -> None:
    first = extract_font_bitmap(text="AB", font="consola")
    second = extract_font_bitmap(text="AB", font="consola")
    assert first.c_code == second.c_code
    assert first.width == 8
    assert first.height == 16
    assert first.bytes_per_glyph == 16
    assert [glyph.char for glyph in first.glyphs] == ["A", "B"]
    # 'A' 的字形数据必须有非零字节
    a_bytes = first.c_code.split("/* [0]")[1].split("};")[0]
    tokens = [token.strip() for token in a_bytes.split(",")]
    assert any(
        token.startswith("0x") and token != "0x00" for token in tokens
    )


@needs_consola
def test_extract_text_dedup_preserves_order() -> None:
    result = extract_font_bitmap(text="BABBA", font="consola")
    assert [glyph.char for glyph in result.glyphs] == ["B", "A"]


@needs_consola
def test_extract_space_is_blank_glyph() -> None:
    result = extract_font_bitmap(text="A ", font="consola")
    space = result.glyphs[1]
    assert space.char == " "
    assert all(not pixel for row in space.rows for pixel in row)
    # 空格打包后全零（比例模式单元宽 = 空格 advance，长度随之变化）
    packed = pack_glyph(space, result.layout)
    assert packed == [0x00] * len(packed)


@needs_consola
def test_extract_custom_cell_and_layout() -> None:
    result = extract_font_bitmap(
        text="A",
        font="consola",
        width=12,
        height=12,
        controller="ssd1306",
    )
    assert result.width == 12
    assert result.height == 12
    assert result.bytes_per_glyph == 12 * ((12 + 7) // 8)  # 逐列: W * ceil(H/8)
    assert "FONT_12X12" in result.c_code
    assert "逐列式" in result.c_code


@needs_simsun
def test_extract_cjk_emits_codepoint_table() -> None:
    result = extract_font_bitmap(text="你好", font="simsun")
    assert result.width == 16
    assert result.height == 16
    assert result.bytes_per_glyph == 32
    assert "FONT_16X16_CODEPOINTS" in result.c_code
    assert "0x4F60" in result.c_code  # '你'
    assert "0x597D" in result.c_code  # '好'


@needs_consola
def test_extract_missing_glyph_raises() -> None:
    with pytest.raises(FontBitmapError) as exc:
        extract_font_bitmap(text="你", font="consola")
    assert exc.value.code == "font_missing_glyphs"
    assert "simhei" in str(exc.value)


def test_extract_array_name_validation() -> None:
    with pytest.raises(FontBitmapError) as exc:
        extract_font_bitmap(text="A", font="consola", array_name="1bad name")
    assert exc.value.code == "array_name_invalid"


def test_extract_empty_text_raises() -> None:
    with pytest.raises(FontBitmapError) as exc:
        extract_font_bitmap(text="  \t\n", font="consola")
    assert exc.value.code == "text_empty"


def test_extract_bad_align_raises() -> None:
    with pytest.raises(FontBitmapError) as exc:
        extract_font_bitmap(text="A", font="consola", align="right")
    assert exc.value.code == "align_unknown"


@needs_msyh
def test_extract_mixed_proportional_msyh() -> None:
    """比例混合字库：ASCII 按实际字宽取模并左对齐，'l' 窄 'w' 宽。"""
    result = extract_font_bitmap(
        text="helloworld你好世界",
        font="msyh",
        controller="ssd1306",
        ascii_half_width=True,
    )
    widths = {glyph.char: glyph.width for glyph in result.glyphs}
    assert widths["l"] < widths["w"]          # 比例宽度
    assert widths["w"] > 8                     # 宽字母不被 8px 半宽压扁
    assert widths["你"] == 16 and widths["好"] == 16  # 中文全宽

    def line_width(line: str) -> int:
        return sum(result.glyph_widths[result.text.index(ch)] for ch in line)

    assert line_width("helloworld") < 128      # 128px 屏放得下
    assert line_width("你好世界") == 64
    # 'l' 左对齐：字形首列存在墨点（不是居中留白）
    l_glyph = result.glyphs[result.text.index("l")]
    assert any(row[0] for row in l_glyph.rows)
    # 总字节数 = 各字形字节之和（offsets 动态）
    assert result.glyph_offsets[-1] == result.total_bytes


def test_u8g2_8x13_extract_glyph_a() -> None:
    """u8g2 内置点阵字体：不依赖系统字体，字形与 BDF 源一致。"""
    result = extract_font_bitmap(text="A", font="u8g2_8x13")
    assert result.font_name == "u8g2_8x13"
    assert result.width == 8
    assert result.height == 13
    glyph = result.glyphs[0]
    assert glyph.width == 8
    assert glyph.height == 13
    # BDF 'A' 位图：第 2 行 0x18 -> 列 3、4 点亮
    assert tuple(glyph.rows[2]) == (False, False, False, True, True, False, False, False)
    # 第 7 行 0x7E -> 列 1..6 点亮
    assert tuple(glyph.rows[7]) == (False, True, True, True, True, True, True, False)
    # 等宽 advance
    assert result.glyph_widths == [8]


def test_u8g2_5x7_deterministic() -> None:
    first = extract_font_bitmap(text="AB", font="u8g2_5x7").c_code
    second = extract_font_bitmap(text="AB", font="u8g2_5x7").c_code
    assert first == second
    assert "FONT_5X7" in first


def test_u8g2_rejects_cjk() -> None:
    with pytest.raises(FontBitmapError) as exc:
        extract_font_bitmap(text="你", font="u8g2_8x13")
    assert exc.value.code == "font_missing_glyphs"
    assert "msyhbd" in str(exc.value)


def test_u8g2_via_tool(tmp_path: Path) -> None:
    registry = create_core_tool_registry()
    outcome = registry.dispatch(
        ToolCall(
            call_id="u8g2-1",
            tool_name="font.extract",
            arguments={"text": "AB", "font": "u8g2_8x13"},
        ),
        _context(tmp_path),
    )
    assert outcome.call.status == "succeeded"
    result = outcome.call.result
    assert result is not None
    assert result["glyphs"] == ["A", "B"]
    assert result["array_name"] == "FONT_8X13"
    assert "const uint8_t FONT_8X13[]" in result["c_code"]


@needs_simsun
def test_extract_mixed_half_width_ascii_cjk() -> None:
    """混合中英文字库：ASCII 半宽 8px，CJK 全宽 16px，可居中混排。"""
    result = extract_font_bitmap(
        text="helloword你好世界",
        font="simsun",
        controller="ssd1306",
        ascii_half_width=True,
    )
    assert [glyph.char for glyph in result.glyphs] == [
        "h", "e", "l", "o", "w", "r", "d", "你", "好", "世", "界",
    ]
    assert result.ascii_half_width is True
    assert result.glyph_widths == [8, 8, 8, 8, 8, 8, 8, 16, 16, 16, 16]
    # 7 个 ASCII 各 16 字节 + 4 个 CJK 各 32 字节
    assert result.glyph_offsets == [0, 16, 32, 48, 64, 80, 96, 112, 144, 176, 208, 240]
    assert result.total_bytes == 240
    # 128px SSD1306 两行都能居中放下
    def line_width(line: str) -> int:
        return sum(
            result.glyph_widths[result.text.index(ch)] for ch in line
        )

    assert line_width("helloword") == 72
    assert line_width("你好世界") == 64
    assert "FONT_16X16_GLYPH_WIDTHS" in result.c_code
    assert "FONT_16X16_GLYPH_OFFSETS" in result.c_code
    assert "FONT_16X16_CODEPOINTS" in result.c_code
    assert "[8px]" in result.c_code


@needs_simsun
def test_half_width_pure_ascii_is_unchanged() -> None:
    """纯 ASCII + ascii_half_width 不产生半宽；显式尺寸 = 固定等宽字模。"""
    result = extract_font_bitmap(
        text="hello",
        font="consola",
        width=8,
        height=16,
        ascii_half_width=True,
    )
    assert result.ascii_half_width is False
    assert result.proportional_ascii is False
    # "hello" 去重后为 h,e,l,o 四个字形，固定 8x16
    assert result.glyph_widths == [8, 8, 8, 8]
    assert result.bytes_per_glyph == 16


@needs_msyh
def test_ascii_baseline_alignment() -> None:
    """ASCII 字形垂直基线对齐：字母底边落在同一条基线上，降部向下伸。"""
    result = extract_font_bitmap(text="eg", font="msyh")
    by_char = {glyph.char: glyph for glyph in result.glyphs}

    def ink_bottom(glyph: GlyphBitmap) -> int:
        return max(
            (y for y, row in enumerate(glyph.rows) if any(row)),
            default=-1,
        )

    # 'e'（x-height）与 'h'（ascender）底边都在基线
    result2 = extract_font_bitmap(text="eh", font="msyh")
    g2 = {glyph.char: glyph for glyph in result2.glyphs}
    assert ink_bottom(g2["e"]) == ink_bottom(g2["h"])
    assert ink_bottom(g2["e"]) < result2.height  # 基线在单元内，不是贴单元底
    # 'g'（descender）底边低于基线
    assert ink_bottom(by_char["g"]) > ink_bottom(by_char["e"])


@needs_msyh
def test_default_font_msyhbd_proportional_ascii() -> None:
    """默认字体 msyhbd（微软雅黑粗体）：纯 ASCII 不显式指定尺寸时按比例排版。"""
    result = extract_font_bitmap(text="helloworld")
    assert result.font_name == "msyhbd"
    assert result.proportional_ascii is True
    widths = {glyph.char: glyph.width for glyph in result.glyphs}
    assert widths["l"] < widths["w"]   # 比例宽度
    assert widths["w"] > 8              # 不被 8px 固定单元压扁
    assert "FONT_8X16_GLYPH_WIDTHS" in result.c_code
    assert "FONT_8X16_GLYPH_OFFSETS" in result.c_code


@needs_simsun
def test_font_extract_tool_ascii_half_width_flag(tmp_path: Path) -> None:
    registry = create_core_tool_registry()
    outcome = registry.dispatch(
        ToolCall(
            call_id="font-extract-half",
            tool_name="font.extract",
            arguments={
                "text": "A你",
                "font": "simsun",
                "ascii_half_width": True,
            },
        ),
        _context(tmp_path),
    )
    assert outcome.call.status == "succeeded"
    result = outcome.call.result
    assert result is not None
    assert result["ascii_half_width"] is True
    assert result["glyph_widths"] == [8, 16]
    assert "FONT_16X16_GLYPH_WIDTHS" in result["c_code"]


# ---------------------------------------------------------------- 工具层


def test_font_extract_tool_read_only(tmp_path: Path) -> None:
    if not HAS_CONSOLA:
        pytest.skip("缺少 consola.ttf")
    registry = create_core_tool_registry()
    outcome = registry.dispatch(
        ToolCall(
            call_id="font-extract",
            tool_name="font.extract",
            arguments={"text": "OK", "font": "consola"},
        ),
        _context(tmp_path),
    )
    assert outcome.call.status == "succeeded"
    assert outcome.pending_approval is None
    result = outcome.call.result
    assert result is not None
    assert result["glyphs"] == ["O", "K"]
    assert "const uint8_t FONT_8X16[]" in result["c_code"]
    assert not (tmp_path / "anything").exists()


def test_font_extract_tool_invalid_arguments() -> None:
    registry = create_core_tool_registry()
    outcome = registry.dispatch(
        ToolCall(
            call_id="font-extract-bad",
            tool_name="font.extract",
            arguments={"text": "A", "font": "no-such-font"},
        ),
        _context(Path("unused")),
    )
    assert outcome.call.status == "failed"
    assert outcome.call.failure is not None
    assert outcome.call.failure.code == "font_font_unknown"


# ---------------------------------------------------------------- 修复回归
# （oled6 乱码根因：降采样阈值 0.5 丢弃小字号细笔画；ascii_font 让英文走
#   u8g2 像素级点阵；头文件写明列主序读取契约）


@needs_simsun
def test_threshold_preserves_small_ascii_glyph_structure() -> None:
    """回归：降采样阈值（0.3，原 0.5）不得把 8px 小字号 TTF 字形的
    横杠/双腿整段丢弃（oled6 的 A/B/C/D 乱码根因）。"""
    result = extract_font_bitmap(
        text="A",
        font="simsun",
        width=8,
        height=16,
        controller="sh1106",
        align="left",
    )
    rows = result.glyphs[0].rows
    # 中部某行应有 >=4 个连续点亮像素（'A' 的横杠）
    assert any(sum(1 for px in row if px) >= 4 for row in rows[6:11])
    # 底部区域左右两列同时有点（'A' 的双腿）
    assert any(row[0] and row[6] for row in rows[11:15])


@needs_simsun
def test_mixed_ascii_font_uses_u8g2_for_ascii() -> None:
    """回归：ascii_font 指定 u8g2 时，混合字库的 ASCII 用 u8g2 像素级
    点阵（不经过 TTF 降采样阈值），CJK 仍用主字体。"""
    result = extract_font_bitmap(
        text="AB你",
        font="simsun",
        controller="sh1106",
        ascii_half_width=True,
        ascii_font="u8g2_5x7",
    )
    assert result.ascii_font == "u8g2_5x7"
    assert result.glyph_widths == [8, 8, 16]
    assert "ascii_font: u8g2_5x7" in result.c_code
    # 'A' 应为 u8g2_5x7 源位图，放在 8x16 单元底部（5x7）
    adv, w, h, bdf_rows = U8G2_FONTS["u8g2_5x7"]["glyphs"][ord("A")]
    src_rows: list[tuple[bool, ...]] = []
    row_bytes = (w + 7) // 8
    for rv in bdf_rows:
        bits: list[bool] = []
        for b in range(row_bytes):
            byte = (rv >> (8 * (row_bytes - 1 - b))) & 0xFF
            for bit in range(8):
                bits.append(bool(byte & (0x80 >> bit)))
        src_rows.append(tuple(bits[:w]))
    cell = result.glyphs[0].rows
    off_y = 16 - 7
    assert all(cell[off_y + y][:w] == row for y, row in enumerate(src_rows))


def test_mixed_ascii_font_rejects_non_u8g2() -> None:
    with pytest.raises(FontBitmapError) as exc:
        extract_font_bitmap(
            text="A你",
            font="simsun",
            ascii_half_width=True,
            ascii_font="not-a-font",
        )
    assert exc.value.code == "font_unknown"


@needs_simsun
def test_generated_header_documents_column_major_contract() -> None:
    """回归：生成头文件必须写明驱动读取契约（glyph[col * pages + page]），
    避免驱动按错误布局读取导致显示乱码。"""
    result = extract_font_bitmap(
        text="A你",
        font="simsun",
        controller="sh1106",
        ascii_half_width=True,
    )
    assert "驱动读取契约（列主序" in result.c_code
    assert "c * pages + p" in result.c_code


@needs_consola
def test_font_export_tool_requires_approval_then_writes(tmp_path: Path) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    registry = create_core_tool_registry()
    call = ToolCall(
        call_id="font-export",
        tool_name="font.export",
        arguments={
            "text": "Hello",
            "font": "consola",
            "controller": "ssd1306",
            "file_path": "main/font_8x16.h",
        },
    )
    waiting = registry.dispatch(call, _context(project))
    assert waiting.call.status == "waiting_approval"
    assert waiting.pending_approval is not None
    assert waiting.pending_approval.tool_name == "font.export"

    approved = registry.dispatch(call, _context(project), approved=True)
    assert approved.call.status == "succeeded"
    result = approved.call.result
    assert result is not None
    assert result["written"] is True
    assert result["file_path"] == "main/font_8x16.h"

    target = project / "main" / "font_8x16.h"
    assert target.is_file()
    content = target.read_text(encoding="utf-8")
    assert "Auto-generated by LUXAR font.extract" in content
    assert "const uint8_t FONT_8X16[]" in content
    assert "0x" in content


@needs_consola
def test_font_export_rejects_path_outside_project(tmp_path: Path) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    registry = create_core_tool_registry()
    outcome = registry.dispatch(
        ToolCall(
            call_id="font-export-outside",
            tool_name="font.export",
            arguments={
                "text": "A",
                "font": "consola",
                "file_path": str(tmp_path / "outside.h"),
            },
        ),
        _context(project),
    )
    assert outcome.call.status == "failed"
    assert outcome.call.failure is not None
    assert outcome.call.failure.code == "font_path_outside_project"


@needs_consola
def test_font_export_rejects_bad_extension(tmp_path: Path) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    registry = create_core_tool_registry()
    outcome = registry.dispatch(
        ToolCall(
            call_id="font-export-ext",
            tool_name="font.export",
            arguments={
                "text": "A",
                "font": "consola",
                "file_path": "main/font.txt",
            },
        ),
        _context(project),
    )
    assert outcome.call.status == "failed"
    assert outcome.call.failure is not None
    assert outcome.call.failure.code == "font_path_outside_project"


@needs_consola
def test_font_export_rejected_by_user_does_not_write(tmp_path: Path) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    registry = create_core_tool_registry()
    call = ToolCall(
        call_id="font-export-reject",
        tool_name="font.export",
        arguments={
            "text": "A",
            "font": "consola",
            "file_path": "main/font.h",
        },
    )
    outcome = registry.dispatch(call, _context(project), approved=False)
    assert outcome.call.status == "rejected"
    assert not (project / "main" / "font.h").exists()
