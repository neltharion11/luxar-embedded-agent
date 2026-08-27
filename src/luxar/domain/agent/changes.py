"""项目变更集与第一版确定性目标解释器。"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from luxar.domain.agent.capabilities import (
    ProjectCapability,
    gpio_output_capability_id,
)
from luxar.domain.agent.objectives import ProjectObjective


class CapabilityChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation: Literal["add", "modify", "remove", "replace", "preserve", "verify"]
    capability_id: str = Field(min_length=1, max_length=240)
    desired_state: dict[str, object] = Field(default_factory=dict)
    replaces: list[str] = Field(default_factory=list, max_length=20)
    preserve: list[str] = Field(default_factory=list, max_length=40)
    rationale: str = Field(default="", max_length=1000)


class ChangeSet(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    changes: list[CapabilityChange] = Field(default_factory=list, max_length=100)
    assumptions: list[str] = Field(default_factory=list, max_length=40)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_change_ids(self) -> "ChangeSet":
        # 同一能力可同时出现在 replace 的补充信息中，但不能重复声明同一操作。
        operations = [(change.operation, change.capability_id) for change in self.changes]
        if len(operations) != len(set(operations)):
            raise ValueError("changes cannot repeat the same operation and capability")
        return self


InteractionIntent = Literal[
    "change_objective",
    "ask_question",
    "continue",
    "inspect_status",
    "revise_plan",
]


class ObjectiveInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    intent: InteractionIntent
    objective: ProjectObjective | None = None
    change_set: ChangeSet | None = None
    allowed_paths_by_capability: dict[str, list[str]] = Field(
        default_factory=dict,
        max_length=100,
    )
    questions: list[str] = Field(default_factory=list, max_length=8)
    objective_changed: bool = False


_PIN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:GPIO(?:_NUM_)?\s*|P\s*)(\d+)",
    re.IGNORECASE,
)
_HIGH_WORDS = ("高电平", "置高", "拉高", "输出高", "high", "on")
_LOW_WORDS = ("低电平", "置低", "拉低", "输出低", "low", "off")
_QUESTION_WORDS = ("什么", "如何", "怎么", "为什么", "是否", "能否", "?", "？")


def _pin_from_capability(capability: ProjectCapability) -> int | None:
    pin = capability.parameters.get("pin")
    if isinstance(pin, int):
        return pin
    match = re.search(r"(?:P|GPIO)(\d+)$", capability.capability_id, re.IGNORECASE)
    return int(match.group(1)) if match else None


class ObjectiveInterpreter:
    """把常见 GPIO 变更语义转换为可审计 ChangeSet。

    真实部署可由模型 Adapter 实现同一合同；该确定性版本用于第一批闭环、
    t2 回归和模型不可用时的安全降级。它宁可产生澄清问题，也不擅自替换能力。
    """

    def interpret(
        self,
        message: str,
        *,
        existing_capabilities: Sequence[ProjectCapability] = (),
        current_objective: ProjectObjective | None = None,
        source_message_id: str = "user",
    ) -> ObjectiveInterpretation:
        text = message.strip()
        if self._is_question(text):
            return ObjectiveInterpretation(
                intent="ask_question",
                objective=current_objective,
                change_set=None,
                questions=[text],
                objective_changed=False,
            )

        pins = self._extract_pins(text)
        if not pins:
            if self._looks_like_status(text):
                return ObjectiveInterpretation(
                    intent="inspect_status",
                    objective=current_objective,
                    objective_changed=False,
                )
            objective = self._objective_for(text, current_objective, source_message_id)
            change_set = ChangeSet(
                assumptions=["未识别到具体硬件能力，保留自然语言目标供后续规划"],
            )
            return ObjectiveInterpretation(
                intent="change_objective",
                objective=objective,
                change_set=change_set,
                objective_changed=True,
            )

        existing_by_pin = {
            pin: capability
            for capability in existing_capabilities
            if (pin := _pin_from_capability(capability)) is not None
        }
        operation = self._operation(text)
        changes: list[CapabilityChange] = []
        unresolved: list[str] = []
        changed_pins: set[int] = set()

        for pin, level in pins:
            capability_id = gpio_output_capability_id(pin)
            desired_state: dict[str, object] = {"pin": pin, "mode": "output"}
            if level is not None:
                desired_state["level"] = level

            pin_operation = self._operation_for_pin(text, pin) or operation
            if pin_operation is None:
                if pin in existing_by_pin:
                    pin_operation = "modify"
                elif existing_by_pin:
                    unresolved.append(
                        f"请确认 P{pin} 是新增输出，还是替换现有 GPIO 输出能力"
                    )
                    continue
                else:
                    pin_operation = "add"

            if pin_operation in {"modify", "replace", "remove", "preserve", "verify"}:
                if pin_operation == "modify" and pin not in existing_by_pin:
                    unresolved.append(
                        f"当前工程未发现 P{pin}，无法直接修改；请确认是否新增"
                    )
                    continue
                if pin_operation == "remove":
                    desired_state = {}
                changes.append(
                    CapabilityChange(
                        operation=pin_operation,
                        capability_id=capability_id,
                        desired_state=desired_state,
                        rationale=f"用户消息明确要求 {pin_operation} P{pin}",
                    )
                )
            else:
                changes.append(
                    CapabilityChange(
                        operation="add",
                        capability_id=capability_id,
                        desired_state=desired_state,
                        rationale=f"用户消息明确要求新增 P{pin} 输出能力",
                    )
                )
            changed_pins.add(pin)

        # 新增/修改/替换默认不删除未涉及能力，显式写出 preserve 让后续补丁校验可用。
        for capability in existing_capabilities:
            pin = _pin_from_capability(capability)
            if pin is None or pin in changed_pins:
                continue
            if any(
                change.operation == "preserve"
                and change.capability_id == capability.capability_id
                for change in changes
            ):
                continue
            changes.append(
                CapabilityChange(
                    operation="preserve",
                    capability_id=capability.capability_id,
                    desired_state=dict(capability.parameters),
                    rationale="默认保护未被本轮目标修改的既有能力",
                )
            )

        objective = self._objective_for(text, current_objective, source_message_id)
        change_set = ChangeSet(
            changes=changes,
            assumptions=["未明确要求删除的既有能力默认保留"],
            unresolved_questions=unresolved,
        )
        return ObjectiveInterpretation(
            intent="change_objective",
            objective=objective,
            change_set=change_set,
            questions=unresolved,
            objective_changed=not bool(unresolved),
        )

    @staticmethod
    def _is_question(text: str) -> bool:
        return any(word in text for word in _QUESTION_WORDS) and not any(
            marker in text for marker in ("新增", "修改", "删除", "替换", "设置", "实现")
        )

    @staticmethod
    def _looks_like_status(text: str) -> bool:
        stripped = text.strip()
        return stripped == "状态" or any(
            phrase in stripped
            for phrase in (
                "当前状态",
                "项目状态",
                "任务状态",
                "查看状态",
                "进度",
                "现在做到",
                "当前任务",
            )
        )

    @staticmethod
    def _extract_pins(text: str) -> list[tuple[int, int | None]]:
        matches = list(_PIN_RE.finditer(text))
        results: list[tuple[int, int | None]] = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            fragment = text[match.end() : end].lower()
            level: int | None = None
            if any(word.lower() in fragment for word in _HIGH_WORDS):
                level = 1
            elif any(word.lower() in fragment for word in _LOW_WORDS):
                level = 0
            results.append((int(match.group(1)), level))
        return results

    @staticmethod
    def _operation(text: str) -> Literal["add", "modify", "remove", "replace", "preserve", "verify"] | None:
        if any(word in text.lower() for word in ("新增", "添加", "增加", "add")):
            return "add"
        if any(word in text.lower() for word in ("删除", "移除", "去掉", "remove")):
            return "remove"
        if any(word in text.lower() for word in ("替换", "replace")):
            return "replace"
        if any(word in text.lower() for word in ("保留", "preserve")):
            return "preserve"
        if any(word in text.lower() for word in ("修改", "改为", "改成", "设置", "set")):
            return "modify"
        return None

    @classmethod
    def _operation_for_pin(
        cls,
        text: str,
        pin: int,
    ) -> Literal["add", "modify", "remove", "replace", "preserve", "verify"] | None:
        """读取某个 pin 附近的动词，支持“删除 P13，但保留 P33”。"""

        matches = list(_PIN_RE.finditer(text))
        for index, match in enumerate(matches):
            if int(match.group(1)) != pin:
                continue
            start = (
                matches[index - 1].end()
                if index > 0
                else max(0, match.start() - 16)
            )
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            local = text[start:end]
            operation = cls._operation(local)
            if operation is not None:
                return operation
        return None

    @staticmethod
    def _objective_for(
        text: str,
        current: ProjectObjective | None,
        source_message_id: str,
    ) -> ProjectObjective:
        if current is None:
            return ProjectObjective(
                objective_id=f"objective-{uuid.uuid4().hex[:12]}",
                title=text[:120],
                description=text,
                source_message_ids=[source_message_id],
                revision=1,
            )
        source_ids = list(current.source_message_ids)
        if source_message_id not in source_ids:
            source_ids.append(source_message_id)
        return current.model_copy(
            update={
                "description": f"{current.description}\n{text}",
                "source_message_ids": source_ids,
                "revision": current.revision + 1,
                "status": "active",
            }
        )
