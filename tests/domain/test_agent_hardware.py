from __future__ import annotations

from luxar.domain.agent.hardware import HardwareRuleEngine
from luxar.domain.agent.project_inspector import ProjectModelExtractor
from luxar.domain.repairs import ProjectFile


def test_esp32_gpio34_output_is_blocking_with_alternatives() -> None:
    report = HardwareRuleEngine().inspect(
        [
            ProjectFile(
                path="main/main.c",
                content=(
                    "gpio_config_t config = {0};\n"
                    "config.pin_bit_mask = 1ULL << GPIO_NUM_34;\n"
                    "config.mode = GPIO_MODE_OUTPUT;\n"
                ),
            )
        ]
    )

    issue = next(issue for issue in report.issues if issue.rule_id.startswith("esp32.gpio"))
    assert issue.severity == "blocking"
    assert issue.resource_ids == ["gpio:P34"]
    assert issue.alternatives == ["改用 GPIO25", "改用 GPIO26", "改用 GPIO27"]


def test_i2c_devices_share_bus_when_addresses_differ() -> None:
    content = (
        "I2C_NUM_0;\n"
        "i2c_device_config_t display = {.device_address = 0x3C};\n"
        "i2c_device_config_t sensor = {.device_address = 0x40};\n"
    )
    report = HardwareRuleEngine().inspect([ProjectFile(path="main/i2c.c", content=content)])

    assert [device.parameters["address"] for device in report.devices] == [0x3C, 0x40]
    assert not any(issue.rule_id == "esp32.i2c.address_unique_per_bus" for issue in report.issues)


def test_i2c_same_address_is_blocking_on_one_bus() -> None:
    content = (
        "I2C_NUM_0;\n"
        "i2c_device_config_t display = {.device_address = 0x3C};\n"
        "i2c_device_config_t sensor = {.device_address = 0x3C};\n"
    )
    report = HardwareRuleEngine().inspect([ProjectFile(path="main/i2c.c", content=content)])

    issue = next(issue for issue in report.issues if "i2c.address" in issue.rule_id)
    assert issue.severity == "blocking"
    assert "0x3C" in issue.message
    assert "修改设备地址" in issue.alternatives


def test_spi_duplicate_cs_gets_independent_assignment() -> None:
    content = (
        "spi_bus_add_device(SPI2_HOST, &display, &display_handle);\n"
        "display_cfg.spics_io_num = GPIO_NUM_5;\n"
        "spi_bus_add_device(SPI2_HOST, &flash, &flash_handle);\n"
        "flash_cfg.spics_io_num = GPIO_NUM_5;\n"
    )
    report = HardwareRuleEngine().inspect([ProjectFile(path="main/spi.c", content=content)])

    assert len(report.assignments) == 1
    assert report.assignments[0].requested == 5
    assert report.assignments[0].assigned == 25
    assert any(issue.rule_id == "esp32.spi.independent_cs" for issue in report.issues)


def test_uart0_console_conflict_is_blocking() -> None:
    content = (
        "uart_driver_install(UART_NUM_0, 2048, 0, 0, NULL, 0);\n"
        "esp_console_init();\n"
    )
    report = HardwareRuleEngine().inspect([ProjectFile(path="main/uart.c", content=content)])

    issue = next(issue for issue in report.issues if "uart.console" in issue.rule_id)
    assert issue.severity == "blocking"
    assert issue.resource_ids == ["uart:0"]


def test_project_model_carries_hardware_report_and_warning() -> None:
    model = ProjectModelExtractor().extract(
        [
            ProjectFile(
                path="main/main.c",
                content=(
                    "gpio_config_t config = {0};\n"
                    "config.pin_bit_mask = 1ULL << GPIO_NUM_34;\n"
                    "config.mode = GPIO_MODE_OUTPUT;\n"
                ),
            )
        ]
    )

    assert model.hardware_report.has_blocking_issue is True
    assert "硬件规则" in "；".join(model.warnings)
