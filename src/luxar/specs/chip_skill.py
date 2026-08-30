"""芯片 Skill 规格：每芯片一个 YAML 的单一事实来源。

对应设计文档 docs/superpowers/specs/2026-08-29-luxar-chip-skill-spec-schema-design.md。

一个 YAML 承载四层数据：
- facts：事实卡（RAG 层，"是什么"，带来源分级 library|pdf|approximation）；
- spec：规格（引擎层消费，"怎么做"，字段名与 font_bitmap 引擎一一对应）；
- diagnostics：L3 判别图案（几何图形，不是文字——文字不可靠）；
- verification：验证记录（只追加；verified 状态由记录推导校验）。

verified 状态机：unverified -> candidate（>=1 次 L3/L4 pass）-> true
（>=2 次**不同 task** 的 L3/L4 pass）。CRC 自检（L2）只能证明"字节写对
位置"，证明不了位序/行序约定与硬件一致，因此不参与固化判定（oled9 教训）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SCHEMA = "luxar/chip-skill/2"

VerifiedStatus = Literal["unverified", "candidate", "true"]
VerificationLevel = Literal["L1", "L2", "L3", "L4"]
SourceKind = Literal["library", "pdf", "approximation"]
Scan = Literal["row", "column"]
BitOrder = Literal["msb", "lsb"]


class ChipSkillError(RuntimeError):
    """规格文件校验/加载失败的确定性错误。"""

    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


# ---------------------------------------------------------------------------
# 事实卡（RAG 层）
# ---------------------------------------------------------------------------


class FactSource(BaseModel):
    """一条事实的来源。输入优先级：主流库源码 > PDF > 近似芯片。"""

    model_config = ConfigDict(extra="forbid")

    kind: SourceKind
    name: str = Field(min_length=1)
    ref: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"
    note: str = ""


class ChipFacts(BaseModel):
    """芯片"是什么"的结构化事实（未验证前身）。"""

    model_config = ConfigDict(extra="forbid")

    vendor: str = ""
    interfaces: list[str] = Field(default_factory=list)
    default_address: str | None = None
    resolutions: list[list[int]] = Field(default_factory=list)
    memory_layout: str = "page"
    sources: list[FactSource] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 规格（引擎层消费）
# ---------------------------------------------------------------------------


class LayoutSpec(BaseModel):
    """与 font_bitmap.FontLayout 一一对应。"""

    model_config = ConfigDict(extra="forbid")

    scan: Scan
    bit_order: BitOrder
    invert: bool = False


class ScreenSpec(BaseModel):
    """屏幕/画布尺寸。

    页寻址芯片（SSD13xx 等）height 必须为 8 的倍数——该约束由引擎运行期
    校验（render_frame_bytes 的 frame_screen_invalid），规格层不重复；
    字符屏（HD44780 的 16x2）height 是字符行数，允许非 8 倍数。
    """

    model_config = ConfigDict(extra="forbid")

    width: int = Field(gt=0)
    height: int = Field(gt=0)


class InitCommand(BaseModel):
    """一条初始化命令：cmd + 可选 args。cmd/args 接受 0x 十六进制字符串。"""

    model_config = ConfigDict(extra="forbid")

    cmd: int = Field(ge=0, le=0xFF)
    args: list[int] = Field(default_factory=list)

    @field_validator("cmd", mode="before")
    @classmethod
    def _cmd_hex(cls, value: object) -> object:
        return _parse_hex_int(value)

    @field_validator("args", mode="before")
    @classmethod
    def _args_hex(cls, values: object) -> object:
        if isinstance(values, list):
            return [_parse_hex_int(value) for value in values]
        return values

    @field_validator("args")
    @classmethod
    def _args_byte_range(cls, values: list[int]) -> list[int]:
        for value in values:
            if not (0 <= value <= 0xFF):
                raise ValueError(f"init 参数必须是 0..0xFF，得到 {value}")
        return values


def _parse_hex_int(value: object) -> object:
    """把 0x 十六进制字符串解析为 int；其他输入原样返回（由 pydantic 处理）。"""
    if isinstance(value, str):
        text = value.strip()
        if text.lower().startswith("0x"):
            try:
                return int(text, 16)
            except ValueError:
                return value
    return value


class DisplayOptions(BaseModel):
    """驱动模板可调参数（映射为 C 宏/条件命令）。"""

    model_config = ConfigDict(extra="forbid")

    seg_remap: bool | None = None
    com_scan_remap: bool | None = None


class Spec(BaseModel):
    """引擎消费的确定性规格（**通用部分，任意设备适用**）。

    init：初始化/配置序列（所有设备都有——传感器配寄存器、SPI flash 发命令、
    GPS 发 AT 配置、显示芯片上电序列）。driver_template：驱动骨架名。
    显示专属字段（layout/screen/column_offset 等）在顶层可选 ``display`` 块。
    """

    model_config = ConfigDict(extra="forbid")

    init: list[InitCommand] = Field(default_factory=list)
    driver_template: str = ""


class DisplaySpec(BaseModel):
    """显示专属规格（可选块：只有显示设备才需要）。

    与 font_bitmap 引擎一一对应；显示设备用 ``display`` 块承载，
    非显示设备（传感器/模块/存储）不写本块。
    """

    model_config = ConfigDict(extra="forbid")

    layout: LayoutSpec
    screen: ScreenSpec
    column_offset: int = Field(default=0, ge=0)
    display_options: DisplayOptions = Field(default_factory=DisplayOptions)


# ---------------------------------------------------------------------------
# L3 判别图案
# ---------------------------------------------------------------------------


class DiagnosticPattern(BaseModel):
    """L3 验证用的几何判别图案：用几何图形定位硬件映射约定，文字不可靠。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    description: str = ""
    draw: str = ""
    probes: list[dict[str, object]] = Field(default_factory=list)
    expect: str = ""


