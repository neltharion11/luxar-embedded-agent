from __future__ import annotations

import shutil
from pathlib import Path

from luxar.models.schemas import DriverMetadata
from luxar.models.schemas import ProjectConfig


APP_MAIN_H = """#ifndef APP_MAIN_H
#define APP_MAIN_H

void app_main_init(void);
void app_main_loop(void);

#endif /* APP_MAIN_H */
"""


APP_MAIN_C = """#include "app_main.h"

void app_main_init(void)
{
    /* TODO(stage-2): add user initialization code. */
}

void app_main_loop(void)
{
    /* TODO(stage-2): add user loop code. */
}
"""


CMAKELISTS_TXT = """cmake_minimum_required(VERSION 3.20)
project(luxar_app C)

file(GLOB APP_SOURCES CONFIGURE_DEPENDS
    "App/Src/*.c"
    "App/Drivers/*/Src/*.c"
)

add_library(app STATIC ${APP_SOURCES})

target_include_directories(app PUBLIC
    App/Inc
)

if(EXISTS "${CMAKE_SOURCE_DIR}/App/Drivers")
    file(GLOB DRIVER_INCLUDE_DIRS CONFIGURE_DEPENDS "App/Drivers/*/Inc")
    foreach(dir ${DRIVER_INCLUDE_DIRS})
        target_include_directories(app PUBLIC ${dir})
    endforeach()
endif()
"""


FIRMWARE_MAIN_C = """#include "app_main.h"

int main(void)
{
    app_main_init();

    while (1) {
        app_main_loop();
    }

    return 0;
}
"""


HAL_CONF_H = """#ifndef STM32_HAL_CONF_H
#define STM32_HAL_CONF_H

/* TODO(stage-2): tailor HAL module enables to the selected STM32 family. */
#define HAL_MODULE_ENABLED

#endif /* STM32_HAL_CONF_H */
"""


TOOLCHAIN_CMAKE = """set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR arm)

set(CMAKE_C_COMPILER arm-none-eabi-gcc)
set(CMAKE_CXX_COMPILER arm-none-eabi-g++)
set(CMAKE_ASM_COMPILER arm-none-eabi-gcc)
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
set(CMAKE_EXECUTABLE_SUFFIX ".elf")

set(TARGET_FLAGS "-mcpu=cortex-m3 -mthumb")
set(CMAKE_C_FLAGS "${TARGET_FLAGS} -ffunction-sections -fdata-sections")
set(CMAKE_ASM_FLAGS "${TARGET_FLAGS} -x assembler-with-cpp")
set(CMAKE_EXE_LINKER_FLAGS "${TARGET_FLAGS} -nostartfiles --specs=nosys.specs --specs=nano.specs")
"""


STARTUP_ASM = """.syntax unified
.cpu cortex-m3
.thumb

.global g_pfnVectors
.global Reset_Handler
.global Default_Handler
.global SysTick_Handler
.type Reset_Handler, %function
.type Default_Handler, %function
.type SysTick_Handler, %function
.thumb_func

.section .isr_vector, "a", %progbits
g_pfnVectors:
    .word _estack
    .word Reset_Handler
    .word Default_Handler  /* NMI */
    .word Default_Handler  /* HardFault */
    .word Default_Handler  /* MemManage */
    .word Default_Handler  /* BusFault */
    .word Default_Handler  /* UsageFault */
    .word 0
    .word 0
    .word 0
    .word 0
    .word Default_Handler  /* SVCall */
    .word Default_Handler  /* DebugMonitor */
    .word 0
    .word Default_Handler  /* PendSV */
    .word SysTick_Handler  /* SysTick */

.section .text.Reset_Handler, "ax", %progbits
Reset_Handler:
    ldr r0, =_sidata
    ldr r1, =_sdata
    ldr r2, =_edata
1:
    cmp r1, r2
    bcs 2f
    ldr r3, [r0], #4
    str r3, [r1], #4
    b 1b
2:
    ldr r1, =_sbss
    ldr r2, =_ebss
    movs r3, #0
3:
    cmp r1, r2
    bcs 4f
    str r3, [r1], #4
    b 3b
4:
    bl SystemInit
    bl main
5:
    b 5b

.section .text.Default_Handler, "ax", %progbits
Default_Handler:
6:
    b 6b

.weak SysTick_Handler
.section .text.SysTick_Handler, "ax", %progbits
SysTick_Handler:
    b .
"""


