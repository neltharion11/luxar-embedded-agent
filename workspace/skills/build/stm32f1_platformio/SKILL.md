---
name: stm32f1_platformio
category: build
mode: executable
promotion_level: draft
tags: [stm32, f1, platformio, build, cmake-alternative]
---

# STM32F1 PlatformIO Build Skill

## Description
Generates a platformio.ini and project structure for STM32F1 projects, enabling PlatformIO CLI as an alternative to CMake+Ninja or Makefile builds.

## Inputs
- PROJECT_NAME: project name (e.g., test2)
- MCU: target chip (default STM32F103C8)
- BOARD: PlatformIO board ID (default bluepill_f103c8)
- FRAMEWORK: stm32cube, libopencm3, or arduino (default stm32cube)

## Workflow

### Step 1: Create platformio.ini
Create platformio.ini in project root with platform=ststm32, board matching the target, and framework matching user choice.

### Step 2: Create src/ directory
Ensure src/ directory exists with main.c (or rename app_main.c -> src/main.c).

### Step 3: Verify build
Run platformio run --project-dir . to verify the project builds.

## Outputs
- platformio.ini in project root
- src/ directory with main source
- Build succeeds with platformio run
