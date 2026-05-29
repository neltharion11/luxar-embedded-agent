---
name: init_project_framework
category: project
mode: executable
promotion_level: validated
tags: [stm32, f1, cubemx, baremetal, setup, template, routing]
---

# Init Project Framework

## Description
Routes project initialization to the correct template based on project platform.
Reads .agent_project.json to determine whether to use CubeMX or baremetal template.

## Routing Rules
- platform == "stm32cubemx" -> stm32f1_cubemx_project_setup
- runtime == "baremetal" (no CubeMX) -> stm32f1_baremetal_project_setup

## Inputs
- PROJECT_NAME: project name

## Workflow

### Step 1: Read project metadata
Read .agent_project.json from the project directory to determine platform and runtime.

### Step 2: Route to correct template
- If platform is stm32cubemx or project_mode is cubemx: use stm32f1_cubemx_project_setup
- Otherwise: use stm32f1_baremetal_project_setup

### Step 3: Execute template
The skill_execute will auto-copy the template and replace {PROJECT_NAME} placeholders.
