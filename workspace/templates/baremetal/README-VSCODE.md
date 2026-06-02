# VSCode build and flash

This LUXAR firmware template is a CMake project. Use VSCode CMake Tools for
builds and Cortex-Debug for F5 ST-Link debugging.

LUXAR provides GCC, GDB, Ninja, and STM32_Programmer_CLI under
`workspace/toolchains`. The `LUXAR: flash` task can program the board with
STM32_Programmer_CLI even when a GDB server is not installed.

F5 debugging requires a VSCode debug extension that supports `cortex-debug` and
an ST-Link GDB server available to that extension.
