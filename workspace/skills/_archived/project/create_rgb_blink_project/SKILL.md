---
name: create_rgb_blink_project
category: project
mode: executable
promotion_level: draft
template: workspace/templates/baremetal
---

# rgb_blink Project Setup Skill

## Task
Create and build an STM32F103C8 project with:
- MCU: STM32F103C8 (Blue Pill)
- 8MHz HSE crystal → PLL → 72MHz system clock
- PA6 (Green), PA7 (Blue), PB0 (Red) - RGB common cathode LED, HIGH=ON
- Three-color running light (water flow), switching every 500ms
- HAL library (STM32Cube_FW_F1_V1.8.7)

## Source Files

### CMakeLists.txt
```cmake
cmake_minimum_required(VERSION 3.20)
project(rgb_blink C ASM)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

# Toolchain
set(TOOLCHAIN_PREFIX arm-none-eabi)
set(CMAKE_C_COMPILER ${TOOLCHAIN_PREFIX}-gcc)
set(CMAKE_ASM_COMPILER ${TOOLCHAIN_PREFIX}-gcc)
set(CMAKE_OBJCOPY ${TOOLCHAIN_PREFIX}-objcopy)
set(CMAKE_SIZE ${TOOLCHAIN_PREFIX}-size)

# MCU flags
set(MCU_FLAGS "-mcpu=cortex-m3 -mthumb -mfloat-abi=soft")
set(CMAKE_C_FLAGS "${MCU_FLAGS} -Wall -Wextra -Werror -Os -ffunction-sections -fdata-sections -fno-common -fno-builtin")
set(CMAKE_ASM_FLAGS "${MCU_FLAGS} -x assembler-with-cpp")
set(CMAKE_EXE_LINKER_FLAGS "${MCU_FLAGS} -Wl,--gc-sections -Wl,-Map=${PROJECT_NAME}.map -T${CMAKE_SOURCE_DIR}/stm32f103c8_flash.ld -nostartfiles -specs=nosys.specs")

# HAL library paths
set(FW_ROOT "C:/Users/Gugugu/Documents/Codex/LUXAR/workspace/firmware_library/stm32/STM32Cube_FW_F1_V1.8.7")
set(HAL_DRIVER "${FW_ROOT}/Drivers/STM32F1xx_HAL_Driver")
set(CMSIS_CORE "${FW_ROOT}/Drivers/CMSIS/Core/Include")
set(CMSIS_DEVICE "${FW_ROOT}/Drivers/CMSIS/Device/ST/STM32F1xx")

# Include directories
include_directories(
    ${CMAKE_SOURCE_DIR}
    ${HAL_DRIVER}/Inc
    ${CMSIS_CORE}
    ${CMSIS_DEVICE}/Include
)

# HAL source files
file(GLOB HAL_SOURCES
    ${HAL_DRIVER}/Src/stm32f1xx_hal_gpio.c
    ${HAL_DRIVER}/Src/stm32f1xx_hal_rcc.c
    ${HAL_DRIVER}/Src/stm32f1xx_hal_cortex.c
    ${HAL_DRIVER}/Src/stm32f1xx_hal_uart.c
    ${HAL_DRIVER}/Src/stm32f1xx_hal.c
    ${HAL_DRIVER}/Src/stm32f1xx_hal_tim.c
    ${HAL_DRIVER}/Src/stm32f1xx_hal_tim_ex.c
)

# Source files
set(SOURCES
    ${CMAKE_SOURCE_DIR}/startup_stm32f103xb.s
    ${CMAKE_SOURCE_DIR}/app_main.c
    ${CMAKE_SOURCE_DIR}/system_stm32f1xx.c
    ${HAL_SOURCES}
)

add_executable(${PROJECT_NAME}.elf ${SOURCES})

# Post-build: generate .bin
add_custom_command(TARGET ${PROJECT_NAME}.elf POST_BUILD
    COMMAND ${CMAKE_OBJCOPY} -O binary ${PROJECT_NAME}.elf ${PROJECT_NAME}.bin
    COMMAND ${CMAKE_SIZE} ${PROJECT_NAME}.elf
)
```

