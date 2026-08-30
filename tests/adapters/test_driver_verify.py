"""引用式驱动校验引擎测试。"""

from __future__ import annotations

from luxar.adapters.driver_verify import ReferencedAtom, check_driver


def _sh1106_ref(excerpt: str, entity_id: str = "chip-sh1106") -> ReferencedAtom:
    return ReferencedAtom(
        knowledge_id="pa-1",
        excerpt=excerpt,
        entity_id=entity_id,
        subject="power_up_sequence",
    )


def test_all_bytes_referenced_passes() -> None:
    code = (
        "static void oled_init(void) {\n"
        "    oled_write_cmd(0xAE);\n"
        "    oled_write_cmd(0xD5);\n"
        "    oled_write_cmd(0x80);\n"
        "    oled_write_cmd(0xAF);\n"
        "}\n"
    )
    ref = _sh1106_ref("0xAE 0xD5 0x80 0xAF")
    result = check_driver(code, [ref])
    assert result.ok, result.violations


def test_invented_byte_reported_with_line() -> None:
    """oled10 历史错误：模型把 SSD1306 的 0x8D 记混给 SH1106。"""
    code = (
        "static void oled_init(void) {\n"
        "    oled_write_cmd(0xAE);\n"
        "    oled_write_cmd(0x8D);\n"   # ← 手册里没有这个字节
        "    oled_write_cmd(0x14);\n"
        "    oled_write_cmd(0xAF);\n"
        "}\n"
    )
    ref = _sh1106_ref("0xAE 0xAF")
    result = check_driver(code, [ref])
    assert not result.ok
    # 0x8D 和 0x14 都应报违规，且带行号
    assert any(v.byte_value == 0x8D for v in result.violations)
    assert any(v.line == 3 for v in result.violations)
    assert any(v.byte_value == 0x14 for v in result.violations)


def test_excerpt_suffix_hex_notation() -> None:
    """手册用 AEH 记法也能匹配（oled9 场景）。"""
    code = "cmd(0xAE); cmd(0xAF);"
    ref = _sh1106_ref("Display OFF/ON: (AEH - AFH)")
    result = check_driver(code, [ref])
    assert result.ok, result.violations


def test_unattributed_reference_rejected() -> None:
    """引用原子无 entity_id（可能是模型记忆）→ 校验不通过。"""
    code = "cmd(0xAE);"
    ref = _sh1106_ref("0xAE 0xAF", entity_id="")
    result = check_driver(code, [ref])
    assert not result.ok
    assert "power_up_sequence" in result.unattributed_refs


def test_multi_reference_union() -> None:
    """多个引用原子的字节取并集。"""
    code = "cmd(0xAE); cmd(0xD5, 0x80);"
    refs = [
        _sh1106_ref("0xAE 0xAF"),
        _sh1106_ref("0xD5 0x80"),
    ]
    result = check_driver(code, refs)
    assert result.ok, result.violations


def test_large_values_ignored() -> None:
    """非字节值（0x3C 地址、0x400000 时钟等）不参与字节校验。"""
    code = (
        "#define OLED_ADDR 0x3C\n"
        "#define I2C_FREQ 400000\n"
        "cmd(0xAE);\n"
    )
    ref = _sh1106_ref("0xAE 0xAF")
    result = check_driver(code, [ref])
    # 0x3C 是地址（也可能在手册里），但这里没引用 → 按规则应报；
    # 若手册原文含 0x3C 则通过。此处引用不含 0x3C，确认会被拦。
    assert any(v.byte_value == 0x3C for v in result.violations)
    # 400000 > 0xFF 不参与
    assert not any(v.byte_value == 400000 for v in result.violations)
