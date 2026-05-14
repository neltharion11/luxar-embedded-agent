from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from luxar.core.driver_artifacts import copy_driver_artifacts
from luxar.models.schemas import BuildManifest
from luxar.models.schemas import DriverMetadata
from luxar.models.schemas import PeripheralCapability
from luxar.models.schemas import ProjectConfig
from luxar.models.schemas import ProjectPlan


APP_MAIN_H = """#ifndef APP_MAIN_H
#define APP_MAIN_H

void app_main_init(void);
void app_main_loop(void);

#endif /* APP_MAIN_H */
"""


APP_MAIN_C = """#include "app_main.h"

void app_main_init(void)
{
    /* TODO(stage-2): add user initialization code. */
}

void app_main_loop(void)
{
    /* TODO(stage-2): add user loop code. */
}
"""


CMAKELISTS_TXT = """cmake_minimum_required(VERSION 3.20)
project(luxar_app C)

file(GLOB APP_SOURCES CONFIGURE_DEPENDS
    "App/Src/*.c"
)

set(APP_DRIVER_SOURCES
@APP_DRIVER_SOURCES@
)

add_library(app STATIC ${APP_SOURCES})
target_sources(app PRIVATE ${APP_DRIVER_SOURCES})

target_include_directories(app PUBLIC
    App/Inc
@APP_DRIVER_INCLUDE_DIRS@
)
"""


FIRMWARE_MAIN_C = """#include "stm32f1xx_hal.h"
#include "app_main.h"
#include "luxar_hardware.h"

static void SystemClock_Config(void);
void Error_Handler(void);

int main(void)
{
    HAL_Init();
    SystemClock_Config();
    LuxarHardware_Init();

    app_main_init();

    while (1) {
        app_main_loop();
    }

    return 0;
}

static void SystemClock_Config(void)
{
    RCC_OscInitTypeDef osc = {0};
    RCC_ClkInitTypeDef clk = {0};

    osc.OscillatorType = RCC_OSCILLATORTYPE_HSE;
    osc.HSEState = RCC_HSE_ON;
    osc.HSEPredivValue = RCC_HSE_PREDIV_DIV1;
    osc.HSIState = RCC_HSI_ON;
    osc.PLL.PLLState = RCC_PLL_ON;
    osc.PLL.PLLSource = RCC_PLLSOURCE_HSE;
    osc.PLL.PLLMUL = RCC_PLL_MUL9;
    if (HAL_RCC_OscConfig(&osc) != HAL_OK) {
        Error_Handler();
    }

    clk.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK |
                    RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
    clk.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
    clk.AHBCLKDivider = RCC_SYSCLK_DIV1;
    clk.APB1CLKDivider = RCC_HCLK_DIV2;
    clk.APB2CLKDivider = RCC_HCLK_DIV1;
    if (HAL_RCC_ClockConfig(&clk, FLASH_LATENCY_2) != HAL_OK) {
        Error_Handler();
    }
}

void SysTick_Handler(void)
{
    HAL_IncTick();
}

void Error_Handler(void)
{
    __disable_irq();
    while (1) {
    }
}
"""


LUXAR_HARDWARE_H = """#ifndef LUXAR_HARDWARE_H
#define LUXAR_HARDWARE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

void LuxarHardware_Init(void);
void LuxarHardwareInit(void);
void luxar_delay_ms(uint32_t delay_ms);
void luxar_uart_write(const char *text);
void luxar_rgb_pwm_set(uint16_t red, uint16_t green, uint16_t blue);
int luxar_spi_transfer(const uint8_t *tx, uint8_t *rx, uint16_t length);
int luxar_i2c_txrx(uint16_t address, const uint8_t *tx, uint16_t tx_length, uint8_t *rx, uint16_t rx_length);

#ifdef __cplusplus
}
#endif

#endif /* LUXAR_HARDWARE_H */
"""