SYSTEM_INIT_C = """#include <stdint.h>

#define RCC_BASE        0x40021000UL
#define RCC_CR          (*(volatile uint32_t *)(RCC_BASE + 0x00))
#define RCC_CR_HSION    (1UL << 0)
#define RCC_CR_HSIRDY   (1UL << 1)

void SystemInit(void)
{
    /* Enable HSI (8 MHz internal oscillator) and wait for stable */
    RCC_CR |= RCC_CR_HSION;
    while (!(RCC_CR & RCC_CR_HSIRDY)) {}

    /* SystemCoreClock = 8 MHz; no PLL, no external crystal needed */
}
"""


LINKER_SCRIPT = """ENTRY(Reset_Handler)

MEMORY
{
  FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 64K
  RAM   (xrw) : ORIGIN = 0x20000000, LENGTH = 20K
}

_estack = ORIGIN(RAM) + LENGTH(RAM);

SECTIONS
{
  .isr_vector :
  {
    . = ALIGN(4);
    KEEP(*(.isr_vector))
    . = ALIGN(4);
  } > FLASH

  .text :
  {
    . = ALIGN(4);
    *(.text*)
    *(.rodata*)
    . = ALIGN(4);
    _etext = .;
  } > FLASH

  _sidata = LOADADDR(.data);

  .data :
  {
    . = ALIGN(4);
    _sdata = .;
    *(.data*)
    . = ALIGN(4);
    _edata = .;
  } > RAM AT> FLASH

  .bss :
  {
    . = ALIGN(4);
    _sbss = .;
    *(.bss*)
    *(COMMON)
    . = ALIGN(4);
    _ebss = .;
  } > RAM

  . = ALIGN(4);
  _end = .;
}
"""


FIRMWARE_CMAKELISTS = """cmake_minimum_required(VERSION 3.20)
set(CMAKE_TOOLCHAIN_FILE "${CMAKE_SOURCE_DIR}/cmake/toolchain-arm-none-eabi.cmake" CACHE STRING "Toolchain file")
project(stm32_firmware_app C ASM)

set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -T${CMAKE_SOURCE_DIR}/cmake/stm32.ld")

file(GLOB HAL_DRIVER_SOURCES CONFIGURE_DEPENDS
    "Drivers/STM32*HAL_Driver/Src/*.c"
)

file(GLOB APP_DRIVER_SOURCES CONFIGURE_DEPENDS
    "App/Drivers/*/Src/*.c"
)

add_executable(${PROJECT_NAME}
    Core/Src/startup_stm32.s
    Core/Src/system_stm32xx.c
    Core/Src/main.c
    App/Src/app_main.c
    ${APP_DRIVER_SOURCES}
    ${HAL_DRIVER_SOURCES}
)

target_include_directories(${PROJECT_NAME} PRIVATE
    Core/Inc
    App/Inc
    Drivers/CMSIS/Include
    Drivers/STM32F1xx_HAL_Driver/Inc
)

if(EXISTS "${CMAKE_SOURCE_DIR}/Drivers/CMSIS/Core/Include")
    target_include_directories(${PROJECT_NAME} PRIVATE Drivers/CMSIS/Core/Include)
endif()

if(EXISTS "${CMAKE_SOURCE_DIR}/Drivers/CMSIS/Device/ST")
    file(GLOB DEVICE_INCLUDE_DIRS CONFIGURE_DEPENDS "Drivers/CMSIS/Device/ST/*/Include")
    foreach(dir ${DEVICE_INCLUDE_DIRS})
        target_include_directories(${PROJECT_NAME} PRIVATE ${dir})
    endforeach()
endif()

if(EXISTS "${CMAKE_SOURCE_DIR}/App/Drivers")
    file(GLOB APP_DRIVER_INCLUDE_DIRS CONFIGURE_DEPENDS "App/Drivers/*/Inc")
    foreach(dir ${APP_DRIVER_INCLUDE_DIRS})
        target_include_directories(${PROJECT_NAME} PRIVATE ${dir})
    endforeach()
endif()

target_compile_definitions(${PROJECT_NAME} PRIVATE
    STM32_TARGET_FAMILY="@STM32_FAMILY@"
    @STM32_FAMILY_DEFINE@
    USE_HAL_DRIVER
)
"""


