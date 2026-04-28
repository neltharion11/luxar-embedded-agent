set(CMAKE_SYSTEM_NAME Generic)
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
