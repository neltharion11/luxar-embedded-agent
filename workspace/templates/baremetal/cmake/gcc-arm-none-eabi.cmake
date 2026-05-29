set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR cortex-m3)
set(TOOLCHAIN_PREFIX arm-none-eabi-)

# Resolve toolchain root from this file's location
get_filename_component(TOOLCHAIN_DIR "${CMAKE_CURRENT_LIST_FILE}" PATH)
get_filename_component(TOOLCHAIN_DIR "${TOOLCHAIN_DIR}" PATH)
get_filename_component(TOOLCHAIN_DIR "${TOOLCHAIN_DIR}" PATH)
get_filename_component(WORKSPACE_DIR "${TOOLCHAIN_DIR}" PATH)

set(TOOLCHAIN_BIN "${WORKSPACE_DIR}/toolchains/gcc-arm/bin")
set(CMAKE_C_COMPILER "${TOOLCHAIN_BIN}/${TOOLCHAIN_PREFIX}gcc.exe")
set(CMAKE_ASM_COMPILER "${TOOLCHAIN_BIN}/${TOOLCHAIN_PREFIX}gcc.exe")
set(CMAKE_OBJCOPY "${TOOLCHAIN_BIN}/${TOOLCHAIN_PREFIX}objcopy.exe")
set(CMAKE_SIZE "${TOOLCHAIN_BIN}/${TOOLCHAIN_PREFIX}size.exe")
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
set(TARGET_FLAGS "-mcpu=cortex-m3 -mthumb -msoft-float")
set(CMAKE_C_FLAGS "${TARGET_FLAGS} -Wall -ffunction-sections -fdata-sections -g -O0")
set(CMAKE_ASM_FLAGS "${TARGET_FLAGS} -x assembler-with-cpp")
# No linker flags ? CMakeLists.txt handles everything via target_link_options
