cmake_minimum_required(VERSION 3.22)

#
# This file is generated only once,
# and is not re-generated if converter is called multiple times.
#
# User is free to modify the file as much as necessary
#

# Setup compiler settings
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS ON)


# Define the build type
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Debug")
endif()

# Set the project name
set(CMAKE_PROJECT_NAME keysking_project)

# Enable compile command to ease indexing with e.g. clangd
set(CMAKE_EXPORT_COMPILE_COMMANDS TRUE)

# Core project settings
project(${CMAKE_PROJECT_NAME})
message("Build type: " ${CMAKE_BUILD_TYPE})

# Enable CMake support for ASM and C languages
enable_language(C ASM)

# Create an executable object type
add_executable(${CMAKE_PROJECT_NAME})

# Add STM32CubeMX generated sources
add_subdirectory(cmake/stm32cubemx)

# Link directories setup
target_link_directories(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user defined library search paths
)

# Add sources to executable
target_sources(${CMAKE_PROJECT_NAME} PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/App/Src/app_main.c
    ${CMAKE_CURRENT_SOURCE_DIR}/BSP/AHT20/Src/aht20.c
    ${CMAKE_CURRENT_SOURCE_DIR}/BSP/BT24/Src/dx_bt24.c
    ${CMAKE_CURRENT_SOURCE_DIR}/BSP/DRV8833/Src/drv8833.c
    ${CMAKE_CURRENT_SOURCE_DIR}/BSP/HCSR04/Src/hcsr04.c
    ${CMAKE_CURRENT_SOURCE_DIR}/BSP/NTC/Src/ntc.c
    ${CMAKE_CURRENT_SOURCE_DIR}/BSP/OLED/Src/ch1116.c
    ${CMAKE_CURRENT_SOURCE_DIR}/BSP/OLED/Src/oled_font_6x8.c
    ${CMAKE_CURRENT_SOURCE_DIR}/BSP/SG90/Src/sg90.c
    ${CMAKE_CURRENT_SOURCE_DIR}/BSP/WS2812/Src/ws2812.c
)

set_source_files_properties(
    ${CMAKE_CURRENT_SOURCE_DIR}/BSP/WS2812/Src/ws2812.c
    PROPERTIES COMPILE_OPTIONS "-O3"
)

# Add include paths
target_include_directories(${CMAKE_PROJECT_NAME} PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/App/Inc
    ${CMAKE_CURRENT_SOURCE_DIR}/BSP/AHT20/Inc
    ${CMAKE_CURRENT_SOURCE_DIR}/BSP/BT24/Inc
    ${CMAKE_CURRENT_SOURCE_DIR}/BSP/DRV8833/Inc
    ${CMAKE_CURRENT_SOURCE_DIR}/BSP/HCSR04/Inc
    ${CMAKE_CURRENT_SOURCE_DIR}/BSP/NTC/Inc
    ${CMAKE_CURRENT_SOURCE_DIR}/BSP/OLED/Inc
    ${CMAKE_CURRENT_SOURCE_DIR}/BSP/SG90/Inc
    ${CMAKE_CURRENT_SOURCE_DIR}/BSP/WS2812/Inc
)

# Add project symbols (macros)
target_compile_definitions(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user defined symbols
)

# Remove wrong libob.a library dependency when using cpp files
list(REMOVE_ITEM CMAKE_C_IMPLICIT_LINK_LIBRARIES ob)

# Add linked libraries
target_link_libraries(${CMAKE_PROJECT_NAME}
    stm32cubemx

    # Add user defined libraries
)

set(STM32CUBE_PROGRAMMER_HINTS
    "$ENV{LOCALAPPDATA}/stm32cube/bundles/programmer"
    "C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin"
    "C:/ST/STM32CubeProgrammer/bin"
)

find_program(STM32_PROGRAMMER_CLI
    NAMES STM32_Programmer_CLI STM32_Programmer_CLI.exe
    HINTS ${STM32CUBE_PROGRAMMER_HINTS}
    PATH_SUFFIXES
        bin
        2.22.0+st.1/bin
)
find_program(STFLASH_TOOL st-flash)

add_custom_command(TARGET ${CMAKE_PROJECT_NAME} POST_BUILD
    COMMAND ${CMAKE_OBJCOPY} -O ihex $<TARGET_FILE:${CMAKE_PROJECT_NAME}> ${CMAKE_PROJECT_NAME}.hex
    COMMAND ${CMAKE_OBJCOPY} -O binary $<TARGET_FILE:${CMAKE_PROJECT_NAME}> ${CMAKE_PROJECT_NAME}.bin
    COMMAND ${CMAKE_SIZE} $<TARGET_FILE:${CMAKE_PROJECT_NAME}>
)

if(STM32_PROGRAMMER_CLI)
    message(STATUS "Using STM32_Programmer_CLI: ${STM32_PROGRAMMER_CLI}")
    add_custom_target(flash
        COMMAND "${STM32_PROGRAMMER_CLI}" -c port=SWD -w $<TARGET_FILE_DIR:${CMAKE_PROJECT_NAME}>/${CMAKE_PROJECT_NAME}.elf -v -rst
        DEPENDS ${CMAKE_PROJECT_NAME}
        USES_TERMINAL
    )
elseif(STFLASH_TOOL)
    add_custom_target(flash
        COMMAND ${STFLASH_TOOL} --reset write $<TARGET_FILE_DIR:${CMAKE_PROJECT_NAME}>/${CMAKE_PROJECT_NAME}.bin 0x08000000
        DEPENDS ${CMAKE_PROJECT_NAME}
        USES_TERMINAL
    )
endif()