### app_main.c
```c
#include "stm32f1xx_hal.h"

// LED Pin Definitions - RGB Common Cathode (HIGH=ON)
#define LED_RED_PORT   GPIOB
#define LED_RED_PIN    GPIO_PIN_0
#define LED_GREEN_PORT GPIOA
#define LED_GREEN_PIN  GPIO_PIN_6
#define LED_BLUE_PORT  GPIOA
#define LED_BLUE_PIN   GPIO_PIN_7

// Timing
#define DELAY_MS 500

void SystemClock_Config(void);
void GPIO_Init(void);
void delay_ms(uint32_t ms);

int main(void)
{
    HAL_Init();
    SystemClock_Config();
    GPIO_Init();

    uint32_t phase = 0;

    while (1)
    {
        // Turn all off first
        HAL_GPIO_WritePin(LED_RED_PORT, LED_RED_PIN, GPIO_PIN_RESET);
        HAL_GPIO_WritePin(LED_GREEN_PORT, LED_GREEN_PIN, GPIO_PIN_RESET);
        HAL_GPIO_WritePin(LED_BLUE_PORT, LED_BLUE_PIN, GPIO_PIN_RESET);

        // Phase 0: Red ON
        // Phase 1: Green ON
        // Phase 2: Blue ON
        switch (phase)
        {
            case 0:
                HAL_GPIO_WritePin(LED_RED_PORT, LED_RED_PIN, GPIO_PIN_SET);
                break;
            case 1:
                HAL_GPIO_WritePin(LED_GREEN_PORT, LED_GREEN_PIN, GPIO_PIN_SET);
                break;
            case 2:
                HAL_GPIO_WritePin(LED_BLUE_PORT, LED_BLUE_PIN, GPIO_PIN_SET);
                break;
        }

        phase = (phase + 1) % 3;

        HAL_Delay(DELAY_MS);
    }
}

void SystemClock_Config(void)
{
    RCC_OscInitTypeDef RCC_OscInitStruct = {0};
    RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

    // HSE: 8MHz external crystal, PLL -> 72MHz
    RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
    RCC_OscInitStruct.HSEState = RCC_HSE_ON;
    RCC_OscInitStruct.HSEPredivValue = RCC_HSE_PREDIV_DIV1;
    RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
    RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
    RCC_OscInitStruct.PLL.PLLMUL = RCC_PLL_MUL9;  // 8MHz * 9 = 72MHz
    HAL_RCC_OscConfig(&RCC_OscInitStruct);

    // System clock, AHB, APB1, APB2 dividers
    RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK
                                | RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
    RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
    RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;     // HCLK = 72MHz
    RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;      // APB1 = 36MHz (max 36MHz)
    RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;      // APB2 = 72MHz
    HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2); // Flash 2 wait states for 72MHz
}

void GPIO_Init(void)
{
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_GPIOA_CLK_ENABLE();

    GPIO_InitTypeDef GPIO_InitStruct = {0};
    GPIO_InitStruct.Pin = LED_RED_PIN;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
    GPIO_InitStruct.Pin = LED_GREEN_PIN | LED_BLUE_PIN;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);
    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

    // Start with all LEDs off
    HAL_GPIO_WritePin(GPIOB, LED_RED_PIN, GPIO_PIN_RESET);`r`n    HAL_GPIO_WritePin(GPIOA, LED_GREEN_PIN | LED_BLUE_PIN, GPIO_PIN_RESET);
}
```

### stm32f1xx_hal_conf.h
```c
#ifndef STM32F1XX_HAL_CONF_H
#define STM32F1XX_HAL_CONF_H

#include "stm32f1xx.h"

#define HSE_VALUE    8000000U
#define HSI_VALUE    8000000U
#define LSI_VALUE    40000U
#define LSE_VALUE    32768U

#define HSE_STARTUP_TIMEOUT   100U
#define LSE_STARTUP_TIMEOUT   5000U

#define HAL_MODULE_ENABLED
#define HAL_GPIO_MODULE_ENABLED
#define HAL_RCC_MODULE_ENABLED
#define HAL_CORTEX_MODULE_ENABLED
#define HAL_FLASH_MODULE_ENABLED
#define HAL_PWR_MODULE_ENABLED
#define HAL_EXTI_MODULE_ENABLED
#define HAL_DMA_MODULE_ENABLED
#define HAL_UART_MODULE_ENABLED

#define VDD_VALUE          3300U
#define HAL_MAX_DELAY      0xFFFFFFFFU
#define TICK_INT_PRIORITY  0x0FU
#define USE_RTOS           0U
#define PREFETCH_ENABLE    1U

#endif /* STM32F1XX_HAL_CONF_H */
```

### stm32f103c8_flash.ld
```ld
MEMORY
{
    FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 64K
    RAM   (xrw) : ORIGIN = 0x20000000, LENGTH = 20K
}

_estack = ORIGIN(RAM) + LENGTH(RAM);
_Min_Heap_Size = 0x200;
_Min_Stack_Size = 0x400;

ENTRY(Reset_Handler)

SECTIONS
{
    .isr_vector : {
        . = ALIGN(4);
        KEEP(*(.isr_vector))
        . = ALIGN(4);
    } > FLASH

    .text : {
        . = ALIGN(4);
        *(.text)
        *(.text.*)
        *(.rodata)
        *(.rodata.*)
        *(.glue_7)
        *(.glue_7t)
        KEEP(*(.init))
        KEEP(*(.fini))
        . = ALIGN(4);
        _etext = .;
    } > FLASH

    .ARM.extab : { *(.ARM.extab* .gnu.linkonce.armextab.*) } > FLASH
    .ARM : {
        __exidx_start = .;
        *(.ARM.exidx*)
        __exidx_end = .;
    } > FLASH

    .preinit_array : {
        PROVIDE_HIDDEN (__preinit_array_start = .);
        KEEP(*(.preinit_array*))
        PROVIDE_HIDDEN (__preinit_array_end = .);
    } > FLASH
    .init_array : {
        PROVIDE_HIDDEN (__init_array_start = .);
        KEEP(*(SORT(.init_array.*)))
        KEEP(*(.init_array*))
        PROVIDE_HIDDEN (__init_array_end = .);
    } > FLASH
    .fini_array : {
        PROVIDE_HIDDEN (__fini_array_start = .);
        KEEP(*(SORT(.fini_array.*)))
        KEEP(*(.fini_array*))
        PROVIDE_HIDDEN (__fini_array_end = .);
    } > FLASH

    _sidata = LOADADDR(.data);

    .data : {
        . = ALIGN(4);
        _sdata = .;
        *(.data*)
        . = ALIGN(4);
        _edata = .;
    } > RAM AT>FLASH

    .bss : {
        . = ALIGN(4);
        _sbss = .;
        __bss_start__ = _sbss;
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        _ebss = .;
        __bss_end__ = _ebss;
    } > RAM

    ._user_heap_stack : {
        . = ALIGN(8);
        PROVIDE ( end = . );
        PROVIDE ( _end = . );
        . = . + _Min_Heap_Size;
        . = . + _Min_Stack_Size;
        . = ALIGN(8);
    } > RAM
}
```