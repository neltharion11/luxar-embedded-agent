# STM32F1 Build Project Skill

## Description
Build an STM32F1 bare-metal project using Makefile and arm-none-eabi-gcc toolchain via a CMake adapter.

## Workflow

### Step 1: Create CMake adapter (CMakeLists.txt)
If test2/CMakeLists.txt does not exist, create it:

```cmake
cmake_minimum_required(VERSION 3.10)
project(test2 C ASM)

# This CMakeLists.txt adapts the Makefile build for workspace_build
# It uses add_custom_target to delegate to make

set(TOOLCHAIN_DIR "C:/Users/Gugugu/Documents/Codex/LUXAR/workspace/toolchains/gcc-arm/bin")
set(PROJECT_DIR "${CMAKE_CURRENT_SOURCE_DIR}")

add_custom_target(build_test2 ALL
    COMMAND ${CMAKE_COMMAND} -E echo "=== Building test2 via Makefile ==="
    COMMAND ${CMAKE_COMMAND} -E env
        "PATH=${TOOLCHAIN_DIR};$ENV{PATH}"
        make -C ${PROJECT_DIR} -j4
    WORKING_DIRECTORY ${PROJECT_DIR}
    COMMENT "Building test2 (Makefile delegate)"
)

add_custom_target(clean_test2
    COMMAND make -C ${PROJECT_DIR} clean
    WORKING_DIRECTORY ${PROJECT_DIR}
    COMMENT "Cleaning test2"
)
```

### Step 2: Run workspace_build
After creating CMakeLists.txt, run `workspace_build(project=test2)`.
