---
id: scaffold_project_2
name: Scaffold Project 2
category: project
version: 1.0.0
promotion_level: draft
executable: true
description: Create the complete project 2 embedded C project file structure
---

# Scaffold Project 2

Create a complete embedded C project framework for project '2' at `C:\Users\Gugugu\Documents\Codex\LUXAR\workspace\projects\2`.

## Actions

1. Create directory structure: src/, include/, cmake/, startup/, linker/, build/
2. Write CMakeLists.txt at project root with:
   - cmake_minimum_required(VERSION 3.20)
   - project(project_2 C ASM)
   - Toolchain file reference
   - Source file glob
   - Linker script configuration
   - Post-build hex/bin generation
3. Write cmake/arm-none-eabi-toolchain.cmake with cortex-m4 cross-compile settings
4. Write src/main.c with HAL init, clock config, and main loop
5. Write include/main.h with function prototypes
6. Write linker/STM32F407VGTx_FLASH.ld basic linker script
7. Confirm build readiness with workspace_build

