# STM32F1 HAL RGB Rewrite Skill

## Description
Rewrite Project 2 to use STM32CubeF1 HAL library instead of raw register manipulation for RGB LED control (PB0=Red, PB6=Green, PB7=Blue, common cathode, HIGH=ON).

## HAL Library Path
`C:\Users\Gugugu\Documents\Codex\LUXAR\workspace\firmware_library\stm32\STM32Cube_FW_F1_V1.8.7`

## Steps

### Step 1: Write CMakeLists.txt
Write CMakeLists.txt at `C:\Users\Gugugu\Documents\Codex\LUXAR\workspace\projects\2\CMakeLists.txt`

```cmake
cmake_minimum_required(VERSION 3.10)
project(STM32F103x8 C ASM)

set(TOOLCHAIN_DIR "C:/Users/Gugugu/Documents/Codex/LUXAR/workspace/toolchains/gcc-arm/bin")
set(FW_LIB "C:/Users/Gugugu/Documents/Codex/LUXAR/workspace/firmware_library/stm32/STM32Cube_FW_F1_V1.8.7")

# HAL include paths
set(HAL_INC
    ${FW_LIB}/Drivers/STM32F1xx_HAL_Driver/Inc
    ${FW_LIB}/Drivers/CMSIS/Device/ST/STM32F1xx/Include
    ${FW_LIB}/Drivers/CMSIS/Core/Include
    ${CMAKE_CURRENT_SOURCE_DIR}
)

# HAL source files
set(HAL_SRC
    ${FW_LIB}/Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal.c
    ${FW_LIB}/Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_gpio.c
    ${FW_LIB}/Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_rcc.c
    ${FW_LIB}/Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_tim.c
)

# CMSIS system source
set(CMSIS_SRC
    ${FW_LIB}/Drivers/CMSIS/Device/ST/STM32F1xx/Source/Templates/system_stm32f1xx.c
)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_FLAGS "-mcpu=cortex-m3 -mthumb -Wall -ffunction-sections -fdata-sections -O2 -DSTM32F103x8 -DUSE_HAL_DRIVER")
set(CMAKE_ASM_FLAGS "-mcpu=cortex-m3 -mthumb -x assembler-with-cpp")
set(CMAKE_EXE_LINKER_FLAGS "-mcpu=cortex-m3 -mthumb -Wl,--gc-sections -Wl,-Map=${CMAKE_PROJECT_NAME}.map -T${CMAKE_CURRENT_SOURCE_DIR}/STM32F103X8.ld")

set(LINKER_SCRIPT ${CMAKE_CURRENT_SOURCE_DIR}/STM32F103X8.ld)

add_executable(${CMAKE_PROJECT_NAME}.elf
    ${CMAKE_CURRENT_SOURCE_DIR}/startup_stm32f103xb.s
    ${CMAKE_CURRENT_SOURCE_DIR}/app_main.c
    ${CMSIS_SRC}
    ${HAL_SRC}
)

target_include_directories(${CMAKE_PROJECT_NAME}.elf PRIVATE ${HAL_INC})

add_custom_command(TARGET ${CMAKE_PROJECT_NAME}.elf POST_BUILD
    COMMAND ${CMAKE_OBJCOPY} -O binary ${CMAKE_PROJECT_NAME}.elf ${CMAKE_PROJECT_NAME}.bin
    COMMAND ${CMAKE_OBJCOPY} -O ihex ${CMAKE_PROJECT_NAME}.elf ${CMAKE_PROJECT_NAME}.hex
    COMMENT "Generating binary and hex files"
)
```

### Step 2: Write linker script
Write STM32F103X8.ld at `C:\Users\Gugugu\Documents\Codex\LUXAR\workspace\projects\2\STM32F103X8.ld`

Standard STM32F103x8 (64KB Flash, 20KB RAM) linker script.

### Step 3: Write stm32f1xx_hal_conf.h
Write conf header at `C:\Users\Gugugu\Documents\Codex\LUXAR\workspace\projects\2\stm32f1xx_hal_conf.h`

```c
#ifndef STM32F1XX_HAL_CONF_H
#define STM32F1XX_HAL_CONF_H

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
#define HAL_TIM_MODULE_ENABLED

#define VDD_VALUE          3300U
#define HAL_MAX_DELAY      0xFFFFFFFFU
#define TICK_INT_PRIORITY  0x0FU
#define USE_RTOS           0U
#define PREFETCH_ENABLE    1U

#endif /* STM32F1XX_HAL_CONF_H */
```

### Step 4: Write app_main.c
Write main source at `C:\Users\Gugugu\Documents\Codex\LUXAR\workspace\projects\2\app_main.c`

HAL-based RGB LED flashing:
- SystemClock_Config() using HSI (8MHz) -> PLL -> 72MHz
- MX_GPIO_Init() configuring PB0, PB6, PB7 as push-pull outputs
- Main loop: cycle through RGB colors with HAL_Delay(500)

### Step 5: Build
Run workspace_build(project=2)

### Step 6: Flash
Run workspace_flash(project=2)