HAL_CONF_H = """#ifndef STM32F1xx_HAL_CONF_H
#define STM32F1xx_HAL_CONF_H

#ifdef __cplusplus
extern "C" {
#endif

#define HAL_MODULE_ENABLED
#define HAL_GPIO_MODULE_ENABLED
#define HAL_I2C_MODULE_ENABLED
#define HAL_UART_MODULE_ENABLED
#define HAL_RCC_MODULE_ENABLED
#define HAL_CORTEX_MODULE_ENABLED
#define HAL_DMA_MODULE_ENABLED
#define HAL_FLASH_MODULE_ENABLED
#define HAL_PWR_MODULE_ENABLED
#define HAL_TIM_MODULE_ENABLED

#define HSE_VALUE    8000000UL
#define HSI_VALUE    8000000UL
#define LSE_VALUE    32768UL
#define LSI_VALUE    40000UL
#define HSE_STARTUP_TIMEOUT 100U
#define HSI_STARTUP_TIMEOUT 5000U
#define LSE_STARTUP_TIMEOUT 5000U
#define VDD_VALUE    3300U
#define TICK_INT_PRIORITY 0x0FU
#define USE_RTOS     0U
#define PREFETCH_ENABLE 1U

#ifdef USE_FULL_ASSERT
#define assert_param(expr) ((expr) ? (void)0U : assert_failed((uint8_t *)__FILE__, __LINE__))
#else
#define assert_param(expr) ((void)0U)
#endif

#define USE_HAL_TIM_REGISTER_CALLBACKS 0U
#define USE_HAL_I2C_REGISTER_CALLBACKS 0U
#define USE_HAL_UART_REGISTER_CALLBACKS 0U

#ifdef HAL_RCC_MODULE_ENABLED
#include "stm32f1xx_hal_rcc.h"
#endif
#ifdef HAL_GPIO_MODULE_ENABLED
#include "stm32f1xx_hal_gpio.h"
#endif
#ifdef HAL_DMA_MODULE_ENABLED
#include "stm32f1xx_hal_dma.h"
#endif
#ifdef HAL_CORTEX_MODULE_ENABLED
#include "stm32f1xx_hal_cortex.h"
#endif
#ifdef HAL_FLASH_MODULE_ENABLED
#include "stm32f1xx_hal_flash.h"
#endif
#ifdef HAL_PWR_MODULE_ENABLED
#include "stm32f1xx_hal_pwr.h"
#endif
#ifdef HAL_TIM_MODULE_ENABLED
#include "stm32f1xx_hal_tim.h"
#endif
#ifdef HAL_I2C_MODULE_ENABLED
#include "stm32f1xx_hal_i2c.h"
#endif
#ifdef HAL_UART_MODULE_ENABLED
#include "stm32f1xx_hal_uart.h"
#endif

#ifdef __cplusplus
}
#endif

#endif /* STM32F1xx_HAL_CONF_H */
"""


TOOLCHAIN_CMAKE = """set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR arm)

set(CMAKE_C_COMPILER arm-none-eabi-gcc)
set(CMAKE_CXX_COMPILER arm-none-eabi-g++)
set(CMAKE_ASM_COMPILER arm-none-eabi-gcc)
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
set(CMAKE_EXECUTABLE_SUFFIX ".elf")

set(TARGET_FLAGS "-mcpu=cortex-m3 -mthumb")
set(CMAKE_C_FLAGS "${TARGET_FLAGS} -ffunction-sections -fdata-sections")
set(CMAKE_ASM_FLAGS "${TARGET_FLAGS} -x assembler-with-cpp")
set(CMAKE_EXE_LINKER_FLAGS "${TARGET_FLAGS} -nostartfiles --specs=nosys.specs --specs=nano.specs -Wl,--gc-sections")
"""


STARTUP_ASM = """.syntax unified
.cpu cortex-m3
.thumb

.global g_pfnVectors
.global Reset_Handler
.global Default_Handler
.global SysTick_Handler
.type Reset_Handler, %function
.type Default_Handler, %function
.type SysTick_Handler, %function
.thumb_func

.section .isr_vector, "a", %progbits
g_pfnVectors:
    .word _estack
    .word Reset_Handler
    .word Default_Handler  /* NMI */
    .word Default_Handler  /* HardFault */
    .word Default_Handler  /* MemManage */
    .word Default_Handler  /* BusFault */
    .word Default_Handler  /* UsageFault */
    .word 0
    .word 0
    .word 0
    .word 0
    .word Default_Handler  /* SVCall */
    .word Default_Handler  /* DebugMonitor */
    .word 0
    .word Default_Handler  /* PendSV */
    .word SysTick_Handler  /* SysTick */

.section .text.Reset_Handler, "ax", %progbits
Reset_Handler:
    ldr r0, =_sidata
    ldr r1, =_sdata
    ldr r2, =_edata
1:
    cmp r1, r2
    bcs 2f
    ldr r3, [r0], #4
    str r3, [r1], #4
    b 1b
2:
    ldr r1, =_sbss
    ldr r2, =_ebss
    movs r3, #0
3:
    cmp r1, r2
    bcs 4f
    str r3, [r1], #4
    b 3b
4:
    bl SystemInit
    bl main
5:
    b 5b

.section .text.Default_Handler, "ax", %progbits
Default_Handler:
6:
    b 6b

.weak SysTick_Handler
.section .text.SysTick_Handler, "ax", %progbits
SysTick_Handler:
    b .
"""


