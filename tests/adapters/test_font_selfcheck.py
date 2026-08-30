"""显示自检：头文件解析、帧重建、display.verify / selfcheck_template 测试。"""

from __future__ import annotations

import os
import re
import zlib
from pathlib import Path

import pytest

from luxar.adapters.continuous_agent_tools import create_core_tool_registry
from luxar.adapters.font_bitmap import (
    FontBitmapError,
    extract_font_bitmap,
    frame_crc32,
    frame_sha256,
    render_frame_bytes,
)
from luxar.adapters.font_header import ParsedFontHeader, parse_font_header
from luxar.adapters.font_selfcheck import (
    DISPLAY_SELFCHECK_TEMPLATES,
    verify_display_selfcheck,
)
from luxar.domain.continuous_agent.steps import ToolCall
from luxar.ports.agent_tool import AgentToolExecutionContext

WINDOWS_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
HAS_CONSOLA = (WINDOWS_FONTS / "consola.ttf").is_file()
HAS_SIMSUN = (WINDOWS_FONTS / "simsun.ttc").is_file()

needs_consola = pytest.mark.skipif(
    not HAS_CONSOLA, reason="缺少系统字体 consola.ttf"
)
needs_simsun = pytest.mark.skipif(
    not HAS_SIMSUN, reason="缺少系统字体 simsun.ttc"
)


def _context(project_path: Path) -> AgentToolExecutionContext:
    return AgentToolExecutionContext(
        session_id="session-1",
        turn_id="turn-1",
        project_key="0:test4",
        project_path=project_path,
    )


def _write_font_header(
    project: Path,
    *,
    text: str,
    font: str,
    controller: str = "ssd1306",
    ascii_half_width: bool = True,
    width: int | None = None,
    height: int | None = None,
    name: str = "main/font_test.h",
) -> Path:
    result = extract_font_bitmap(
        text=text,
        font=font,
        controller=controller,
        ascii_half_width=ascii_half_width,
        width=width,
        height=height,
    )
    target = project / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(result.c_code, encoding="utf-8")
    return target


# ---------------------------------------------------------------- 头文件解析