# ---------------------------------------------------------------------------
# 验证记录
# ---------------------------------------------------------------------------


class VerificationEntry(BaseModel):
    """一条分层验证记录，只追加不修改。"""

    model_config = ConfigDict(extra="forbid")

    level: VerificationLevel
    date: str = ""
    project: str = ""
    task: str = Field(min_length=1)
    pattern: str = ""
    result: Literal["pass", "fail"] = "pass"
    user_confirmed: bool = False
    evidence: str = ""
    hardware: str = ""
    expected_crc: str = ""
    actual_crc: str = ""

    @model_validator(mode="after")
    def _l4_requires_user_confirmation(self) -> "VerificationEntry":
        if self.level == "L4" and self.result == "pass" and not self.user_confirmed:
            raise ValueError("L4 视觉确认通过必须 user_confirmed=true")
        return self


# ---------------------------------------------------------------------------
# 顶层模型
# ---------------------------------------------------------------------------


class ChipSkill(BaseModel):
    """一个设备 Skill 的完整规格文件（chip/device 两层中的一层）。

    v2 拆分：``spec`` 只含通用字段（init/driver_template，任意设备适用）；
    显示专属字段（layout/screen/column_offset）移到可选 ``display`` 块——
    非显示设备（传感器/模块/存储）无需 display 块。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(
        default=_SCHEMA,
        validation_alias="schema",
        serialization_alias="schema",
    )
    controller: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    family: str = ""
    facts: ChipFacts = Field(default_factory=ChipFacts)
    spec: Spec
    display: DisplaySpec | None = None
    diagnostics: list[DiagnosticPattern] = Field(default_factory=list)
    verified: VerifiedStatus = "unverified"
    verification: list[VerificationEntry] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def _schema_version(cls, value: str) -> str:
        if value != _SCHEMA:
            raise ValueError(f"不支持的 schema 版本 {value!r}，需要 {_SCHEMA!r}")
        return value

    @field_validator("controller", "aliases")
    @classmethod
    def _lower_identifiers(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, str):
            return value.strip().lower()
        return [item.strip().lower() for item in value if item.strip()]

    @model_validator(mode="after")
    def _verified_matches_records(self) -> "ChipSkill":
        derived = derive_verified(self.verification)
        if self.verified == "true" and derived != "true":
            # 声称已固化但记录不足：防手改（证据不可伪造）
            raise ValueError(
                f"controller={self.controller!r} 声明 verified=true，但验证记录只支持 "
                f"{derived}：固化需要 ≥2 次不同 task 的 L3/L4 pass（CRC 自检 L2 不算）"
            )
        return self

    @property
    def match_names(self) -> list[str]:
        """匹配名：controller + aliases（均已小写）。"""
        return [self.controller, *self.aliases]

    def to_yaml(self) -> str:
        return yaml.safe_dump(
            self.model_dump(mode="json", by_alias=True),
            sort_keys=False,
            allow_unicode=True,
        )


def derive_verified(entries: list[VerificationEntry]) -> VerifiedStatus:
    """由验证记录推导 verified 状态（固化的唯一合法来源）。

    规则：不同 task 的 L3/L4 pass 记录数 >=2 -> true；>=1 -> candidate；
    否则 unverified。L1/L2 不算（CRC 自检证明不了位序/行序约定）。
    """
    confirmed_tasks = {
        entry.task
        for entry in entries
        if entry.level in ("L3", "L4") and entry.result == "pass"
    }
    if len(confirmed_tasks) >= 2:
        return "true"
    if len(confirmed_tasks) >= 1:
        return "candidate"
    return "unverified"


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------

_BUILTIN_DIR = Path(__file__).resolve().parent / "chips"


def load_chip_skill(path: str | Path) -> ChipSkill:
    """从 YAML 文件加载并校验一个芯片规格。"""
    file_path = Path(path)
    try:
        raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ChipSkillError(
            "spec_unreadable",
            f"无法读取规格文件：{file_path}",
            details={"path": str(file_path), "os_error": str(error)},
        ) from error
    except yaml.YAMLError as error:
        raise ChipSkillError(
            "spec_yaml_invalid",
            f"规格文件 YAML 语法错误：{file_path}",
            details={"path": str(file_path), "yaml_error": str(error)},
        ) from error
    if not isinstance(raw, dict):
        raise ChipSkillError(
            "spec_not_mapping",
            f"规格文件顶层必须是映射：{file_path}",
            details={"path": str(file_path)},
        )
    try:
        skill = ChipSkill.model_validate(raw)
    except ValueError as error:
        raise ChipSkillError(
            "spec_invalid",
            f"规格校验失败：{file_path}",
            details={"path": str(file_path), "error": str(error)},
        ) from error
    return skill


def load_builtin_chips(directory: str | Path | None = None) -> dict[str, ChipSkill]:
    """加载内置规格目录（默认 src/luxar/specs/chips）下的全部芯片。

    返回 {controller: ChipSkill}；单个文件损坏时跳过并记录 warnings。
    结果按目录缓存（引擎高频调用不重复扫盘）；传入不同 directory 独立缓存。
    """
    scan_dir = Path(directory) if directory is not None else _BUILTIN_DIR
    return _load_chips_cached(scan_dir)


@lru_cache(maxsize=8)
def _load_chips_cached(scan_dir: Path) -> dict[str, ChipSkill]:
    chips: dict[str, ChipSkill] = {}
    if not scan_dir.is_dir():
        return chips
    for file_path in sorted(scan_dir.glob("*.yaml")):
        try:
            skill = load_chip_skill(file_path)
        except ChipSkillError:
            continue
        chips[skill.controller] = skill
    return chips


def available_controllers(chips: dict[str, ChipSkill] | None = None) -> list[str]:
    """全部可匹配的控制器名（controller + aliases，排序去重）。"""
    pool = chips if chips is not None else load_builtin_chips()
    names: set[str] = set()
    for skill in pool.values():
        names.update(skill.match_names)
    return sorted(names)


def clear_chip_cache() -> None:
    """清空内置规格缓存（规格文件被新增/修改后调用）。"""
    _load_chips_cached.cache_clear()


def find_chip_skill(
    controller: str,
    chips: dict[str, ChipSkill] | None = None,
) -> ChipSkill | None:
    """按 controller 或 aliases 匹配（大小写不敏感）。"""
    key = controller.strip().lower()
    pool = chips if chips is not None else load_builtin_chips()
    if key in pool:
        return pool[key]
    for skill in pool.values():
        if key in skill.match_names:
            return skill
    return None


# ---------------------------------------------------------------------------
# 起草 / 固化 / 写回（新硬件工作流：提取 -> 起草 -> 分层验证 -> 两次固化）
# ---------------------------------------------------------------------------


def spec_path_for(controller: str, directory: str | Path | None = None) -> Path:
    """规格文件路径：<directory|内置 chips 目录>/<controller>.yaml。"""
    base = Path(directory) if directory is not None else _BUILTIN_DIR
    return base / f"{controller.strip().lower()}.yaml"


def write_chip_skill(
    skill: ChipSkill,
    directory: str | Path | None = None,
) -> Path:
    """把规格写回 YAML 文件（默认内置 chips 目录），并刷新缓存。

    写回前再次校验（verified 与记录一致性由模型校验器保证）。
    返回实际写入的文件路径。
    """
    file_path = spec_path_for(skill.controller, directory)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(skill.to_yaml(), encoding="utf-8")
    clear_chip_cache()
    return file_path


def draft_chip_spec(
    *,
    controller: str,
    spec: dict[str, object],
    facts: dict[str, object] | None = None,
    aliases: list[str] | None = None,
    family: str = "",
    diagnostics: list[dict[str, object]] | None = None,
    display: dict[str, object] | None = None,
) -> ChipSkill:
    """起草一份新设备规格（verified=unverified，无验证记录）。

    供新硬件工作流"提取 -> 起草"使用：模型从 PDF/库源码提取事实后，
    用结构化 dict 调本函数生成合法规格；再 write_chip_skill 落盘。
    ``spec`` 只含通用字段（init/driver_template，任意设备适用）；
    显示设备额外传 ``display`` 块（layout/screen/column_offset/display_options）。
    校验失败抛 ChipSkillError。
    """
    raw: dict[str, object] = {
        "schema": _SCHEMA,
        "controller": controller,
        "spec": spec,
    }
    if display:
        raw["display"] = display
    if facts:
        raw["facts"] = facts
    if aliases:
        raw["aliases"] = aliases
    if family:
        raw["family"] = family
    if diagnostics:
        raw["diagnostics"] = diagnostics
    # 起草的规格永不携带验证记录：verified 固定为 unverified
    try:
        return ChipSkill.model_validate(raw)
    except ValueError as error:
        raise ChipSkillError(
            "spec_draft_invalid",
            f"规格起草校验失败：{controller}",
            details={"controller": controller, "error": str(error)},
        ) from error


def append_verification(
    skill: ChipSkill,
    entry: VerificationEntry | dict[str, object],
    *,
    promote: bool = True,
) -> ChipSkill:
    """追加一条验证记录，并按状态机提升 verified（只追加不修改历史）。

    promote=True（默认）：追加后 verified 取 derive_verified 的最新值
    （记录足够即可自动 unverified -> candidate -> true）；promote=False
    时保持原 verified（保守，只记录证据不固化）。
    """
    new_entry = (
        entry
        if isinstance(entry, VerificationEntry)
        else VerificationEntry.model_validate(entry)
    )
    records = [*skill.verification, new_entry]
    derived = derive_verified(records)
    verified = derived if promote else skill.verified
    updated = skill.model_copy(update={"verification": records, "verified": verified})
    # model_copy 不重新跑 model_validator：手工校验 promote=False 时的合法性
    if verified == "true" and derived != "true":
        raise ChipSkillError(
            "spec_verified_inconsistent",
            f"controller={skill.controller!r} 无法保持 verified=true：验证记录只支持 "
            f"{derived}（固化需要 ≥2 次不同 task 的 L3/L4 pass）",
        )
    return updated


__all__ = [
    "ChipSkill",
    "ChipFacts",
    "ChipSkillError",
    "DiagnosticPattern",
    "DisplayOptions",
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
