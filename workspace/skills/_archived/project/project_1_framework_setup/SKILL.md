# Project 1 Framework Setup Skill

## Description
Sets up the complete STM32F103C8 project framework for project '1' with CMake+Ninja build system.

## Inputs
- PROJECT: 1
- MCU: STM32F103C8 (Cortex-M3, 64KB Flash, 20KB RAM)

## Workflow

### Step 1: Verify directory structure exists
- projects_root/1/Makefile (from baremetal skill)
- projects_root/1/app_main.c
- projects_root/1/startup_stm32f103xb.s
- projects_root/1/stm32f103x8.h
- projects_root/1/ld/stm32f103c8.ld

### Step 2: Create CMakeLists.txt (proper CMake build, not Makefile delegate)
Write projects_root/1/CMakeLists.txt:

```cmake
cmake_minimum_required(VERSION 3.10)
project(project_1 C ASM)

set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR cortex-m3)

set(TOOLCHAIN_PREFIX arm-none-eabi-)
set(CMAKE_C_COMPILER ${TOOLCHAIN_PREFIX}gcc)
set(CMAKE_ASM_COMPILER ${CMAKE_C_COMPILER})
set(CMAKE_OBJCOPY ${TOOLCHAIN_PREFIX}objcopy)
set(CMAKE_SIZE ${TOOLCHAIN_PREFIX}size)
set(CMAKE_OBJDUMP ${TOOLCHAIN_PREFIX}objdump)

set(COMMON_FLAGS "-mcpu=cortex-m3 -mthumb -msoft-float -DSTM32F103xB -DSTM32F1 -O0 -g -Wall -ffunction-sections -fdata-sections")
set(CMAKE_C_FLAGS "${COMMON_FLAGS} -I${CMAKE_CURRENT_SOURCE_DIR}")
set(CMAKE_ASM_FLAGS "${COMMON_FLAGS} -x assembler-with-cpp")

set(CMAKE_EXE_LINKER_FLAGS "-mcpu=cortex-m3 -mthumb -msoft-float -T${CMAKE_CURRENT_SOURCE_DIR}/ld/stm32f103c8.ld -Wl,--gc-sections -Wl,-Map=${CMAKE_PROJECT_NAME}.map -Wl,--cref")

file(GLOB_RECURSE SOURCES ${CMAKE_CURRENT_SOURCE_DIR}/*.c ${CMAKE_CURRENT_SOURCE_DIR}/*.s)
list(FILTER SOURCES EXCLUDE REGEX ".*/build/.*")

add_executable(${CMAKE_PROJECT_NAME}.elf ${SOURCES})

add_custom_command(TARGET ${CMAKE_PROJECT_NAME}.elf POST_BUILD
    COMMAND ${CMAKE_OBJCOPY} -O binary ${CMAKE_PROJECT_NAME}.bin ${CMAKE_PROJECT_NAME}.elf
    COMMAND ${CMAKE_SIZE} ${CMAKE_PROJECT_NAME}.elf
    COMMENT "Generating .bin and showing size"
)
```

### Step 3: Create cmake toolchain file
Write projects_root/1/cmake_toolchain.cmake:

```cmake
set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR cortex-m3)

set(TOOLCHAIN_DIR "C:/Users/Gugugu/Documents/Codex/LUXAR/workspace/toolchains/gcc-arm/bin")
set(CMAKE_C_COMPILER ${TOOLCHAIN_DIR}/arm-none-eabi-gcc.exe)
set(CMAKE_ASM_COMPILER ${TOOLCHAIN_DIR}/arm-none-eabi-gcc.exe)
set(CMAKE_OBJCOPY ${TOOLCHAIN_DIR}/arm-none-eabi-objcopy.exe)
set(CMAKE_SIZE ${TOOLCHAIN_DIR}/arm-none-eabi-size.exe)
set(CMAKE_OBJDUMP ${TOOLCHAIN_DIR}/arm-none-eabi-objdump.exe)

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
```

### Step 4: Configure with CMake + Ninja
```
cmake.exe -G Ninja -B build -S . -DCMAKE_TOOLCHAIN_FILE=cmake_toolchain.cmake
```

### Step 5: Build with Ninja
```
ninja.exe -C build
```

### Step 6: Verify output
Check that build/project_1.elf and build/project_1.bin exist.