class Assembler:
    def assemble_minimal_app(self, project: ProjectConfig) -> list[str]:
        project_dir = Path(project.path)
        created_files: list[str] = []

        app_inc = project_dir / "App" / "Inc"
        app_src = project_dir / "App" / "Src"
        app_inc.mkdir(parents=True, exist_ok=True)
        app_src.mkdir(parents=True, exist_ok=True)

        header = app_inc / "app_main.h"
        source = app_src / "app_main.c"
        cmake = project_dir / "CMakeLists.txt"

        if not header.exists():
            header.write_text(APP_MAIN_H, encoding="utf-8")
            created_files.append(str(header))
        if not source.exists():
            source.write_text(APP_MAIN_C, encoding="utf-8")
            created_files.append(str(source))
        if not cmake.exists():
            cmake.write_text(CMAKELISTS_TXT, encoding="utf-8")
            created_files.append(str(cmake))

        return created_files

    def assemble_stm32_firmware_project(
        self,
        project: ProjectConfig,
        firmware_package: str,
        stm32_family: str,
        build_context: dict | None = None,
        staged_firmware_paths: list[str] | None = None,
    ) -> list[str]:
        project_dir = Path(project.path)
        created_files: list[str] = []
        build_context = build_context or {}
        staged_firmware_paths = staged_firmware_paths or []

        for directory in (
            project_dir / "App" / "Inc",
            project_dir / "App" / "Src",
            project_dir / "Core" / "Inc",
            project_dir / "Core" / "Src",
            project_dir / "Drivers",
            project_dir / "cmake",
        ):
            directory.mkdir(parents=True, exist_ok=True)

        family_define = build_context.get("family_define", "")
        firmware_cmakelists = (
            FIRMWARE_CMAKELISTS
            .replace("@STM32_FAMILY@", stm32_family)
            .replace("@STM32_FAMILY_DEFINE@", family_define)
        )

        file_map = {
            project_dir / "App" / "Inc" / "app_main.h": APP_MAIN_H,
            project_dir / "App" / "Src" / "app_main.c": APP_MAIN_C,
            project_dir / "Core" / "Src" / "main.c": FIRMWARE_MAIN_C,
            project_dir / "Core" / "Src" / "system_stm32xx.c": SYSTEM_INIT_C,
            project_dir / "Core" / "Src" / "startup_stm32.s": STARTUP_ASM,
            project_dir / "Core" / "Inc" / "stm32_hal_conf.h": HAL_CONF_H,
            project_dir / "cmake" / "toolchain-arm-none-eabi.cmake": TOOLCHAIN_CMAKE,
            project_dir / "cmake" / "stm32.ld": LINKER_SCRIPT,
            project_dir / "CMakeLists.txt": firmware_cmakelists,
            project_dir / "FIRMWARE_PACKAGE.txt": f"{firmware_package}\n",
            project_dir / "STM32_FAMILY.txt": f"{stm32_family}\n",
        }
        for path, content in file_map.items():
            if not path.exists():
                path.write_text(content, encoding="utf-8")
                created_files.append(str(path))

        created_files.extend(staged_firmware_paths)
        return created_files

    def install_driver_records(
        self,
        project: ProjectConfig,
        drivers: list[DriverMetadata],
    ) -> list[str]:
        project_dir = Path(project.path)
        created_files: list[str] = []
        for driver in drivers:
            driver_name = driver.name.strip() or Path(driver.path).stem
            target_root = project_dir / "App" / "Drivers" / driver_name
            inc_dir = target_root / "Inc"
            src_dir = target_root / "Src"
            inc_dir.mkdir(parents=True, exist_ok=True)
            src_dir.mkdir(parents=True, exist_ok=True)

            if driver.header_path:
                source_header = Path(driver.header_path)
                if source_header.exists():
                    target_header = inc_dir / source_header.name
                    shutil.copy2(source_header, target_header)
                    created_files.append(str(target_header))

            source_source = Path(driver.source_path or driver.path)
            if source_source.exists():
                target_source = src_dir / source_source.name
                shutil.copy2(source_source, target_source)
                created_files.append(str(target_source))

        return created_files


