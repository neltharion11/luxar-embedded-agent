---
name: libopencm3_stm32f1
category: driver
mode: executable
promotion_level: draft
tags: [stm32, f1, libopencm3, hal, driver, open-source, lgpl]
---

# libopencm3 STM32F1 Driver Skill

## Description
Integrates libopencm3 open-source firmware library (LGPL v3) into an STM32F1 project as a lightweight alternative to STM32Cube HAL. Provides peripheral drivers for GPIO, RCC, USART, Timer, SPI, I2C, ADC, and NVIC/SysTick.

## Inputs
- PROJECT_NAME: project name
- MCU: STM32F103C8 (default)
- LIBOPENCM3_PATH: path to libopencm3 clone (default: driver_library/libopencm3)

## Why libopencm3
- LGPL v3 licensed (more permissive than STM32Cube)
- Lightweight: ~100KB vs Cube HAL ~30MB
- Clean, register-level API with minimal overhead
- Cross-vendor: supports STM32F0/F1/F2/F3/F4/L1/L4
- No code generator required

## Workflow

### Step 1: Ensure libopencm3 is available
Check if LIBOPENCM3_PATH exists. If not, clone and build:
  git clone https://github.com/libopencm3/libopencm3.git LIBOPENCM3_PATH
  cd LIBOPENCM3_PATH && make

### Step 2: Create libopencm3.h adapter
Create libopencm3/libopencm3.h in project with all needed peripheral includes.

### Step 3: Create linker script
Create ld/stm32f103c8.ld with 64KB FLASH at 0x08000000, 20KB RAM at 0x20000000.

### Step 4: Create Makefile with libopencm3 integration
Set OPENCM3_DIR, include libopencm3.rules.mk, set proper flags.

### Step 5: Create main.c using libopencm3 API
Use libopencm3-style peripheral access with rcc, gpio, usart, timer APIs.

## Outputs
- libopencm3/libopencm3.h adapter header
- ld/stm32f103c8.ld linker script
- Makefile configured for libopencm3
- main.c using libopencm3 API