---
name: stm32f1_cubemx_project_setup
category: project
mode: executable
promotion_level: validated
tags: [stm32, f1, cubemx, app, bsp, template]
template: workspace/templates/cubemx
---

# STM32F1 CubeMX Project Setup

## Description
Creates the LUXAR-owned user-code folders for a CubeMX project. CubeMX owns Core, Drivers, Middlewares, CMake, build, flash, and toolchain configuration.

## Structure
Copied from workspace/templates/cubemx/ with PROJECT_NAME substitution:
- App/
- BSP/

## Inputs
- PROJECT_NAME: project name

## Usage
skill_execute auto-copies the user-code folders and replaces {PROJECT_NAME} placeholders.

## Post-Setup
- Open STM32CubeMX, configure the MCU and peripherals, and generate code into this project directory.
- Do not add or overwrite CubeMX toolchain files from LUXAR for this platform.
- Edit App/ and BSP/ for application logic that must survive CubeMX regeneration.
