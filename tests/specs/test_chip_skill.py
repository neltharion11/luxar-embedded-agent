"""芯片 Skill 规格模型/加载/状态机测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from luxar.specs import (
    ChipSkill,
    ChipSkillError,
    VerificationEntry,
    append_verification,
    derive_verified,
    draft_chip_spec,
    find_chip_skill,
    load_builtin_chips,
    load_chip_skill,
    write_chip_skill,
)


def _entry(task: str, level: str = "L3", result: str = "pass") -> VerificationEntry:
    return VerificationEntry(
        level=level,  # type: ignore[arg-type]
        task=task,
        result=result,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------


def test_derive_verified_empty_is_unverified() -> None:
    assert derive_verified([]) == "unverified"


def test_derive_verified_l2_only_is_unverified() -> None:
    # CRC 自检（L2）证明不了位序/行序约定，不参与固化判定
    assert derive_verified([_entry("t1", level="L2")]) == "unverified"


def test_derive_verified_fail_is_unverified() -> None:
    assert derive_verified([_entry("t1", result="fail")]) == "unverified"


def test_derive_verified_one_task_is_candidate() -> None:
    assert derive_verified([_entry("t1")]) == "candidate"


def test_derive_verified_two_distinct_tasks_is_verified() -> None:
    assert derive_verified([_entry("t1"), _entry("t2")]) == "true"


def test_derive_verified_repeated_same_task_is_candidate() -> None:
    # 同一任务的重复烧录不算第二次成功（两次不同任务才固化）
    assert derive_verified([_entry("t1"), _entry("t1")]) == "candidate"


def test_l4_requires_user_confirmation() -> None:
    with pytest.raises(ValidationError):
        VerificationEntry(level="L4", task="t1", result="pass", user_confirmed=False)


# ---------------------------------------------------------------------------
# 模型校验
# ---------------------------------------------------------------------------


def _minimal() -> dict[str, object]:
    return {
        "schema": "luxar/chip-skill/2",
        "controller": "sh1106",
        "spec": {},
        "display": {
            "layout": {"scan": "column", "bit_order": "lsb", "invert": False},
            "screen": {"width": 128, "height": 64},
        },
    }


def test_minimal_model_loads() -> None:
    skill = ChipSkill.model_validate(_minimal())
    assert skill.controller == "sh1106"
    assert skill.verified == "unverified"
    assert skill.display is not None
    assert skill.display.layout.scan == "column"
    assert skill.display.layout.bit_order == "lsb"


def test_schema_version_rejected() -> None:
    raw = _minimal()
    raw["schema"] = "luxar/chip-skill/1"
    with pytest.raises(ValidationError):
        ChipSkill.model_validate(raw)


def test_screen_height_allows_character_lcd() -> None:
    # 字符屏（HD44780 16x2）height 是字符行数，不是页寻址像素；
    # 页寻址的 8 倍数约束由引擎运行期校验（frame_screen_invalid），规格层不重复。
    raw = _minimal()
    raw["display"]["screen"] = {"width": 16, "height": 2}  # type: ignore[index]
    skill = ChipSkill.model_validate(raw)
    assert skill.display is not None
    assert skill.display.screen.height == 2


def test_init_command_byte_range() -> None:
    raw = _minimal()
    raw["spec"]["init"] = [{"cmd": 0xAE}, {"cmd": 0xD5, "args": [0x1FF]}]  # type: ignore[index]
    with pytest.raises(ValidationError):
        ChipSkill.model_validate(raw)


def test_init_command_accepts_hex_strings() -> None:
    """YAML 里 cmd/args 可用 0x 十六进制字符串（可读性）。"""
    raw = _minimal()
    raw["spec"]["init"] = [
        {"cmd": "0xAE"},
        {"cmd": "0xD5", "args": ["0x80"]},
    ]
    skill = ChipSkill.model_validate(raw)
    assert skill.spec.init[0].cmd == 0xAE
    assert skill.spec.init[1].cmd == 0xD5
    assert skill.spec.init[1].args == [0x80]


def test_non_display_spec_without_display_block() -> None:
    """非显示设备（传感器等）：无 display 块，只有通用 spec。"""
    skill = ChipSkill.model_validate(
        {
            "schema": "luxar/chip-skill/2",
            "controller": "bmp280",
            "spec": {
                "init": [{"cmd": 0xB6}],
                "driver_template": "i2c_register",
            },
        }
    )
    assert skill.display is None
    assert skill.spec.init[0].cmd == 0xB6


def test_layout_scan_enum() -> None:
    raw = _minimal()
    raw["display"]["layout"] = {"scan": "diagonal", "bit_order": "lsb"}  # type: ignore[index]
    with pytest.raises(ValidationError):
        ChipSkill.model_validate(raw)


def test_verified_true_without_records_rejected() -> None:
    raw = _minimal()
    raw["verified"] = "true"
    with pytest.raises(ValidationError):
        ChipSkill.model_validate(raw)


def test_verified_true_with_single_task_rejected() -> None:
    raw = _minimal()
    raw["verified"] = "true"
    raw["verification"] = [
        {"level": "L3", "task": "t1", "result": "pass"},
    ]
    with pytest.raises(ValidationError):
        ChipSkill.model_validate(raw)


def test_verified_true_with_two_tasks_accepted() -> None:
    raw = _minimal()
    raw["verified"] = "true"
    raw["verification"] = [
        {"level": "L3", "task": "t1", "result": "pass"},
        {"level": "L4", "task": "t2", "result": "pass", "user_confirmed": True},
    ]
    skill = ChipSkill.model_validate(raw)
    assert skill.verified == "true"


def test_verified_conservative_lower_is_allowed() -> None:
    # 记录已够固化但声明保守等级：允许（提示可提升）
    raw = _minimal()
    raw["verified"] = "candidate"
    raw["verification"] = [
        {"level": "L3", "task": "t1", "result": "pass"},
        {"level": "L4", "task": "t2", "result": "pass", "user_confirmed": True},
    ]
    skill = ChipSkill.model_validate(raw)
    assert skill.verified == "candidate"


def test_extra_fields_rejected() -> None:
    raw = _minimal()
    raw["bogus"] = 1
    with pytest.raises(ValidationError):
        ChipSkill.model_validate(raw)


def test_controller_lowercased() -> None:
    raw = _minimal()
    raw["controller"] = "SH1106"
    skill = ChipSkill.model_validate(raw)
    assert skill.controller == "sh1106"


# ---------------------------------------------------------------------------
# YAML 加载
# ---------------------------------------------------------------------------


def test_load_builtin_chips_includes_sh1106() -> None:
    chips = load_builtin_chips()
    assert "sh1106" in chips
    skill = chips["sh1106"]
    assert skill.display is not None
    assert skill.display.layout.bit_order == "lsb"
    assert skill.display.column_offset == 2
    assert skill.verified == "candidate"  # oled9 一次成功，还差第二次


def test_find_chip_skill_by_alias() -> None:
    chips = load_builtin_chips()
    assert find_chip_skill("nokia5110", chips) is not None
    assert find_chip_skill("NOKIA5110", chips) is not None


def test_find_chip_skill_unknown() -> None:
    chips = load_builtin_chips()
    assert find_chip_skill("nosuchchip", chips) is None


def test_load_chip_skill_bad_yaml(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("{unclosed", encoding="utf-8")
    with pytest.raises(ChipSkillError) as exc:
        load_chip_skill(bad)
    assert exc.value.code == "spec_yaml_invalid"


def test_load_chip_skill_invalid_model(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schema: luxar/chip-skill/2\n"
        "controller: x\n"
        "spec: {}\n"
        "display:\n"
        "  layout: {scan: diagonal, bit_order: msb}\n"  # 非法枚举
        "  screen: {width: 84, height: 48}\n",
        encoding="utf-8",
    )
    with pytest.raises(ChipSkillError) as exc:
        load_chip_skill(bad)
    assert exc.value.code == "spec_invalid"


def test_builtin_sh1106_yaml_roundtrip(tmp_path) -> None:
    chips = load_builtin_chips()
    skill = chips["sh1106"]
    restored = ChipSkill.model_validate_json(skill.model_dump_json())
    assert restored == skill
    dumped = tmp_path / "roundtrip.yaml"
    dumped.write_text(skill.to_yaml(), encoding="utf-8")
    reloaded = load_chip_skill(dumped)
    assert reloaded.controller == "sh1106"
    assert reloaded.display is not None
    assert reloaded.display.column_offset == 2
    assert reloaded.verified == "candidate"


def test_builtin_specs_match_engine_presets() -> None:
    """规格层与引擎 CONTROLLER_LAYOUTS 硬编码表保持一致（§3.4 第 2 项迁移防漂移）。

    nokia5110 是 pcd8544 的别名（同一芯片），引擎表作为独立键存在属历史冗余。
    """
    from luxar.adapters.font_bitmap import CONTROLLER_LAYOUTS

    chips = load_builtin_chips()
    for controller, (scan, bit_order, invert) in CONTROLLER_LAYOUTS.items():
        skill = chips.get(controller) or find_chip_skill(controller, chips)
        assert skill is not None, f"引擎预设 {controller} 缺少对应规格 YAML"
        assert skill.display is not None, f"{controller} 缺少 display 块"
        layout = skill.display.layout
        assert layout.scan == scan, f"{controller}: scan 不一致"
        assert layout.bit_order == bit_order, f"{controller}: bit_order 不一致"
        assert layout.invert == invert, f"{controller}: invert 不一致"


# ---------------------------------------------------------------------------
# 引擎从规格读布局（§3.4 第 2 项）
# ---------------------------------------------------------------------------


def test_resolve_layout_reads_from_spec(tmp_path, monkeypatch) -> None:
    """resolve_layout 优先从规格 YAML 读布局：临时目录加新芯片即接入，无需改引擎。"""
    from luxar.adapters.font_bitmap import CONTROLLER_LAYOUTS, FontBitmapError, resolve_layout

    spec_dir = tmp_path / "chips"
    spec_dir.mkdir()
    # 新芯片 st7567：不在 CONTROLLER_LAYOUTS，只在规格里
    (spec_dir / "st7567.yaml").write_text(
        "schema: luxar/chip-skill/2\n"
        "controller: st7567\n"
        "spec: {}\n"
        "display:\n"
        "  layout: {scan: column, bit_order: lsb, invert: false}\n"
        "  screen: {width: 128, height: 64}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "luxar.specs.chip_skill._BUILTIN_DIR",
        spec_dir,
    )
    from luxar.specs import clear_chip_cache

    clear_chip_cache()
    try:
        layout = resolve_layout("st7567", None, None, None)
        assert layout.scan == "column"
        assert layout.bit_order == "lsb"
        assert layout.invert is False
        # 规格不在引擎硬编码表里也能解析——YAML 即接入
        assert "st7567" not in CONTROLLER_LAYOUTS
    finally:
        clear_chip_cache()


def test_resolve_layout_falls_back_to_engine_preset(tmp_path, monkeypatch) -> None:
    """规格缺失时回退 CONTROLLER_LAYOUTS（迁移对照表仍有效）。"""
    from luxar.adapters.font_bitmap import resolve_layout

    monkeypatch.setattr(
        "luxar.specs.chip_skill._BUILTIN_DIR",
        tmp_path,  # 空目录：无任何规格
    )
    from luxar.specs import clear_chip_cache

    clear_chip_cache()
    try:
        layout = resolve_layout("ssd1306", None, None, None)
        assert layout.scan == "column"
        assert layout.bit_order == "lsb"
    finally:
        clear_chip_cache()


def test_resolve_layout_unknown_mentions_yaml(tmp_path, monkeypatch) -> None:
    """未知控制器错误信息引导新建 YAML 接入。"""
    from luxar.adapters.font_bitmap import FontBitmapError, resolve_layout

    monkeypatch.setattr(
        "luxar.specs.chip_skill._BUILTIN_DIR",
        tmp_path,
    )
    from luxar.specs import clear_chip_cache

    clear_chip_cache()
    try:
        with pytest.raises(FontBitmapError) as exc:
            resolve_layout("nosuchchip", None, None, None)
        assert exc.value.code == "controller_unknown"
        assert "yaml" in str(exc.value).lower()
        assert "ssd1306" in str(exc.value)  # 仍提示已接入的芯片
    finally:
        clear_chip_cache()


# ---------------------------------------------------------------------------
# 起草 / 固化 / 写回（新硬件工作流）
# ---------------------------------------------------------------------------


def _draft_dict() -> dict[str, object]:
    return {
        "controller": "st7567",
        "spec": {"init": [{"cmd": 0xAE}]},
        "display": {
            "layout": {"scan": "column", "bit_order": "lsb", "invert": False},
            "screen": {"width": 128, "height": 64},
        },
        "facts": {
            "vendor": "测试厂商",
            "interfaces": ["i2c", "spi"],
            "sources": [{"kind": "library", "name": "u8g2", "confidence": "high"}],
        },
        "aliases": ["st7567r"],
    }


def test_draft_chip_spec_creates_unverified() -> None:
    skill = draft_chip_spec(**_draft_dict())
    assert skill.controller == "st7567"
    assert skill.verified == "unverified"
    assert skill.verification == []
    assert skill.display is not None
    assert skill.display.layout.scan == "column"
    assert skill.display.layout.bit_order == "lsb"
    assert skill.spec.init[0].cmd == 0xAE
    assert skill.facts.sources[0].kind == "library"


def test_draft_chip_spec_invalid_layout_raises() -> None:
    raw = _draft_dict()
    raw["display"] = {  # type: ignore[assignment]
        "layout": {"scan": "diagonal", "bit_order": "lsb"},
        "screen": {"width": 128, "height": 64},
    }
    with pytest.raises(ChipSkillError) as exc:
        draft_chip_spec(**raw)
    assert exc.value.code == "spec_draft_invalid"


def test_append_verification_promotes_to_candidate_then_true() -> None:
    skill = draft_chip_spec(**_draft_dict())
    # 一次 L3 pass -> candidate
    skill = append_verification(
        skill, {"level": "L3", "task": "oled_diag_1", "result": "pass"}
    )
    assert skill.verified == "candidate"
    # 第二次不同 task 的 L3/L4 pass -> true
    skill = append_verification(
        skill,
        {
            "level": "L4",
            "task": "oled_diag_2",
            "result": "pass",
            "user_confirmed": True,
        },
    )
    assert skill.verified == "true"
    # 同一 task 重复不算第二次
    skill2 = draft_chip_spec(**_draft_dict())
    skill2 = append_verification(skill2, _entry("t1"))
    skill2 = append_verification(skill2, _entry("t1"))
    assert skill2.verified == "candidate"


def test_append_verification_preserves_history() -> None:
    skill = draft_chip_spec(**_draft_dict())
    assert skill.verification == []
    skill = append_verification(skill, _entry("t1"))
    assert len(skill.verification) == 1
    assert skill.verified == "candidate"
    # 追加不改写已有记录：再追加后仍保留第一条
    skill = append_verification(skill, _entry("t2"))
    assert [e.task for e in skill.verification] == ["t1", "t2"]


def test_write_chip_skill_roundtrip(tmp_path) -> None:
    from luxar.specs import find_chip_skill, load_builtin_chips, load_chip_skill

    skill = draft_chip_spec(**_draft_dict())
    path = write_chip_skill(skill, directory=tmp_path)
    assert path.name == "st7567.yaml"
    reloaded = load_chip_skill(path)
    assert reloaded == skill
    # 临时目录作为规格目录可被 find_chip_skill 匹配（外部目录覆盖/扩展）
    chips = load_builtin_chips(tmp_path)
    assert find_chip_skill("st7567", chips) is not None


def test_append_verification_promote_false_keeps_unverified() -> None:
    skill = draft_chip_spec(**_draft_dict())
    skill = append_verification(
        skill, _entry("t1"), promote=False
    )
    assert skill.verified == "unverified"
    assert len(skill.verification) == 1


def test_verified_true_without_records_rejected_by_model() -> None:
    # 直接构造 verified=true 且无记录会被模型拒绝（防手改）
    raw = _draft_dict()
    raw["verified"] = "true"
    with pytest.raises(ValidationError):
        ChipSkill.model_validate(raw)
