cmake_minimum_required(VERSION 3.22)
project({PROJECT_NAME} C ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
enable_language(C ASM)

set(FW_LIB "C:/Users/Gugugu/Documents/Codex/LUXAR/workspace/firmware_library/stm32/STM32Cube_FW_F1_V1.8.7")
set(HAL_DRIVER "${FW_LIB}/Drivers/STM32F1xx_HAL_Driver")
set(CMSIS_DEVICE "${FW_LIB}/Drivers/CMSIS/Device/ST/STM32F1xx")
set(CMSIS_CORE "${FW_LIB}/Drivers/CMSIS/Core/Include")

add_executable(${CMAKE_PROJECT_NAME}.elf)

target_sources(${CMAKE_PROJECT_NAME} PRIVATE
    Core/Src/main.c
    Core/Src/system_stm32f1xx.c
    Core/Src/stm32f1xx_it.c
    Core/Src/stm32f1xx_hal_msp.c
    App/Src/app_main.c
    startup_stm32f103xb.s
    ${HAL_DRIVER}/Src/stm32f1xx_hal.c
    ${HAL_DRIVER}/Src/stm32f1xx_hal_cortex.c
    ${HAL_DRIVER}/Src/stm32f1xx_hal_rcc.c
    ${HAL_DRIVER}/Src/stm32f1xx_hal_rcc_ex.c
    ${HAL_DRIVER}/Src/stm32f1xx_hal_gpio.c
    ${HAL_DRIVER}/Src/stm32f1xx_hal_gpio_ex.c
    ${HAL_DRIVER}/Src/stm32f1xx_hal_flash.c
    ${HAL_DRIVER}/Src/stm32f1xx_hal_flash_ex.c
    ${HAL_DRIVER}/Src/stm32f1xx_hal_dma.c
    ${HAL_DRIVER}/Src/stm32f1xx_hal_exti.c
    ${HAL_DRIVER}/Src/stm32f1xx_hal_pwr.c
)

target_include_directories(${CMAKE_PROJECT_NAME} PRIVATE
    Core/Inc
    App/Inc
    ${HAL_DRIVER}/Inc
    ${HAL_DRIVER}/Inc/Legacy
    ${CMSIS_DEVICE}/Include
    ${CMSIS_CORE}
)

target_link_options(${CMAKE_PROJECT_NAME} PRIVATE
    -T${CMAKE_CURRENT_SOURCE_DIR}/link.ld
    --specs=nano.specs
    --specs=nosys.specs
    -Wl,-Map=${CMAKE_PROJECT_NAME}.map
)

target_compile_definitions(${CMAKE_PROJECT_NAME} PRIVATE
    STM32F103xB
    USE_HAL_DRIVER
)

add_custom_command(TARGET ${CMAKE_PROJECT_NAME} POST_BUILD
    COMMAND ${CMAKE_OBJCOPY} -O binary $<TARGET_FILE:${CMAKE_PROJECT_NAME}> ${CMAKE_PROJECT_NAME}.bin
    COMMAND ${CMAKE_OBJCOPY} -O ihex $<TARGET_FILE:${CMAKE_PROJECT_NAME}> ${CMAKE_PROJECT_NAME}.hex
)