@needs_simsun
def test_parse_font_header_mixed(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    header = _write_font_header(project, text="A你", font="simsun")
    parsed: ParsedFontHeader = parse_font_header(header)
    assert parsed.array_name == "FONT_16X16"
    assert parsed.width == 16
    assert parsed.height == 16
    assert parsed.glyph_count == 2
    assert parsed.glyph_widths == [8, 16]
    assert parsed.glyph_offsets == [0, 16, 48]
    assert parsed.codepoints == [ord("A"), ord("你")]
    assert len(parsed.data) == 48
    assert len(parsed.glyph_crc32s) == 2
    # 数据完整性锚点与解析结果一致
    assert f"{zlib.crc32(parsed.data) & 0xFFFFFFFF:08X}" == parsed.data_crc32
    assert len(parsed.data_sha256) == 64
    # 每字形 crc32 与工具结果一致（同一取模参数）
    result = extract_font_bitmap(
        text="A你", font="simsun", controller="ssd1306", ascii_half_width=True
    )
    assert parsed.glyph_crc32s == result.glyph_crc32s


@needs_consola
def test_parse_font_header_uniform_ascii(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    header = _write_font_header(
        project,
        text="AB",
        font="consola",
        ascii_half_width=False,
        width=8,
        height=16,
    )
    parsed = parse_font_header(header)
    assert parsed.width == 8
    assert parsed.height == 16
    assert parsed.glyph_widths == [8, 8]
    # 无 GLYPH_OFFSETS 表时按 bytes_per_glyph 推导
    assert parsed.glyph_offsets == [0, 16, 32]
    assert parsed.codepoints == [0x41, 0x42]


def test_parse_font_header_rejects_non_luxar(tmp_path: Path) -> None:
    header = tmp_path / "fake.h"
    header.write_text("#ifndef X\n#define X\nconst uint8_t font[] = {0x00};\n#endif\n")
    with pytest.raises(FontBitmapError) as exc:
        parse_font_header(header)
    assert exc.value.code == "font_header_not_luxar"


# ---------------------------------------------------------------- 帧重建与校验


@needs_simsun
def test_render_frame_and_selfcheck_match(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    header = _write_font_header(project, text="AB你", font="simsun")
    frame = render_frame_bytes(
        data=parse_font_header(header).data,
        glyph_offsets=parse_font_header(header).glyph_offsets,
        glyph_widths=parse_font_header(header).glyph_widths,
        codepoints=parse_font_header(header).codepoints,
        glyph_height=16,
        text="A你",
        x=0,
        y=0,
    )
    assert len(frame) == 128 * 64 // 8
    crc = frame_crc32(frame)
    sha = frame_sha256(frame)
    assert re.fullmatch(r"[0-9A-F]{8}", crc)
    assert re.fullmatch(r"[0-9a-f]{64}", sha)
    # 正确回传 -> 通过
    ok = verify_display_selfcheck(
        header_path=header,
        lines=[("A你", 0, 0)],
        actual_crc32=crc,
        actual_sha256=sha,
    )
    assert ok.match is True
    assert ok.expected_crc32 == crc
    assert ok.header_data_integrity is True
    assert ok.glyph_crc32s.get("A") is not None
    # 错误回传 -> 不通过
    bad = verify_display_selfcheck(
        header_path=header,
        lines=[("A你", 0, 0)],
        actual_crc32="DEADBEEF",
    )
    assert bad.match is False


@needs_simsun
def test_render_frame_y_must_be_page_aligned(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    header = _write_font_header(project, text="A", font="simsun")
    with pytest.raises(FontBitmapError) as exc:
        render_frame_bytes(
            data=parse_font_header(header).data,
            glyph_offsets=parse_font_header(header).glyph_offsets,
            glyph_widths=parse_font_header(header).glyph_widths,
            codepoints=parse_font_header(header).codepoints,
            glyph_height=16,
            text="A",
            x=0,
            y=3,
        )
    assert exc.value.code == "frame_y_not_page_aligned"


@needs_simsun
def test_render_frame_missing_glyph_raises(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    header = _write_font_header(project, text="A", font="simsun")
    parsed = parse_font_header(header)
    with pytest.raises(FontBitmapError) as exc:
        render_frame_bytes(
            data=parsed.data,
            glyph_offsets=parsed.glyph_offsets,
            glyph_widths=parsed.glyph_widths,
            codepoints=parsed.codepoints,
            glyph_height=16,
            text="B",
            x=0,
            y=0,
        )
    assert exc.value.code == "glyph_not_in_font"


def test_selfcheck_c_template_crc_matches_zlib() -> None:
    """模板内嵌 CRC32 表 + C 算法与 Python zlib.crc32 结果一致。"""
    source = DISPLAY_SELFCHECK_TEMPLATES["display_selfcheck.c"]
    assert "FONT_CHECK" in source
    assert "0xEDB88320" in source
    assert "display_selfcheck.h" in DISPLAY_SELFCHECK_TEMPLATES
    table_text = source.split("static const uint32_t s_crc32_table[256] = {")[1]
    table_text = table_text.split("};")[0]
    table = [
        int(item, 16)
        for item in re.findall(r"0x([0-9A-Fa-f]{8})U", table_text)
    ]
    assert len(table) == 256
    # 用与 C 实现相同的表驱动逻辑复算
    def crc32_table_driven(data: bytes) -> int:
        crc = 0xFFFFFFFF
        for byte in data:
            crc = table[(crc ^ byte) & 0xFF] ^ (crc >> 8)
        return crc ^ 0xFFFFFFFF

    payload = bytes(range(256)) + b"hello world \x00\xff"
    assert crc32_table_driven(payload) == (zlib.crc32(payload) & 0xFFFFFFFF)


# ---------------------------------------------------------------- 工具层


@needs_simsun
def test_display_verify_tool_match_and_mismatch(tmp_path: Path) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    header = _write_font_header(project, text="AB你", font="simsun")
    registry = create_core_tool_registry()
    expected = frame_crc32(
        render_frame_bytes(
            data=parse_font_header(header).data,
            glyph_offsets=parse_font_header(header).glyph_offsets,
            glyph_widths=parse_font_header(header).glyph_widths,
            codepoints=parse_font_header(header).codepoints,
            glyph_height=16,
            text="A你",
            x=0,
            y=0,
        )
    )
    ok = registry.dispatch(
        ToolCall(
            call_id="verify-ok",
            tool_name="display.verify",
            arguments={
                "header_path": "main/font_test.h",
                "lines": [{"text": "A你", "x": 0, "y": 0}],
                "actual_crc32": expected,
            },
        ),
        _context(project),
    )
    assert ok.call.status == "succeeded"
    assert ok.call.result is not None
    assert ok.call.result["match"] is True
    assert ok.call.result["expected_crc32"] == expected

    bad = registry.dispatch(
        ToolCall(
            call_id="verify-bad",
            tool_name="display.verify",
            arguments={
                "header_path": "main/font_test.h",
                "lines": [{"text": "A你", "x": 0, "y": 0}],
                "actual_crc32": "00000000",
            },
        ),
        _context(project),
    )
    assert bad.call.status == "failed"
    assert bad.call.failure is not None
    assert bad.call.failure.code == "display_selfcheck_mismatch"


@needs_simsun
def test_display_verify_rejects_outside_project(tmp_path: Path) -> None:
    project = tmp_path / "test4"
    project.mkdir()
    registry = create_core_tool_registry()
    outcome = registry.dispatch(
        ToolCall(
            call_id="verify-outside",
            tool_name="display.verify",
            arguments={
                "header_path": str(tmp_path / "outside.h"),
                "lines": [{"text": "A", "x": 0, "y": 0}],
            },
        ),
        _context(project),
    )
    assert outcome.call.status == "failed"
    assert outcome.call.failure is not None
    assert outcome.call.failure.code == "font_header_outside_project"


def test_selfcheck_template_tool_output() -> None:
    registry = create_core_tool_registry()
    outcome = registry.dispatch(
        ToolCall(
            call_id="selfcheck-tpl",
            tool_name="display.selfcheck_template",
            arguments={},
        ),
        _context(Path("unused")),
    )
    assert outcome.call.status == "succeeded"
    result = outcome.call.result
    assert result is not None
    assert set(result["files"]) == {"display_selfcheck.h", "display_selfcheck.c"}
    assert "FONT_CHECK" in result["display_selfcheck.c"]
    assert "display_selfcheck_crc32" in result["display_selfcheck.h"]


# ---------------------------------------------------------------- 约定级校验（第 4/5 项）


@needs_simsun
def test_parse_font_header_reads_layout_marks(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    header = _write_font_header(project, text="A你", font="simsun", controller="sh1106")
    parsed = parse_font_header(header)
    assert parsed.layout_scan == "column"
    assert parsed.layout_bit_order == "lsb"
    assert parsed.layout_invert is False


@needs_simsun
def test_verify_layout_mismatch_raises_before_crc(tmp_path: Path) -> None:
    """字模用 msb 打包、控制器规格是 lsb -> 自检阶段直接报错（无需真机）。"""
    project = tmp_path / "p"
    project.mkdir()
    # 用显式 bit_order=msb 打包（与 sh1106 规格 lsb 冲突）
    result = extract_font_bitmap(
        text="A你",
        font="simsun",
        scan="column",
        bit_order="msb",
        invert=False,
        ascii_half_width=True,
    )
    header = project / "main" / "font_bad.h"
    header.parent.mkdir(parents=True, exist_ok=True)
    header.write_text(result.c_code, encoding="utf-8")
    with pytest.raises(FontBitmapError) as exc:
        verify_display_selfcheck(
            header_path=header,
            lines=[("A", 0, 0)],
            controller="sh1106",
        )
    assert exc.value.code == "display_layout_mismatch"
    assert exc.value.details["header_layout"]["bit_order"] == "msb"
    assert exc.value.details["spec_layout"]["bit_order"] == "lsb"
    assert "高位在前" in str(exc.value)  # 人类可读：字模是 MSB
    assert "低位在前" in str(exc.value)  # 控制器规格是 LSB
    assert "sh1106" in str(exc.value)


@needs_simsun
def test_verify_layout_consistent_passes(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    header = _write_font_header(project, text="A你", font="simsun", controller="sh1106")
    ok = verify_display_selfcheck(
        header_path=header,
        lines=[("A", 0, 0)],
        controller="sh1106",
    )
    assert ok.layout_consistent is True
    assert ok.spec_verified == "candidate"  # sh1106 已真机一次（oled9）
    assert ok.header_layout is not None
    assert ok.header_layout.bit_order == "lsb"


@needs_simsun
def test_verify_unknown_controller_skips_layout_check(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    header = _write_font_header(project, text="A你", font="simsun", controller="sh1106")
    # 不存在的控制器：跳过约定级校验，CRC 逻辑照常
    ok = verify_display_selfcheck(
        header_path=header,
        lines=[("A", 0, 0)],
        controller="nosuchchip",
    )
    assert ok.layout_consistent is True
    assert ok.spec_verified is None


@needs_simsun
def test_verify_unverified_spec_reports_spec_verified(tmp_path: Path) -> None:
    """第 5 项：verified=false 芯片在结果中给出 spec_verified 供提示位序未真机验证。"""
    project = tmp_path / "p"
    project.mkdir()
    header = _write_font_header(project, text="A你", font="simsun", controller="ssd1306")
    ok = verify_display_selfcheck(
        header_path=header,
        lines=[("A", 0, 0)],
        controller="ssd1306",
    )
    assert ok.layout_consistent is True
    assert ok.spec_verified == "unverified"  # ssd1306 未真机验证


@needs_consola
def test_display_verify_tool_reports_layout_conflict(tmp_path: Path) -> None:
    """工具层：controller 与字模位序冲突 -> display_layout_mismatch。"""
    from luxar.adapters.font_bitmap import extract_font_bitmap

    project = tmp_path / "test4"
    project.mkdir()
    result = extract_font_bitmap(
        text="AB",
        font="consolab",
        width=8,
        height=16,
        scan="column",
        bit_order="msb",
        invert=False,
    )
    header = project / "main" / "font_bad.h"
    header.parent.mkdir(parents=True, exist_ok=True)
    header.write_text(result.c_code, encoding="utf-8")
    registry = create_core_tool_registry()
    outcome = registry.dispatch(
        ToolCall(
            call_id="verify-conflict",
            tool_name="display.verify",
            arguments={
                "header_path": "main/font_bad.h",
                "lines": [{"text": "A", "x": 0, "y": 0}],
                "controller": "sh1106",
            },
        ),
        _context(project),
    )
    assert outcome.call.status == "failed"
    assert outcome.call.failure is not None
    assert outcome.call.failure.code == "font_display_layout_mismatch"