SYSTEM_INIT_C = """#include "stm32f1xx.h"
#include "stm32f1xx_hal.h"

uint32_t SystemCoreClock = 8000000U;
const uint8_t AHBPrescTable[16U] = {0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 6, 7, 8, 9};
const uint8_t APBPrescTable[8U] = {0, 0, 0, 0, 1, 2, 3, 4};

void SystemInit(void)
{
    /* Keep reset clock defaults here; main.c configures the target clock tree with HAL. */
}

void SystemCoreClockUpdate(void)
{
    SystemCoreClock = HAL_RCC_GetHCLKFreq();
}
"""


LINKER_SCRIPT = """ENTRY(Reset_Handler)

MEMORY
{
  FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 64K
  RAM   (xrw) : ORIGIN = 0x20000000, LENGTH = 20K
}

_estack = ORIGIN(RAM) + LENGTH(RAM);

SECTIONS
{
  .isr_vector :
  {
    . = ALIGN(4);
    KEEP(*(.isr_vector))
    . = ALIGN(4);
  } > FLASH

  .text :
  {
    . = ALIGN(4);
    *(.text*)
    *(.rodata*)
    . = ALIGN(4);
    _etext = .;
  } > FLASH

  _sidata = LOADADDR(.data);

  .data :
  {
    . = ALIGN(4);
    _sdata = .;
    *(.data*)
    . = ALIGN(4);
    _edata = .;
  } > RAM AT> FLASH

  .bss :
  {
    . = ALIGN(4);
    _sbss = .;
    *(.bss*)
    *(COMMON)
    . = ALIGN(4);
    _ebss = .;
  } > RAM

  . = ALIGN(4);
  _end = .;
}
"""


FIRMWARE_CMAKELISTS = """cmake_minimum_required(VERSION 3.20)
set(CMAKE_TOOLCHAIN_FILE "${CMAKE_SOURCE_DIR}/cmake/toolchain-arm-none-eabi.cmake" CACHE STRING "Toolchain file")
project(stm32_firmware_app C ASM)

set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -T${CMAKE_SOURCE_DIR}/cmake/stm32.ld")

set(HAL_DRIVER_SOURCES
@HAL_DRIVER_SOURCES@
)

set(APP_DRIVER_SOURCES
@APP_DRIVER_SOURCES@
)

add_executable(${PROJECT_NAME}
    Core/Src/startup_stm32.s
    Core/Src/system_stm32xx.c
    Core/Src/main.c
    Core/Src/luxar_hardware.c
    App/Src/app_main.c
    ${APP_DRIVER_SOURCES}
    ${HAL_DRIVER_SOURCES}
)

target_include_directories(${PROJECT_NAME} PRIVATE
    Core/Inc
    App/Inc
    Drivers/CMSIS/Include
    Drivers/STM32F1xx_HAL_Driver/Inc
@APP_DRIVER_INCLUDE_DIRS@
)

if(EXISTS "${CMAKE_SOURCE_DIR}/Drivers/CMSIS/Core/Include")
    target_include_directories(${PROJECT_NAME} PRIVATE Drivers/CMSIS/Core/Include)
endif()

if(EXISTS "${CMAKE_SOURCE_DIR}/Drivers/CMSIS/Device/ST")
    file(GLOB DEVICE_INCLUDE_DIRS CONFIGURE_DEPENDS "Drivers/CMSIS/Device/ST/*/Include")
    foreach(dir ${DEVICE_INCLUDE_DIRS})
        target_include_directories(${PROJECT_NAME} PRIVATE ${dir})
    endforeach()
endif()

target_compile_definitions(${PROJECT_NAME} PRIVATE
    STM32_TARGET_FAMILY="@STM32_FAMILY@"
    @STM32_FAMILY_DEFINE@
    @STM32_DEVICE_DEFINE@
    USE_HAL_DRIVER
)
"""


DEFAULT_STM32_HAL_SOURCES = [
    "Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal.c",
    "Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_cortex.c",
    "Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_dma.c",
    "Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_flash.c",
    "Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_flash_ex.c",
    "Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_gpio.c",
    "Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_i2c.c",
    "Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_pwr.c",
    "Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_rcc.c",
    "Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_rcc_ex.c",
    "Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_tim.c",
    "Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_tim_ex.c",
    "Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_uart.c",
]


