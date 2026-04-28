# Bundled Toolchains

Place bundled tool binaries here so `BoardSmith` can find them without relying on system `PATH`.

Expected layout:

```text
toolchains/
├─ cmake/
│  └─ bin/
│     ├─ cmake.exe
│     └─ cmake
├─ openocd/
│  └─ bin/
│     ├─ openocd.exe
│     └─ openocd
└─ gcc-arm/
   └─ bin/
      ├─ arm-none-eabi-gcc.exe
      └─ arm-none-eabi-gcc
```

Resolution order:

1. Explicit path from `config/luxar.yaml`
2. Bundled binary under `toolchains/`
3. System `PATH`

This directory is the default distribution target for agent-managed toolchains.


