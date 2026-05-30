---
name: init_project_framework
category: project
mode: executable
promotion_level: validated
tags: [stm32, f1, cubemx, baremetal, freertos, setup, template, routing]
---

# Init Project Framework

## Description
Routes project initialization to the correct template based on project platform.
Reads .agent_project.json to determine whether to use CubeMX user folders, firmware baremetal, or firmware FreeRTOS template.

## Routing Rules
- platform == "stm32cubemx" -> stm32f1_cubemx_project_setup
- platform == "stm32firmware" and system/runtime == "baremetal" -> stm32f1_baremetal_project_setup
- platform == "stm32firmware" and system/runtime == "freertos" -> freertos firmware template

## Inputs
- PROJECT_NAME: project name

## Workflow

### Step 1: Read project metadata
Read .agent_project.json from the project directory to determine platform and system/runtime.

### Step 2: Route to correct template
- If platform is stm32cubemx or project_mode is cubemx: copy only App/ and BSP/. CubeMX generates every other file.
- If platform is stm32firmware and system/runtime is freertos: use freertos firmware template.
- Otherwise: use stm32f1_baremetal_project_setup.

### Step 3: Execute template
The skill_execute will auto-copy the template and replace {PROJECT_NAME} placeholders.
