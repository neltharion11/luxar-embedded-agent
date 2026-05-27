---
name: stm32f1_cubemx_project_setup
category: project
mode: executable
promotion_level: validated
tags: [stm32, f1, cubemx, hal, cmake, template]
template: workspace/templates/cubemx
---

# STM32F1 CubeMX Project Setup

## Description
Creates a production-grade STM32F103C8 project based on CubeMX ecosystem with HAL library. Features separated Core/App/BSP layers, CMake+Ninja build, Debug/Release presets.

## Structure (19 files)
Copied from workspace/templates/cubemx/ with PROJECT_NAME substitution:
- CMakeLists.txt (root + cmake/stm32cubemx/)
- CMakePresets.json
- cmake/gcc-arm-none-eabi.cmake
- Core/Inc/ (main.h, stm32f1xx_hal_conf.h, stm32f1xx_it.h)
- Core/Src/ (main.c, stm32f1xx_it.c, stm32f1xx_hal_msp.c, system_stm32f1xx.c, syscalls.c, sysmem.c)
- App/Inc/app_main.h, App/Src/app_main.c
- startup_stm32f103xb.s, STM32F103XX_FLASH.ld
- Drivers/ (.gitkeep), BSP/ (.gitkeep)

## Inputs
- PROJECT_NAME: project name

## Usage
skill_execute auto-copies the template, replaces keysking_project / PROJECT_NAME placeholders, and writes .agent_project.json.

## Post-Setup
- Copy Drivers/CMSIS and Drivers/STM32F1xx_HAL_Driver from STM32CubeF1 FW package
- Edit App/Src/app_main.c for application logic
- Add BSP modules under BSP/<device>/Inc/ and Src/
