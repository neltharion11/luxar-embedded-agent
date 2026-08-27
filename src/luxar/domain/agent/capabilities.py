"""项目能力模型、源码能力提取和 preserve 不变量检查。"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from luxar.domain.repairs import ProjectFile


CapabilityStatus = Literal[
    "inferred",
    "implemented",
    "built",
    "verified",
    "degraded",
]


class ProjectCapability(BaseModel):
    """项目已经具备或被推断具备的一项能力。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    capability_id: str = Field(min_length=1, max_length=240)
    kind: str = Field(min_length=1, max_length=120)
    parameters: dict[str, object] = Field(default_factory=dict)
    status: CapabilityStatus = "inferred"
    owners: list[str] = Field(default_factory=list, max_length=40)
    evidence_ids: list[str] = Field(default_factory=list, max_length=80)
    source_paths: list[str] = Field(default_factory=list, max_length=80)
    preserve_by_default: bool = True
    # 来源和置信度是阶段 2 的事实溯源基础；不影响 legacy 模型。
    source_kind: Literal["user", "source", "document", "inference", "tool"] = "source"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


def gpio_output_capability_id(pin: int) -> str:
    """返回稳定、可读且可跨文件匹配的 GPIO 输出能力 ID。"""

    return f"gpio.output:P{pin}"


def _capability_ids(capabilities: Iterable[ProjectCapability]) -> set[str]:
    return {capability.capability_id for capability in capabilities}


class PreserveViolationError(ValueError):
    """补丁或代码任务删除了声明必须保留的能力。"""

    def __init__(self, capability_ids: Sequence[str]) -> None:
        self.capability_ids = tuple(capability_ids)
        joined = ", ".join(self.capability_ids)
        super().__init__(f"preserved capabilities were removed: {joined}")


def find_preserve_violations(
    preserves: Iterable[str],
    before: Iterable[ProjectCapability],
    after: Iterable[ProjectCapability],
) -> list[str]:
    """找出修改前存在、修改后消失且被声明 preserve 的能力。"""

    before_ids = _capability_ids(before)
    after_ids = _capability_ids(after)
    return sorted(
        capability_id
        for capability_id in set(preserves)
        if capability_id in before_ids and capability_id not in after_ids
    )


def assert_preserved(
    preserves: Iterable[str],
    before: Iterable[ProjectCapability],
    after: Iterable[ProjectCapability],
) -> None:
    violations = find_preserve_violations(preserves, before, after)
    if violations:
        raise PreserveViolationError(violations)


_GPIO_PIN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:GPIO(?:_NUM_)?\s*|P\s*)(\d+)",
    re.IGNORECASE,
)
_GPIO_LEVEL_RE = re.compile(
    r"(?P<high>高电平|置高|拉高|输出高|\bHIGH\b|\bON\b|\bTRUE\b)"
    r"|(?P<low>低电平|置低|拉低|输出低|\bLOW\b|\bOFF\b|\bFALSE\b)",
    re.IGNORECASE,
)
_TASK_CREATE_RE = re.compile(
    r"xTaskCreate(?:PinnedToCore)?\s*\(\s*([A-Za-z_]\w*)",
    re.IGNORECASE,
)
_SOURCE_FEATURE_PATTERNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("bus.i2c", "bus.i2c", ("i2c_master", "i2c_param_config", "I2C_NUM_")),
    ("bus.spi", "bus.spi", ("spi_bus_initialize", "spi_device_", "SPI2_HOST", "SPI3_HOST")),
    ("bus.uart", "bus.uart", ("uart_driver_install", "uart_param_config", "UART_NUM_")),
    ("bus.twai", "bus.twai", ("twai_driver_install", "twai_general_config", "CAN")),
    ("network.wifi", "network.wifi", ("esp_wifi_init", "esp_wifi_start", "WIFI_MODE_")),
    ("network.ble", "network.ble", ("esp_ble", "nimble_port", "BLE_GATT")),
    ("network.mqtt_client", "network.mqtt_client", ("mqtt_client", "esp_mqtt", "MQTT_EVENT_")),
    ("network.http", "network.http_client", ("esp_http_client", "httpd_start", "HTTPD_")),
    ("protocol.modbus_rtu", "protocol.modbus_rtu", ("modbus", "MODBUS_RTU", "rs485")),
    ("storage.nvs_config", "storage.nvs_config", ("nvs_open", "nvs_set", "nvs_get")),
    ("system.ota", "system.ota", ("esp_ota", "ota_begin", "OTA_")),
    ("sync.queue", "sync.queue", ("xQueueCreate", "xQueueSend", "xQueueReceive")),
    ("sync.event_group", "sync.event_group", ("xEventGroupCreate", "xEventGroupSetBits")),
    ("sync.mutex", "sync.mutex", ("xSemaphoreCreateMutex", "xSemaphoreTake")),
)