class Assembler:
    def _cmake_list_entries(self, values: list[str], indent: str = "    ") -> str:
        cleaned = []
        for value in values:
            normalized = value.replace("\\", "/").strip()
            if normalized and normalized not in cleaned:
                cleaned.append(normalized)
        return "\n".join(f'{indent}"{item}"' for item in cleaned)

    def _cmake_include_entries(self, values: list[str], indent: str = "    ") -> str:
        entries = self._cmake_list_entries(values, indent=indent)
        return ("\n" + entries) if entries else ""

    def _relative_existing_sources(self, project_dir: Path, candidates: list[str]) -> list[str]:
        return [item for item in candidates if (project_dir / item).exists()]

    def _render_minimal_cmake(self, driver_sources: list[str] | None = None, driver_include_dirs: list[str] | None = None) -> str:
        return (
            CMAKELISTS_TXT
            .replace("@APP_DRIVER_SOURCES@", self._cmake_list_entries(driver_sources or []))
            .replace("@APP_DRIVER_INCLUDE_DIRS@", self._cmake_include_entries(driver_include_dirs or []))
        )

    def _render_firmware_cmake(
        self,
        *,
        stm32_family: str,
        family_define: str,
        project_dir: Path,
        mcu: str = "",
        driver_sources: list[str] | None = None,
        driver_include_dirs: list[str] | None = None,
    ) -> str:
        hal_sources = self._relative_existing_sources(project_dir, DEFAULT_STM32_HAL_SOURCES)
        return (
            FIRMWARE_CMAKELISTS
            .replace("@STM32_FAMILY@", stm32_family)
            .replace("@STM32_FAMILY_DEFINE@", family_define)
            .replace("@STM32_DEVICE_DEFINE@", self._stm32_device_define_from_mcu(mcu))
            .replace("@HAL_DRIVER_SOURCES@", self._cmake_list_entries(hal_sources))
            .replace("@APP_DRIVER_SOURCES@", self._cmake_list_entries(driver_sources or []))
            .replace("@APP_DRIVER_INCLUDE_DIRS@", self._cmake_include_entries(driver_include_dirs or []))
        )

    def _render_luxar_hardware_c(self, project_plan: ProjectPlan | None = None) -> str:
        caps = project_plan.internal_peripherals if project_plan is not None else []
        uart_caps = [cap for cap in caps if cap.interface.upper() == "UART" and cap.instance.upper() in {"USART2", "UART2"}]
        i2c1_cap = next(
            (cap for cap in caps if cap.interface.upper() == "I2C" and cap.instance.upper() == "I2C1"),
            None,
        )
        tim3_pwm = next(
            (
                cap for cap in caps
                if cap.interface.upper() in {"TIM", "PWM"} and cap.instance.upper() == "TIM3" and "PWM" in cap.mode.upper()
            ),
            None,
        )

        gpio_lines = self._render_gpio_init_lines(
            uart_caps=uart_caps,
            i2c1_cap=i2c1_cap,
            tim3_pwm=tim3_pwm,
            project_plan=project_plan,
        )
        uart_declarations = "UART_HandleTypeDef huart2;\n" if uart_caps else ""
        i2c_declarations = "I2C_HandleTypeDef hi2c1;\n" if i2c1_cap else ""
        tim_declarations = "TIM_HandleTypeDef htim3;\n" if tim3_pwm else ""
        uart_proto = "static void MX_USART2_UART_Init(void);\n" if uart_caps else ""
        i2c_proto = "static void MX_I2C1_Init(void);\n" if i2c1_cap else ""
        tim_proto = "static void MX_TIM3_PWM_Init(void);\n" if tim3_pwm else ""
        init_calls = []
        if uart_caps:
            init_calls.append("    MX_USART2_UART_Init();")
        if i2c1_cap:
            init_calls.append("    MX_I2C1_Init();")
        if tim3_pwm:
            init_calls.append("    MX_TIM3_PWM_Init();")
        init_call_text = "\n".join(init_calls) or "    /* No internal peripheral init requested yet. */"
        uart_write = self._render_uart_write(has_uart=bool(uart_caps))
        i2c_txrx = self._render_i2c_txrx(i2c1_cap)
        rgb_pwm = self._render_rgb_pwm_set(tim3_pwm, project_plan=project_plan)
        uart_init = self._render_usart2_init() if uart_caps else ""
        i2c_init = self._render_i2c1_init(i2c1_cap) if i2c1_cap else ""
        tim_init = self._render_tim3_pwm_init(tim3_pwm) if tim3_pwm else ""

        return f"""#include \"luxar_hardware.h\"
#include \"stm32f1xx_hal.h\"

#include <stddef.h>
#include <string.h>

{uart_declarations}{i2c_declarations}{tim_declarations}
void Error_Handler(void);
static void MX_GPIO_Init(void);
{uart_proto}{i2c_proto}{tim_proto}

void LuxarHardware_Init(void)
{{
    MX_GPIO_Init();
{init_call_text}
}}

void LuxarHardwareInit(void)
{{
    LuxarHardware_Init();
}}

void luxar_delay_ms(uint32_t delay_ms)
{{
    HAL_Delay(delay_ms);
}}

{uart_write}

{rgb_pwm}

int luxar_spi_transfer(const uint8_t *tx, uint8_t *rx, uint16_t length)
{{
    (void)tx;
    (void)rx;
    (void)length;
    return -1;
}}

{i2c_txrx}

static void MX_GPIO_Init(void)
{{
{gpio_lines}
}}

{uart_init}
{i2c_init}
{tim_init}
"""

    def _render_gpio_init_lines(
        self,
        *,
        uart_caps: list[PeripheralCapability],
        i2c1_cap: PeripheralCapability | None,
        tim3_pwm: PeripheralCapability | None,
        project_plan: ProjectPlan | None,
    ) -> str:
        ports: set[str] = set()
        blocks: list[str] = []
        if uart_caps:
            ports.update({"A"})
            blocks.append(
                """    GPIO_InitStruct.Pin = GPIO_PIN_2;
    GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

    GPIO_InitStruct.Pin = GPIO_PIN_3;
    GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);"""
            )
        if i2c1_cap:
            i2c_pins = dict(i2c1_cap.pins or {})
            scl = i2c_pins.get("SCL", "PB6")
            sda = i2c_pins.get("SDA", "PB7")
            pins_by_port: dict[str, list[str]] = {}
            for pin in (scl, sda):
                port, gpio_pin = self._split_gpio_pin(pin)
                if port and gpio_pin:
                    ports.add(port)
                    pins_by_port.setdefault(port, []).append(gpio_pin)
            for port, pins in sorted(pins_by_port.items()):
                blocks.append(
                    f"""    GPIO_InitStruct.Pin = {' | '.join(pins)};
    GPIO_InitStruct.Mode = GPIO_MODE_AF_OD;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
    HAL_GPIO_Init(GPIO{port}, &GPIO_InitStruct);"""
                )
        if tim3_pwm:
            timer_pins = set(pin.upper() for pin in tim3_pwm.pins.values())
            if not timer_pins:
                timer_pins = {"PA6", "PA7", "PB0"}
            pins_by_port: dict[str, list[str]] = {}
            for pin in sorted(timer_pins):
                port, gpio_pin = self._split_gpio_pin(pin)
                if port and gpio_pin:
                    ports.add(port)
                    pins_by_port.setdefault(port, []).append(gpio_pin)
            for port, pins in sorted(pins_by_port.items()):
                blocks.append(
                    f"""    GPIO_InitStruct.Pin = {' | '.join(pins)};
    GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
    HAL_GPIO_Init(GPIO{port}, &GPIO_InitStruct);"""
                )

        if project_plan is not None:
            for feature in project_plan.board_features:
                if feature.instance.upper() != "LED":
                    continue
                pin = next(iter(feature.pins.values()), "")
                port, gpio_pin = self._split_gpio_pin(pin)
                if not port or not gpio_pin:
                    continue
                ports.add(port)
                blocks.append(
                    f"""    HAL_GPIO_WritePin(GPIO{port}, {gpio_pin}, GPIO_PIN_SET);
    GPIO_InitStruct.Pin = {gpio_pin};
    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(GPIO{port}, &GPIO_InitStruct);"""
                )

        clock_lines = [f"    __HAL_RCC_GPIO{port}_CLK_ENABLE();" for port in sorted(ports)]
        if blocks:
            return "\n".join(["    GPIO_InitTypeDef GPIO_InitStruct = {0};", ""] + clock_lines + [""] + blocks)
        return "    /* No GPIO pins required by the current hardware plan. */"

    def _render_uart_write(self, *, has_uart: bool) -> str:
        if not has_uart:
            return """void luxar_uart_write(const char *text)
{
    (void)text;
}"""
        return """void luxar_uart_write(const char *text)
{
    if (text == NULL) {
        return;
    }
    (void)HAL_UART_Transmit(&huart2, (uint8_t *)text, (uint16_t)strlen(text), 100U);
}"""

    def _render_rgb_pwm_set(self, tim3_pwm: PeripheralCapability | None, *, project_plan: ProjectPlan | None = None) -> str:
        if tim3_pwm is None:
            return """void luxar_rgb_pwm_set(uint16_t red, uint16_t green, uint16_t blue)
{
    (void)red;
    (void)green;
    (void)blue;
}"""
        channel_by_pin = {pin.upper(): channel.upper() for channel, pin in tim3_pwm.pins.items()}
        rgb_pins = self._rgb_role_pins(project_plan) or {"R": "PA6", "G": "PA7", "B": "PB0"}
        red_channel = self._tim_channel_expr(channel_by_pin.get(rgb_pins.get("R", "PA6").upper(), "CH1"))
        green_channel = self._tim_channel_expr(channel_by_pin.get(rgb_pins.get("G", "PA7").upper(), "CH2"))
        blue_channel = self._tim_channel_expr(channel_by_pin.get(rgb_pins.get("B", "PB0").upper(), "CH3"))
        return f"""void luxar_rgb_pwm_set(uint16_t red, uint16_t green, uint16_t blue)
{{
    __HAL_TIM_SET_COMPARE(&htim3, {red_channel}, red);
    __HAL_TIM_SET_COMPARE(&htim3, {green_channel}, green);
    __HAL_TIM_SET_COMPARE(&htim3, {blue_channel}, blue);
}}"""

    def _render_i2c_txrx(self, i2c1_cap: PeripheralCapability | None) -> str:
        if i2c1_cap is None:
            return """int luxar_i2c_txrx(uint16_t address, const uint8_t *tx, uint16_t tx_length, uint8_t *rx, uint16_t rx_length)
{
    (void)address;
    (void)tx;
    (void)tx_length;
    (void)rx;
    (void)rx_length;
    return -1;
}"""
        return """int luxar_i2c_txrx(uint16_t address, const uint8_t *tx, uint16_t tx_length, uint8_t *rx, uint16_t rx_length)
{
    const uint16_t dev_addr = (uint16_t)(address << 1);
    if (tx != NULL && tx_length > 0U) {
        if (HAL_I2C_Master_Transmit(&hi2c1, dev_addr, (uint8_t *)tx, tx_length, 100U) != HAL_OK) {
            return -1;
        }
    }
    if (rx != NULL && rx_length > 0U) {
        if (HAL_I2C_Master_Receive(&hi2c1, dev_addr, rx, rx_length, 100U) != HAL_OK) {
            return -1;
        }
    }
    return 0;
}"""

    def _render_usart2_init(self) -> str:
        return """static void MX_USART2_UART_Init(void)
{
    __HAL_RCC_USART2_CLK_ENABLE();

    huart2.Instance = USART2;
    huart2.Init.BaudRate = 115200;
    huart2.Init.WordLength = UART_WORDLENGTH_8B;
    huart2.Init.StopBits = UART_STOPBITS_1;
    huart2.Init.Parity = UART_PARITY_NONE;
    huart2.Init.Mode = UART_MODE_TX_RX;
    huart2.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart2.Init.OverSampling = UART_OVERSAMPLING_16;
    if (HAL_UART_Init(&huart2) != HAL_OK) {
        Error_Handler();
    }
}
"""

    def _render_i2c1_init(self, i2c1_cap: PeripheralCapability | None) -> str:
        frequency = "400000" if i2c1_cap and "400" in i2c1_cap.frequency else "100000"
        return f"""static void MX_I2C1_Init(void)
{{
    __HAL_RCC_I2C1_CLK_ENABLE();

    hi2c1.Instance = I2C1;
    hi2c1.Init.ClockSpeed = {frequency};
    hi2c1.Init.DutyCycle = I2C_DUTYCYCLE_2;
    hi2c1.Init.OwnAddress1 = 0;
    hi2c1.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
    hi2c1.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
    hi2c1.Init.OwnAddress2 = 0;
    hi2c1.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
    hi2c1.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
    if (HAL_I2C_Init(&hi2c1) != HAL_OK) {{
        Error_Handler();
    }}
}}
"""

    def _render_tim3_pwm_init(self, tim3_pwm: PeripheralCapability | None) -> str:
        pins = tim3_pwm.pins if tim3_pwm is not None else {}
        channels = [self._tim_channel_expr(channel) for channel in pins.keys()] or [
            "TIM_CHANNEL_1",
            "TIM_CHANNEL_2",
            "TIM_CHANNEL_3",
        ]
        config_lines = []
        for channel in channels:
            config_lines.append(
                f"""    if (HAL_TIM_PWM_ConfigChannel(&htim3, &sConfigOC, {channel}) != HAL_OK) {{
        Error_Handler();
    }}
    if (HAL_TIM_PWM_Start(&htim3, {channel}) != HAL_OK) {{
        Error_Handler();
    }}"""
            )
        return f"""static void MX_TIM3_PWM_Init(void)
{{
    TIM_OC_InitTypeDef sConfigOC = {{0}};

    __HAL_RCC_TIM3_CLK_ENABLE();

    htim3.Instance = TIM3;
    htim3.Init.Prescaler = 71;
    htim3.Init.CounterMode = TIM_COUNTERMODE_UP;
    htim3.Init.Period = 999;
    htim3.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
    htim3.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
    if (HAL_TIM_PWM_Init(&htim3) != HAL_OK) {{
        Error_Handler();
    }}

    sConfigOC.OCMode = TIM_OCMODE_PWM1;
    sConfigOC.Pulse = 0;
    sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
    sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
{chr(10).join(config_lines)}
}}
"""

    def _split_gpio_pin(self, pin: str) -> tuple[str, str]:
        match = re.fullmatch(r"P([A-K])(\d{1,2})", pin.strip().upper())
        if not match:
            return "", ""
        return match.group(1), f"GPIO_PIN_{match.group(2)}"

    def _tim_channel_expr(self, channel: str) -> str:
        match = re.search(r"(\d+)", channel)
        if not match:
            return "TIM_CHANNEL_1"
        return f"TIM_CHANNEL_{match.group(1)}"

    def _rgb_role_pins(self, project_plan: ProjectPlan | None) -> dict[str, str]:
        if project_plan is None:
            return {}
        for feature in project_plan.board_features:
            if feature.instance.upper() != "RGB_LED":
                continue
            pins = {
                role.upper(): pin.upper()
                for role, pin in feature.pins.items()
                if role.upper() in {"R", "G", "B"} and pin
            }
            if pins:
                return pins
        return {}

    def write_build_manifest(
        self,
        project: ProjectConfig,
        *,
        driver_sources: list[str] | None = None,
        driver_include_dirs: list[str] | None = None,
        stm32_family: str = "",
        family_define: str = "",
    ) -> list[str]:
        project_dir = Path(project.path)
        driver_sources = driver_sources or []
        driver_include_dirs = driver_include_dirs or []
        manifest = BuildManifest(
            core_sources=[
                "Core/Src/startup_stm32.s",
                "Core/Src/system_stm32xx.c",
                "Core/Src/main.c",
                "Core/Src/luxar_hardware.c",
            ] if project.project_mode == "firmware" else [],
            app_sources=["App/Src/app_main.c"],
            hal_sources=self._relative_existing_sources(project_dir, DEFAULT_STM32_HAL_SOURCES) if project.project_mode == "firmware" else [],
            driver_sources=driver_sources,
            include_dirs=[
                "Core/Inc",
                "App/Inc",
                "Drivers/CMSIS/Include",
                "Drivers/STM32F1xx_HAL_Driver/Inc",
                *driver_include_dirs,
            ] if project.project_mode == "firmware" else ["App/Inc", *driver_include_dirs],
            compile_definitions=["USE_HAL_DRIVER"],
        )
        manifest_path = project_dir / "LUXAR_BUILD_MANIFEST.json"
        manifest_path.write_text(
            json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        cmake = project_dir / "CMakeLists.txt"
        if project.project_mode == "firmware":
            family = stm32_family or self._read_marker(project_dir / "STM32_FAMILY.txt") or "F1"
            define = family_define or (f"STM32{family.upper()}xx" if family else "")
            cmake.write_text(
                self._render_firmware_cmake(
                    stm32_family=family,
                    family_define=define,
                    project_dir=project_dir,
                    mcu=project.mcu,
                    driver_sources=driver_sources,
                    driver_include_dirs=driver_include_dirs,
                ),
                encoding="utf-8",
            )
        else:
            cmake.write_text(
                self._render_minimal_cmake(driver_sources=driver_sources, driver_include_dirs=driver_include_dirs),
                encoding="utf-8",
            )
        return [str(manifest_path), str(cmake)]

    def _read_marker(self, path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    def _stm32_device_define_from_mcu(self, mcu: str) -> str:
        normalized = mcu.upper().replace("-", "").replace("_", "").strip()
        if normalized.startswith("STM32F103"):
            flash_code_match = re.match(r"STM32F103[A-Z]([0-9A-Z])", normalized)
            if flash_code_match:
                flash_code = flash_code_match.group(1)
                if flash_code == "6":
                    return "STM32F103x6"
                if flash_code in {"8", "B"}:
                    return "STM32F103xB"
                if flash_code in {"C", "D", "E"}:
                    return "STM32F103xE"
                if flash_code in {"F", "G"}:
                    return "STM32F103xG"
            if any(normalized.endswith(suffix) for suffix in ("C6", "K6", "T6")):
                return "STM32F103x6"
            if any(normalized.endswith(suffix) for suffix in ("C8", "CB", "R8", "RB", "T8", "TB")):
                return "STM32F103xB"
            if any(normalized.endswith(suffix) for suffix in ("RC", "RD", "RE", "VC", "VD", "VE", "ZC", "ZD", "ZE")):
                return "STM32F103xE"
            if any(normalized.endswith(suffix) for suffix in ("RF", "RG", "VF", "VG", "ZF", "ZG")):
                return "STM32F103xG"
            return "STM32F103xB"
        return ""

    def assemble_minimal_app(self, project: ProjectConfig) -> list[str]:
        project_dir = Path(project.path)
        created_files: list[str] = []

        app_inc = project_dir / "App" / "Inc"
        app_src = project_dir / "App" / "Src"
        app_inc.mkdir(parents=True, exist_ok=True)
        app_src.mkdir(parents=True, exist_ok=True)

        header = app_inc / "app_main.h"
        source = app_src / "app_main.c"
        cmake = project_dir / "CMakeLists.txt"

        if not header.exists():
            header.write_text(APP_MAIN_H, encoding="utf-8")
            created_files.append(str(header))
        if not source.exists():
            source.write_text(APP_MAIN_C, encoding="utf-8")
            created_files.append(str(source))
        if not cmake.exists():
            cmake.write_text(self._render_minimal_cmake(), encoding="utf-8")
            created_files.append(str(cmake))
        created_files.extend(self.write_build_manifest(project))

        return created_files

    def assemble_stm32_firmware_project(
        self,
        project: ProjectConfig,
        firmware_package: str,
        stm32_family: str,
        build_context: dict | None = None,
        staged_firmware_paths: list[str] | None = None,
        project_plan: ProjectPlan | None = None,
    ) -> list[str]:
        project_dir = Path(project.path)
        created_files: list[str] = []
        build_context = build_context or {}
        staged_firmware_paths = staged_firmware_paths or []

        for directory in (
            project_dir / "App" / "Inc",
            project_dir / "App" / "Src",
            project_dir / "Core" / "Inc",
            project_dir / "Core" / "Src",
            project_dir / "Drivers",
            project_dir / "cmake",
        ):
            directory.mkdir(parents=True, exist_ok=True)

        family_define = build_context.get("family_define", "")
        firmware_cmakelists = self._render_firmware_cmake(
            stm32_family=stm32_family,
            family_define=family_define,
            project_dir=project_dir,
            mcu=project.mcu,
        )

        file_map = {
            project_dir / "App" / "Inc" / "app_main.h": APP_MAIN_H,
            project_dir / "App" / "Src" / "app_main.c": APP_MAIN_C,
            project_dir / "Core" / "Src" / "main.c": FIRMWARE_MAIN_C,
            project_dir / "Core" / "Src" / "luxar_hardware.c": self._render_luxar_hardware_c(project_plan),
            project_dir / "Core" / "Src" / "system_stm32xx.c": SYSTEM_INIT_C,
            project_dir / "Core" / "Src" / "startup_stm32.s": STARTUP_ASM,
            project_dir / "Core" / "Inc" / "stm32f1xx_hal_conf.h": HAL_CONF_H,
            project_dir / "Core" / "Inc" / "luxar_hardware.h": LUXAR_HARDWARE_H,
            project_dir / "cmake" / "toolchain-arm-none-eabi.cmake": TOOLCHAIN_CMAKE,
            project_dir / "cmake" / "stm32.ld": LINKER_SCRIPT,
            project_dir / "CMakeLists.txt": firmware_cmakelists,
            project_dir / "FIRMWARE_PACKAGE.txt": f"{firmware_package}\n",
            project_dir / "STM32_FAMILY.txt": f"{stm32_family}\n",
        }
        for path, content in file_map.items():
            should_rewrite = path.name in {
                "CMakeLists.txt",
                "luxar_hardware.c",
                "luxar_hardware.h",
                "stm32f1xx_hal_conf.h",
                "toolchain-arm-none-eabi.cmake",
                "stm32.ld",
                "FIRMWARE_PACKAGE.txt",
                "STM32_FAMILY.txt",
            }
            if should_rewrite or not path.exists():
                path.write_text(content, encoding="utf-8")
                created_files.append(str(path))

        created_files.extend(
            self.write_build_manifest(
                project,
                stm32_family=stm32_family,
                family_define=family_define,
            )
        )
        created_files.extend(staged_firmware_paths)
        return created_files

    def install_driver_records(
        self,
        project: ProjectConfig,
        drivers: list[DriverMetadata],
    ) -> list[str]:
        project_dir = Path(project.path)
        created_files: list[str] = []
        for driver in drivers:
            driver_name = driver.name.strip() or Path(driver.path).stem
            target_root = project_dir / "App" / "Drivers" / driver_name
            inc_dir = target_root / "Inc"
            src_dir = target_root / "Src"
            inc_dir.mkdir(parents=True, exist_ok=True)
            src_dir.mkdir(parents=True, exist_ok=True)

            if driver.header_path:
                source_header = Path(driver.header_path)
                if source_header.exists():
                    target_header = inc_dir / source_header.name
                    source_source = Path(driver.source_path or driver.path)
                    target_source = src_dir / source_source.name
                    if source_source.exists():
                        copy_driver_artifacts(
                            source_header=source_header,
                            source_source=source_source,
                            target_header=target_header,
                            target_source=target_source,
                        )
                        created_files.append(str(target_header))
                        created_files.append(str(target_source))
                        continue
                    shutil.copy2(source_header, target_header)
                    created_files.append(str(target_header))

            source_source = Path(driver.source_path or driver.path)
            if source_source.exists():
                target_source = src_dir / source_source.name
                shutil.copy2(source_source, target_source)
                created_files.append(str(target_source))

        return created_files
