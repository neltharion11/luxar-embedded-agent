"""ESP32 硬件与协议能力包的确定性合同和基础规则。"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from luxar.domain.agent.capabilities import ProjectCapabilityExtractor
from luxar.domain.repairs import ProjectFile


HardwareSeverity = Literal["info", "warning", "blocking"]
HardwareProtocol = Literal[
    "gpio",
    "i2c",
    "spi",
    "uart",
    "twai",
    "adc",
    "pwm",
    "wifi",
    "ble",
    "mqtt",
    "http",
    "modbus",
]


class ChipProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    chip_id: str = Field(min_length=1, max_length=120)
    family: str = Field(min_length=1, max_length=120)
    input_only_pins: list[int] = Field(default_factory=list, max_length=80)
    reserved_pins: list[int] = Field(default_factory=list, max_length=80)
    console_uart: int | None = Field(default=0, ge=0, le=5)
    preferred_spi_cs_pins: list[int] = Field(default_factory=list, max_length=40)
    idf_major: int | None = Field(default=None, ge=1, le=20)


class BoardProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    board_id: str = Field(min_length=1, max_length=160)
    chip: ChipProfile
    aliases: list[str] = Field(default_factory=list, max_length=40)
    module_ids: list[str] = Field(default_factory=list, max_length=40)
    source_kind: Literal["builtin", "user", "document"] = "builtin"
    evidence_ids: list[str] = Field(default_factory=list, max_length=80)


class ModuleProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    module_id: str = Field(min_length=1, max_length=160)
    board_ids: list[str] = Field(default_factory=list, max_length=40)
    interfaces: list[HardwareProtocol] = Field(default_factory=list, max_length=40)
    constraints: list[str] = Field(default_factory=list, max_length=80)


class DeviceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    device_id: str = Field(min_length=1, max_length=200)
    protocol: HardwareProtocol
    bus_id: str | None = None
    parameters: dict[str, object] = Field(default_factory=dict)
    source_paths: list[str] = Field(default_factory=list, max_length=80)
    evidence_ids: list[str] = Field(default_factory=list, max_length=80)


class ProtocolPackage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    protocol_id: HardwareProtocol
    package_id: str = Field(min_length=1, max_length=160)
    idf_api_symbols: list[str] = Field(default_factory=list, max_length=80)
    version_constraint: str | None = Field(default=None, max_length=120)
    evidence_ids: list[str] = Field(default_factory=list, max_length=80)


class HardwareValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    issue_id: str = Field(min_length=1, max_length=240)
    rule_id: str = Field(min_length=1, max_length=160)
    severity: HardwareSeverity
    message: str = Field(min_length=1, max_length=1000)
    resource_ids: list[str] = Field(default_factory=list, max_length=40)
    evidence_ids: list[str] = Field(default_factory=list, max_length=80)
    alternatives: list[str] = Field(default_factory=list, max_length=40)


class HardwareAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    assignment_id: str = Field(min_length=1, max_length=240)
    resource_kind: Literal["spi_cs", "gpio"]
    requested: int | None = None
    assigned: int = Field(ge=0, le=99)
    reason: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=80)


class HardwareValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    chip: ChipProfile
    devices: list[DeviceSpec] = Field(default_factory=list, max_length=500)
    protocols: list[ProtocolPackage] = Field(default_factory=list, max_length=100)
    issues: list[HardwareValidationIssue] = Field(default_factory=list, max_length=300)
    assignments: list[HardwareAssignment] = Field(default_factory=list, max_length=200)

    @property
    def blocking_issues(self) -> list[HardwareValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "blocking"]

    @property
    def has_blocking_issue(self) -> bool:
        return bool(self.blocking_issues)


_GPIO_RE = re.compile(r"GPIO_NUM_(?P<pin>\d+)", re.IGNORECASE)
_I2C_BUS_RE = re.compile(r"I2C_NUM_(?P<bus>\d+)", re.IGNORECASE)
_I2C_ADDRESS_RE = re.compile(
    r"(?:device_address|dev_addr|i2c_addr|address)\s*[=:]\s*"
    r"(?P<address>0x[0-9a-f]+|\d+)",
    re.IGNORECASE,
)
_SPI_CS_RE = re.compile(
    r"spics_io_num\s*[=:]\s*(?:GPIO_NUM_)?(?P<pin>\d+)",
    re.IGNORECASE,
)
_SPI_DEVICE_RE = re.compile(r"spi_bus_add_device\s*\(", re.IGNORECASE)
_UART_RE = re.compile(r"UART_NUM_(?P<uart>\d+)", re.IGNORECASE)
_ADC_RE = re.compile(r"\badc[_\w]*|ADC_UNIT_", re.IGNORECASE)
_PWM_RE = re.compile(r"ledc_(?:set_pin|channel_config)|mcpwm_gpio_init", re.IGNORECASE)


ESP32_PROFILE = BoardProfile(
    board_id="esp32-generic",
    aliases=["esp32", "esp32-wroom", "esp32-devkit"],
    chip=ChipProfile(
        chip_id="esp32",
        family="esp32",
        input_only_pins=[34, 35, 36, 37, 38, 39],
        reserved_pins=[],
        console_uart=0,
        preferred_spi_cs_pins=[25, 26, 27, 32, 33],
    ),
)


class HardwareRuleEngine:
    """对受控源码快照执行最小 ESP32 规则集。"""

    def __init__(self, profiles: Sequence[BoardProfile] = (ESP32_PROFILE,)) -> None:
        self._profiles = tuple(profiles)
        self._capability_extractor = ProjectCapabilityExtractor()

    def profile_for(self, target_chip: str | None) -> BoardProfile:
        normalized = (target_chip or "esp32").casefold()
        for profile in self._profiles:
            if normalized == profile.board_id.casefold() or normalized in {
                alias.casefold() for alias in profile.aliases
            }:
                return profile
            if normalized == profile.chip.chip_id.casefold():
                return profile
        return self._profiles[0]

    def inspect(
        self,
        files: Sequence[ProjectFile],
        *,
        target_chip: str | None = None,
    ) -> HardwareValidationReport:
        normalized = [
            item if isinstance(item, ProjectFile) else ProjectFile.model_validate(item)
            for item in files
        ]
        profile = self.profile_for(target_chip)
        issues: list[HardwareValidationIssue] = []
        devices: list[DeviceSpec] = []
        assignments: list[HardwareAssignment] = []
        protocols: dict[str, ProtocolPackage] = {}
        console_paths = [
            item.path for item in normalized if self._uses_console(item.content)
        ]
        used_pins = {
            int(match.group("pin"))
            for item in normalized
            for match in _GPIO_RE.finditer(item.content)
        }

        def add_protocol(
            protocol_id: HardwareProtocol,
            package_id: str,
            symbols: Sequence[str],
            paths: Sequence[str],
        ) -> None:
            protocols[protocol_id] = ProtocolPackage(
                protocol_id=protocol_id,
                package_id=package_id,
                idf_api_symbols=list(symbols),
                evidence_ids=[f"source:{path}" for path in sorted(set(paths))],
            )

        capabilities = self._capability_extractor.extract(normalized)
        for capability in capabilities:
            if capability.kind != "gpio.output":
                continue
            pin = capability.parameters.get("pin")
            if not isinstance(pin, int) or pin not in profile.chip.input_only_pins:
                continue
            alternatives = self._gpio_alternatives(profile, used_pins)
            issues.append(
                HardwareValidationIssue(
                    issue_id=f"gpio-output-input-only:P{pin}",
                    rule_id="esp32.gpio.output_not_input_only",
                    severity="blocking",
                    message=f"GPIO{pin} 是 ESP32 仅输入引脚，不能配置为输出",
                    resource_ids=[f"gpio:P{pin}"],
                    evidence_ids=capability.evidence_ids,
                    alternatives=[f"改用 GPIO{value}" for value in alternatives],
                )
            )

        for item in normalized:
            content = item.content
            paths = [item.path]
            buses = sorted({int(match.group("bus")) for match in _I2C_BUS_RE.finditer(content)})
            addresses = [
                int(match.group("address"), 0)
                for match in _I2C_ADDRESS_RE.finditer(content)
            ]
            if buses and addresses:
                bus_id = f"i2c:{buses[0]}"
                for index, address in enumerate(addresses):
                    devices.append(
                        DeviceSpec(
                            device_id=f"i2c:{buses[0]}:{address:02x}:{item.path}:{index}",
                            protocol="i2c",
                            bus_id=bus_id,
                            parameters={"address": address},
                            source_paths=paths,
                            evidence_ids=[f"source:{item.path}"],
                        )
                    )
                add_protocol("i2c", "esp-idf.driver.i2c", ["i2c_master_bus_add_device"], paths)

            cs_pins = [int(match.group("pin")) for match in _SPI_CS_RE.finditer(content)]
            spi_devices = max(len(cs_pins), len(_SPI_DEVICE_RE.findall(content)))
            if spi_devices:
                add_protocol("spi", "esp-idf.driver.spi", ["spi_bus_add_device"], paths)
                for index in range(spi_devices):
                    requested = cs_pins[index] if index < len(cs_pins) else None
                    assigned = requested
                    reason = "沿用源码声明的独立 CS"
                    if requested is None or cs_pins[:index].count(requested) > 0:
                        candidate = self._next_spi_cs(profile, used_pins)
                        if candidate is not None:
                            assigned = candidate
                            reason = (
                                "CS 缺失，自动规划独立片选"
                                if requested is None
                                else "CS 重复，自动规划替代片选"
                            )
                            used_pins.add(candidate)
                    if assigned is None:
                        issues.append(
                            HardwareValidationIssue(
                                issue_id=f"spi-cs-unassigned:{item.path}:{index}",
                                rule_id="esp32.spi.independent_cs",
                                severity="blocking",
                                message="SPI 设备无法获得独立 CS 引脚",
                                resource_ids=[f"spi:cs:{item.path}:{index}"],
                                evidence_ids=[f"source:{item.path}"],
                                alternatives=[
                                    f"释放 SPI CS 引脚或指定其他 GPIO"
                                ],
                            )
                        )
                    elif requested != assigned:
                        assignments.append(
                            HardwareAssignment(
                                assignment_id=f"spi-cs:{item.path}:{index}",
                                resource_kind="spi_cs",
                                requested=requested,
                                assigned=assigned,
                                reason=reason,
                                evidence_ids=[f"source:{item.path}"],
                            )
                        )
                if len(cs_pins) != len(set(cs_pins)):
                    duplicate_pins = sorted(
                        pin for pin in set(cs_pins) if cs_pins.count(pin) > 1
                    )
                    issues.append(
                        HardwareValidationIssue(
                            issue_id=f"spi-cs-conflict:{item.path}",
                            rule_id="esp32.spi.independent_cs",
                            severity="warning",
                            message=(
                                f"SPI 源码声明了重复 CS：{', '.join(map(str, duplicate_pins))}；"
                                "已生成替代片选规划"
                            ),
                            resource_ids=[f"gpio:P{pin}" for pin in duplicate_pins],
                            evidence_ids=[f"source:{item.path}"],
                            alternatives=["采用 assignments 中的独立 CS"],
                        )
                    )

            uarts = sorted({int(match.group("uart")) for match in _UART_RE.finditer(content)})
            if uarts:
                add_protocol("uart", "esp-idf.driver.uart", ["uart_driver_install"], paths)
                for uart in uarts:
                    devices.append(
                        DeviceSpec(
                            device_id=f"uart:{uart}:{item.path}",
                            protocol="uart",
                            bus_id=f"uart:{uart}",
                            parameters={"uart": uart},
                            source_paths=paths,
                            evidence_ids=[f"source:{item.path}"],
                        )
                    )
                    if uart == profile.chip.console_uart and console_paths:
                        issues.append(
                            HardwareValidationIssue(
                                issue_id=f"uart-console-conflict:{item.path}:{uart}",
                                rule_id="esp32.uart.console_exclusive",
                                severity="blocking",
                                message=f"UART{uart} 同时被应用串口和控制台占用",
                                resource_ids=[f"uart:{uart}"],
                                evidence_ids=[
                                    f"source:{path}"
                                    for path in sorted({item.path, *console_paths})
                                ],
                                alternatives=[
                                    "将应用串口迁移到 UART1/UART2",
                                    "显式关闭或迁移 ESP-IDF 控制台",
                                ],
                            )
                        )

            if _ADC_RE.search(content):
                add_protocol("adc", "esp-idf.driver.adc", ["adc_oneshot_new_unit"], paths)
            if _PWM_RE.search(content):
                add_protocol("pwm", "esp-idf.driver.ledc", ["ledc_set_pin"], paths)
                for match in _GPIO_RE.finditer(content):
                    pin = int(match.group("pin"))
                    if pin in profile.chip.input_only_pins:
                        issues.append(
                            HardwareValidationIssue(
                                issue_id=f"pwm-output-input-only:{item.path}:{pin}",
                                rule_id="esp32.pwm.output_not_input_only",
                                severity="blocking",
                                message=f"PWM 不能输出到 ESP32 GPIO{pin}",
                                resource_ids=[f"gpio:P{pin}"],
                                evidence_ids=[f"source:{item.path}"],
                                alternatives=[
                                    f"改用 GPIO{value}"
                                    for value in self._gpio_alternatives(profile, used_pins)
                                ],
                            )
                        )
            if "twai_driver_install" in content or "twai_general_config" in content:
                add_protocol("twai", "esp-idf.driver.twai", ["twai_driver_install"], paths)
            if re.search(r"esp_wifi_|WIFI_MODE_", content, re.IGNORECASE):
                add_protocol("wifi", "esp-idf.wifi", ["esp_wifi_init"], paths)
            if re.search(r"esp_ble|nimble_port|BLE_GATT", content, re.IGNORECASE):
                add_protocol("ble", "esp-idf.ble", ["nimble_port_init"], paths)
            if re.search(r"esp_mqtt|mqtt_client|MQTT_EVENT_", content, re.IGNORECASE):
                add_protocol("mqtt", "esp-idf.mqtt", ["esp_mqtt_client_init"], paths)
            if re.search(r"esp_http_client|httpd_start|HTTPD_", content, re.IGNORECASE):
                add_protocol("http", "esp-idf.http", ["esp_http_client_init"], paths)
            if re.search(r"modbus|MODBUS_RTU|rs485", content, re.IGNORECASE):
                add_protocol("modbus", "esp-idf.modbus", ["mbc_master_init"], paths)

        i2c_groups: dict[tuple[str, int], list[DeviceSpec]] = defaultdict(list)
        for device in devices:
            if device.protocol == "i2c" and device.bus_id is not None:
                address = device.parameters.get("address")
                if isinstance(address, int):
                    i2c_groups[(device.bus_id, address)].append(device)
        for (bus_id, address), grouped in sorted(i2c_groups.items()):
            if len(grouped) < 2:
                continue
            evidence = [evidence_id for device in grouped for evidence_id in device.evidence_ids]
            issues.append(
                HardwareValidationIssue(
                    issue_id=f"i2c-address-conflict:{bus_id}:{address:02x}",
                    rule_id="esp32.i2c.address_unique_per_bus",
                    severity="blocking",
                    message=f"{bus_id} 上多个设备使用相同 I2C 地址 0x{address:02X}",
                    resource_ids=[f"{bus_id}:0x{address:02x}"],
                    evidence_ids=sorted(set(evidence)),
                    alternatives=["修改设备地址", "使用 I2C 多路复用器", "迁移到其他 I2C 总线"],
                )
            )

        return HardwareValidationReport(
            chip=profile.chip,
            devices=devices,
            protocols=[protocols[key] for key in sorted(protocols)],
            issues=sorted(issues, key=lambda issue: issue.issue_id),
            assignments=sorted(assignments, key=lambda assignment: assignment.assignment_id),
        )

    @staticmethod
    def _uses_console(content: str) -> bool:
        return bool(
            re.search(
                r"esp_console|console_init|ESP_CONSOLE_UART|esp_console_new_repl_uart",
                content,
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _gpio_alternatives(profile: BoardProfile, used_pins: set[int]) -> list[int]:
        return [
            pin
            for pin in profile.chip.preferred_spi_cs_pins
            if pin not in used_pins
            and pin not in profile.chip.reserved_pins
        ][:3]

    @staticmethod
    def _next_spi_cs(profile: BoardProfile, used_pins: set[int]) -> int | None:
        for pin in profile.chip.preferred_spi_cs_pins:
            if pin not in used_pins and pin not in profile.chip.reserved_pins:
                return pin
        return None


__all__ = [
    "BoardProfile",
    "ChipProfile",
    "DeviceSpec",
    "ESP32_PROFILE",
    "HardwareAssignment",
    "HardwareRuleEngine",
    "HardwareValidationIssue",
    "HardwareValidationReport",
    "ModuleProfile",
    "ProtocolPackage",
]