def _source_owner(path: str) -> str:
    parts = PurePosixPath(path).parts
    if len(parts) >= 3 and parts[0] == "components":
        return parts[1]
    if parts and parts[0] == "main":
        return "main"
    return parts[0] if parts else "project"


class ProjectCapabilityExtractor:
    """无需模型即可从受限源码快照提取可保留的工程能力。

    该提取器不试图替代完整 C 语言解析器。它只识别稳定的 ESP-IDF API
    和 FreeRTOS 创建调用，把 GPIO、总线、协议、存储和任务转成带证据的
    能力事实。事务式变更校验复用同一提取器，因此 preserve 不再只保护 GPIO。
    """

    def extract(self, files: Sequence[ProjectFile]) -> list[ProjectCapability]:
        records: dict[str, dict[str, object]] = {}
        for project_file in files:
            content = project_file.content
            pins = {
                int(match.group(1))
                for match in _GPIO_PIN_RE.finditer(content)
            }
            if not pins:
                continue

            is_output = bool(
                re.search(r"GPIO_MODE_OUTPUT|gpio_config", content, re.IGNORECASE)
            )
            for pin in sorted(pins):
                # 只把明确配置为输出的 GPIO 建立为 gpio.output 能力，
                # 避免把注释、输入或总线引脚误报成输出。
                if not is_output:
                    continue
                capability_id = gpio_output_capability_id(pin)
                record = records.setdefault(
                    capability_id,
                    {
                        "capability_id": capability_id,
                        "kind": "gpio.output",
                        "parameters": {"pin": pin, "mode": "output"},
                        "status": "inferred",
                        "owners": [project_file.path.split("/")[0]],
                        "evidence_ids": [],
                        "source_paths": [],
                        "preserve_by_default": True,
                        "source_kind": "source",
                        "confidence": 1.0,
                    },
                )
                source_paths = record["source_paths"]
                assert isinstance(source_paths, list)
                if project_file.path not in source_paths:
                    source_paths.append(project_file.path)
                evidence_ids = record["evidence_ids"]
                assert isinstance(evidence_ids, list)
                evidence_id = f"source:{project_file.path}:gpio{pin}"
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)

                level = self._level_for_pin(content, pin)
                if level is not None:
                    parameters = record["parameters"]
                    assert isinstance(parameters, dict)
                    parameters["level"] = level

        for capability_id, kind, patterns in _SOURCE_FEATURE_PATTERNS:
            matched_paths = [
                project_file.path
                for project_file in files
                if any(
                    pattern.casefold() in project_file.content.casefold()
                    for pattern in patterns
                )
            ]
            if not matched_paths:
                continue
            records[capability_id] = {
                "capability_id": capability_id,
                "kind": kind,
                "parameters": {"matched_patterns": list(patterns)},
                "status": "inferred",
                "owners": sorted({_source_owner(path) for path in matched_paths}),
                "evidence_ids": [
                    f"source:{path}" for path in sorted(set(matched_paths))
                ],
                "source_paths": sorted(set(matched_paths)),
                "preserve_by_default": True,
                "source_kind": "source",
                "confidence": 0.95,
            }

        task_paths: dict[str, set[str]] = {}
        for project_file in files:
            for match in _TASK_CREATE_RE.finditer(project_file.content):
                task_paths.setdefault(match.group(1), set()).add(project_file.path)
        for task_name, paths in sorted(task_paths.items()):
            capability_id = f"task.freertos:{task_name}"
            records[capability_id] = {
                "capability_id": capability_id,
                "kind": "task.freertos",
                "parameters": {"entry_function": task_name},
                "status": "inferred",
                "owners": sorted({_source_owner(path) for path in paths}),
                "evidence_ids": [
                    f"source:{path}:task:{task_name}" for path in sorted(paths)
                ],
                "source_paths": sorted(paths),
                "preserve_by_default": True,
                "source_kind": "source",
                "confidence": 0.95,
            }

        return [
            ProjectCapability.model_validate(records[key])
            for key in sorted(records)
        ]

    @staticmethod
    def _level_for_pin(content: str, pin: int) -> int | None:
        pin_matches = list(_GPIO_PIN_RE.finditer(content))
        for index, match in enumerate(pin_matches):
            if int(match.group(1)) != pin:
                continue
            end = (
                pin_matches[index + 1].start()
                if index + 1 < len(pin_matches)
                else min(len(content), match.end() + 180)
            )
            fragment = content[match.end() : end]
            level_match = _GPIO_LEVEL_RE.search(fragment)
            if level_match is None:
                # C API 常见形式为 gpio_set_level(GPIO_NUM_13, 1)。
                level_call = re.search(
                    rf"gpio_set_level\s*\(\s*GPIO_NUM_{pin}\s*,\s*([01])\s*\)",
                    content,
                    re.IGNORECASE,
                )
                return int(level_call.group(1)) if level_call else None
            return 1 if level_match.group("high") else 0
        return None
