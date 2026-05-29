---
name: stm32f1_baremetal_project_setup
category: project
mode: executable
promotion_level: validated
tags: [stm32, f1, baremetal, hal, cmake, template]
template: workspace/templates/baremetal
---

# STM32F1 Baremetal Project Setup (HAL)

## Description
Creates a minimal STM32F103C8 HAL baremetal project. Uses STM32CubeF1 HAL library with proper initialization chain: SystemInit → HAL_Init → SystemClock_Config → app loop. Includes SysTick_Handler, HAL_MspInit (AFIO + NOJTAG), and complete linker script.

## Structure (8 files)
Copied from workspace/templates/baremetal/ with PROJECT_NAME substitution:
- CMakeLists.txt, link.ld
- startup_stm32f103xb.s (SystemInit + __libc_init_array)
- Core/Inc/stm32f1xx_hal_conf.h
- Core/Src/main.c, Core/Src/system_stm32f1xx.c
- Core/Src/stm32f1xx_it.c (SysTick_Handler → HAL_IncTick)
- Core/Src/stm32f1xx_hal_msp.c (AFIO + NOJTAG)

## Inputs
- PROJECT_NAME: project name

## Usage
skill_execute auto-copies the template and replaces PROJECT_NAME placeholders.

## Customization
- Edit Core/Src/main.c to add your application code
- Modify CMakeLists.txt to add more HAL source files / peripherals