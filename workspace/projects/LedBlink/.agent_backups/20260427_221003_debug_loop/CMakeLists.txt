cmake_minimum_required(VERSION 3.20)
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
    STM32_TARGET_FAMILY="F1"
    STM32F1xx
    USE_HAL_DRIVER
)
