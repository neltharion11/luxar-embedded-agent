"""芯片 Skill 规格层：每芯片一个 YAML 的单一事实来源。

对应设计文档 docs/superpowers/specs/2026-08-29-luxar-chip-skill-spec-schema-design.md。
"""

from luxar.specs.chip_skill import (
    ChipSkill,
    ChipFacts,
    ChipSkillError,
    DiagnosticPattern,
    DisplayOptions,
    DisplaySpec,
    FactSource,
    InitCommand,
    LayoutSpec,
    ScreenSpec,
    Spec,
    VerificationEntry,
    append_verification,
    available_controllers,
    clear_chip_cache,
    derive_verified,
    draft_chip_spec,
    find_chip_skill,
    load_builtin_chips,
    load_chip_skill,
    spec_path_for,
    write_chip_skill,
)

__all__ = [
    "ChipSkill",
    "ChipFacts",
    "ChipSkillError",
    "DiagnosticPattern",
    "DisplayOptions",
    "DisplaySpec",
    "FactSource",
    "InitCommand",
    "LayoutSpec",
    "ScreenSpec",
    "Spec",
    "VerificationEntry",
    "append_verification",
    "available_controllers",
    "clear_chip_cache",
    "derive_verified",
    "draft_chip_spec",
    "find_chip_skill",
    "load_builtin_chips",
    "load_chip_skill",
    "spec_path_for",
    "write_chip_skill",
]
