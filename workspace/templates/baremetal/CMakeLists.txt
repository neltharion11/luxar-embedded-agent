cmake_minimum_required(VERSION 3.22)
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Debug")
endif()

set(CMAKE_PROJECT_NAME {PROJECT_NAME})
project(${CMAKE_PROJECT_NAME} C ASM)
message("Build type: ${CMAKE_BUILD_TYPE}")

set(FW_LIB "{STM32_FIRMWARE_PACKAGE}" CACHE PATH "STM32Cube firmware package")
set(HAL_DRIVER "${FW_LIB}/Drivers/{STM32_HAL_DRIVER_DIR}")
set(CMSIS_DEVICE "${FW_LIB}/Drivers/CMSIS/Device/ST/{STM32_CMSIS_DEVICE_DIR}")
set(CMSIS_CORE "${FW_LIB}/Drivers/CMSIS/Include")

add_executable(${CMAKE_PROJECT_NAME})

target_sources(${CMAKE_PROJECT_NAME} PRIVATE
    Core/Src/main.c
    Core/Src/{STM32_SYSTEM_FILE}
    Core/Src/stm32f1xx_it.c
    Core/Src/stm32f1xx_hal_msp.c
    App/Src/app_main.c
    {STM32_STARTUP_FILE}
    ${HAL_DRIVER}/Src/stm32{STM32_FAMILY_LOWER}xx_hal.c
    ${HAL_DRIVER}/Src/stm32{STM32_FAMILY_LOWER}xx_hal_cortex.c
    ${HAL_DRIVER}/Src/stm32{STM32_FAMILY_LOWER}xx_hal_rcc.c
    ${HAL_DRIVER}/Src/stm32{STM32_FAMILY_LOWER}xx_hal_rcc_ex.c
    ${HAL_DRIVER}/Src/stm32{STM32_FAMILY_LOWER}xx_hal_gpio.c
    ${HAL_DRIVER}/Src/stm32{STM32_FAMILY_LOWER}xx_hal_gpio_ex.c
    ${HAL_DRIVER}/Src/stm32{STM32_FAMILY_LOWER}xx_hal_flash.c
    ${HAL_DRIVER}/Src/stm32{STM32_FAMILY_LOWER}xx_hal_flash_ex.c
    ${HAL_DRIVER}/Src/stm32{STM32_FAMILY_LOWER}xx_hal_dma.c
    ${HAL_DRIVER}/Src/stm32{STM32_FAMILY_LOWER}xx_hal_exti.c
    ${HAL_DRIVER}/Src/stm32{STM32_FAMILY_LOWER}xx_hal_pwr.c
)

target_include_directories(${CMAKE_PROJECT_NAME} PRIVATE
    Core/Inc
    App/Inc
    ${HAL_DRIVER}/Inc
    ${HAL_DRIVER}/Inc/Legacy
    ${CMSIS_DEVICE}/Include
    ${CMSIS_CORE}
)

set(LINKER_SCRIPT "${CMAKE_CURRENT_SOURCE_DIR}/{STM32_LINKER_SCRIPT}" CACHE FILEPATH "Linker script")
target_link_options(${CMAKE_PROJECT_NAME} PRIVATE
    -T${LINKER_SCRIPT}
    --specs=nano.specs
    --specs=nosys.specs
    -Wl,-Map=${CMAKE_PROJECT_NAME}.map
    -Wl,--gc-sections
    -Wl,--print-memory-usage
)

target_compile_definitions(${CMAKE_PROJECT_NAME} PRIVATE
    {STM32_DEVICE_DEFINE}
    USE_HAL_DRIVER
)

target_link_libraries(${CMAKE_PROJECT_NAME} PRIVATE
    ${TOOLCHAIN_LINK_LIBRARIES}
)

add_custom_command(TARGET ${CMAKE_PROJECT_NAME} POST_BUILD
    COMMAND ${CMAKE_OBJCOPY} -O binary $<TARGET_FILE:${CMAKE_PROJECT_NAME}> ${CMAKE_PROJECT_NAME}.bin
    COMMAND ${CMAKE_OBJCOPY} -O ihex $<TARGET_FILE:${CMAKE_PROJECT_NAME}> ${CMAKE_PROJECT_NAME}.hex
)
