# LUXAR

**Current release:** `v0.2.3`

LUXAR is an STM32-first embedded AI agent toolkit for firmware planning, project generation, review, build, flash, monitor, and hardware bring-up workflows.

It is designed around a Codex-style workflow: describe what you want in natural language, then let LUXAR route the task through runtime skills, workspace tools, project templates, hardware evidence collection, and review gates.

> English documentation comes first. A Chinese guide is available in [中文说明](#中文说明).

## What v0.2.3 Provides

- A source-installable Python CLI: `luxar`.
- A local Web UI and API server: `luxar start`.
- Runtime primitives for tasks, skills, memory, workspace inspection, build, flash, monitor, and static project probing.
- STM32 project flows for:
  - `stm32cubemx`: CubeMX-owned generated code and toolchain files, with LUXAR keeping only `App/` and `BSP/` as user integration folders.
  - `stm32firmware`: LUXAR-native CMake templates for `baremetal` and `freertos`, including VSCode build/flash/debug configuration.
- STM32Firmware template support for multiple STM32Cube firmware package families instead of hardcoded F103-only startup/linker assets.
- Hardware gate support through runtime/API tools:
  - `workspace_hw_probe` for ST-Link/SWD readback evidence.
  - `workspace_uart_gate` for explicit UART gate firmware generation after the user confirms USART, TX/RX pins, and baudrate.
- A quieter chat UI that hides successful tool noise and folds edited files.

## Requirements

Minimum:

- Windows is the primary tested environment for `v0.2.3`.
- Python `3.11+`.
- Git.
- An OpenAI-compatible or configured LLM provider. The default config uses DeepSeek through `DEEPSEEK_API_KEY`.

For STM32 hardware workflows:

- STM32CubeProgrammer or bundled `STM32_Programmer_CLI`.
- ST-Link or compatible probe for flashing and SWD evidence.
- A serial adapter or ST-Link VCP for UART monitor evidence.
- STM32Cube firmware packages placed under `workspace/firmware_library/stm32/`.

Optional:

- VSCode.
- Cortex-Debug or STM32-related VSCode debugging extension.
- ST-Link GDB Server if you want VSCode F5 debug, not only CLI flash.

## Install From Source

LUXAR `v0.2.3` does **not** currently provide a PyPI package, one-click installer, or binary GitHub Release asset. The supported installation method is a source checkout with a Python virtual environment.

```powershell
git clone https://github.com/neltharion11/luxar-embedded-agent.git
cd luxar-embedded-agent

py -m venv .venv
.\.venv\Scripts\Activate.ps1

py -m pip install --upgrade pip
py -m pip install -e .

luxar --help
```

Optional PDF/OCR support:

```powershell
py -m pip install -e ".[pdf]"
```

If you run from a source checkout, LUXAR anchors its workspace to the repository root automatically. For a custom location, set one of:

```powershell
$env:LUXAR_ROOT="D:\Tools\LUXAR"
$env:LUXAR_CONFIG="D:\Tools\LUXAR\config\luxar.yaml"
```

## Start the Web UI and API

```powershell
luxar start --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000
```

Stop the server:

```powershell
luxar stop
```

## CLI Quick Start

Explain the runtime:

```powershell
luxar run --explain --task "Explain the LUXAR runtime"
```

Run a task:

```powershell
luxar run --project DirectF1C --task "Inspect the project and explain the next build step"
```

Inspect workspace state:

```powershell
luxar workspace inspect
```

Build, flash, and monitor a project:

```powershell
luxar workspace build --project DirectF1C
luxar workspace flash --project DirectF1C --probe stlink
luxar workspace monitor --project DirectF1C --port COM3 --baudrate 115200
```

Run a static project probe, such as UART/I2C/SPI configuration inspection:

```powershell
luxar workspace probe --project DirectF1C --probe-type uart
```

Manage runtime skills:

```powershell
luxar skills list
luxar skills view init_project_framework
```

Use durable memory and lessons:

```powershell
luxar memory read
luxar memory search "stm32 uart gate"
luxar memory lessons
```

## Workspace Layout and Custom Files

LUXAR stores local projects, firmware libraries, toolchains, skills, drivers, memory, and templates under `workspace/`.

```text
workspace/
  docs/
  driver_library/
  firmware_library/
    stm32/
      STM32Cube_FW_F1/
      STM32Cube_FW_F1_V1.8.7/
  lessons/
  memory/
  projects/
  prompts/
  skills/
  templates/
  toolchains/
```

Place custom files here:

| Purpose | Path |
| --- | --- |
| STM32Cube firmware package | `workspace/firmware_library/stm32/STM32Cube_FW_<family>[_version]/` |
| CMake | `workspace/toolchains/cmake/` |
| ARM GCC | `workspace/toolchains/gcc-arm/` |
| Ninja | `workspace/toolchains/ninja/` |
| STM32CubeProgrammer CLI | `workspace/toolchains/programmer/` |
| OpenOCD | `workspace/toolchains/openocd/` |
| User projects | `workspace/projects/` |
| Reusable drivers | `workspace/driver_library/<chip-or-driver>/` |
| Runtime skills | `workspace/skills/<category>/<skill-name>/SKILL.md` |
| Documents and datasheets | `workspace/docs/` |
| Project templates | `workspace/templates/` |

Toolchain resolution order is:

1. Explicit path in `config/luxar.yaml`.
2. Bundled binary under `workspace/toolchains/`.
3. System `PATH`.

## STM32Cube Firmware Packages

For `stm32firmware` projects, LUXAR needs STM32Cube firmware package content.

Expected examples:

```text
workspace/firmware_library/stm32/STM32Cube_FW_F1/
workspace/firmware_library/stm32/STM32Cube_FW_F1_V1.8.7/
workspace/firmware_library/stm32/STM32Cube_FW_F4_Vx.y.z/
```

The package should contain the normal ST layout:

```text
Drivers/
Middlewares/
Projects/
Utilities/
```

`v0.2.3` can infer the needed package family from the MCU name for STM32Firmware templates. If no matching package is present, project creation should fail with an explicit package-missing error.

## Project Platforms

### `stm32cubemx`

Use this platform when you want STM32CubeMX to own generated firmware structure.

Important rule:

- LUXAR keeps only `App/` and `BSP/` in the CubeMX template.
- CubeMX generates `Core/`, `Drivers/`, `.ioc`, CMake/toolchain files, startup files, linker scripts, and peripheral initialization.
- For CubeMX projects, do not let LUXAR rewrite CubeMX toolchain configuration casually.
- A newly created CubeMX project should not be built until CubeMX has generated code.

### `stm32firmware`

Use this platform when you want LUXAR-native project templates without making the project a CubeMX project.

Available systems:

- `baremetal`
- `freertos`

The templates include:

- CMake presets.
- VSCode `.vscode/tasks.json`, `launch.json`, and `settings.json`.
- Stable Debug output paths.
- `.elf`, `.bin`, and `.hex` generation.
- Flash task support through `STM32_Programmer_CLI`.
- F5 debug configuration through Cortex-Debug/ST-Link when the required VSCode tools are installed.

## Hardware Gate and Evidence

Hardware evidence is separated from static project probes.

- `workspace_probe` is for static project configuration inspection, such as UART/I2C/SPI config evidence.
- `workspace_hw_probe` is for real ST-Link/SWD hardware evidence, including ST-Link serial, target voltage, Device ID/name, and flash readback.

UART monitor gate firmware is not generated silently. The user must confirm:

- USART instance, such as `USART2`.
- TX/RX pins, such as `PA2/PA3`.
- Baudrate, such as `115200`.

After confirmation, the runtime/API tool `workspace_uart_gate` can generate gate firmware that periodically prints:

```text
LUXAR_HW_GATE_OK
```

For `v0.2.3`, the validated hardware gate path was:

```text
build -> flash -> workspace_hw_probe -> monitor
```

## Configuration

Main configuration file:

```text
config/luxar.yaml
```

Common settings:

- `llm.api_key_env`: environment variable for the LLM provider key.
- `toolchains.*`: explicit toolchain binary paths.
- `stm32.firmware_package`: optional default firmware package override.
- `platform.default_platform`: default project platform.
- `platform.default_runtime`: default project system.
- `review.*`: review gate and auto-fix behavior.

Example API key setup:

```powershell
$env:DEEPSEEK_API_KEY="your-api-key"
```

## Current Limitations

- PyPI distribution is not available yet.
- A one-click installer is not available yet.
- GitHub Releases currently use Git tags; no binary release assets are provided by default.
- Firmware packages and toolchains can be large and may require manual placement.
- STM32 hardware evidence requires actual connected hardware and matching serial wiring.
- VSCode F5 debug depends on local VSCode extensions and available ST-Link GDB Server support.

## Planned Distribution

Future releases should add a downloadable source bundle layout:

```text
LUXAR-v0.2.x/
  src/
  config/
  workspace/
  README.md
  install.ps1
  start.ps1
```

Planned installer behavior:

- Create `.venv`.
- Install LUXAR from a wheel or editable source checkout.
- Verify `luxar --help`.
- Print the expected workspace paths for firmware packages, toolchains, projects, docs, drivers, and skills.

Planned PyPI package:

- Install the CLI and Python package.
- Keep firmware packages and toolchains external/manual because of size and licensing.

Planned GitHub Release assets:

- Source bundle.
- Optional Windows toolchain bundle, only if redistribution licenses allow it.

## Development Checks

Run unit tests:

```powershell
py -m pytest tests\unit -q --basetemp .pytest_tmp
```

Check CLI help:

```powershell
py -m luxar.cli --help
py -m luxar.cli workspace --help
```

## 中文说明

**当前版本：** `v0.2.3`

LUXAR 是一个面向 STM32 的嵌入式 AI Agent 工具包，用于固件规划、工程生成、代码审查、编译、烧录、串口监控和硬件门禁验证。

它的目标是提供类似 Codex 的单入口体验：用户用自然语言描述目标，LUXAR 通过 runtime skills、workspace tools、项目模板、硬件证据和审查门禁来完成工程任务。

## v0.2.3 提供什么

- 可从源码安装的 Python CLI：`luxar`。
- 本地 Web UI 和 API 服务：`luxar start`。
- runtime 能力：任务运行、技能、记忆、工作区检查、build、flash、monitor、静态工程 probe。
- STM32 项目流：
  - `stm32cubemx`：CubeMX 负责生成代码和工具链文件，LUXAR 只保留 `App/` 和 `BSP/` 作为用户集成目录。
  - `stm32firmware`：LUXAR-native CMake 模板，支持 `baremetal` 和 `freertos`，并带 VSCode 编译、烧录、调试配置。
- STM32Firmware 模板不再硬编码 F103 启动文件/链接脚本，可根据 MCU 推断 STM32Cube 固件包家族。
- 硬件门禁 runtime/API 工具：
  - `workspace_hw_probe`：读取 ST-Link/SWD 硬件证据。
  - `workspace_uart_gate`：在用户确认 USART、TX/RX、波特率后生成 UART 门禁固件。
- 更安静的聊天界面：隐藏成功工具噪音，折叠已编辑文件。

## 环境要求

最低要求：

- `v0.2.3` 主要在 Windows 环境测试。
- Python `3.11+`。
- Git。
- 已配置的 LLM provider。默认配置使用 DeepSeek，并读取 `DEEPSEEK_API_KEY`。

STM32 硬件流程需要：

- STM32CubeProgrammer 或 bundled `STM32_Programmer_CLI`。
- ST-Link 或兼容调试器。
- 串口适配器或 ST-Link VCP。
- 放在 `workspace/firmware_library/stm32/` 下的 STM32Cube 固件包。

可选：

- VSCode。
- Cortex-Debug 或 STM32 相关 VSCode 扩展。
- 如果需要 VSCode F5 调试，还需要可用的 ST-Link GDB Server。

## 从源码安装

LUXAR `v0.2.3` 当前 **没有** PyPI 包、一键安装器或 GitHub Release 二进制资产。当前支持的安装方式是源码 clone + Python 虚拟环境 + editable install。

```powershell
git clone https://github.com/neltharion11/luxar-embedded-agent.git
cd luxar-embedded-agent

py -m venv .venv
.\.venv\Scripts\Activate.ps1

py -m pip install --upgrade pip
py -m pip install -e .

luxar --help
```

可选 PDF/OCR 能力：

```powershell
py -m pip install -e ".[pdf]"
```

如果从源码目录运行，LUXAR 会自动以仓库根目录作为工作区根。也可以手动指定：

```powershell
$env:LUXAR_ROOT="D:\Tools\LUXAR"
$env:LUXAR_CONFIG="D:\Tools\LUXAR\config\luxar.yaml"
```

## 启动 Web UI 和 API

```powershell
luxar start --host 127.0.0.1 --port 8000
```

打开：

```text
http://127.0.0.1:8000
```

停止服务：

```powershell
luxar stop
```

## CLI 快速使用

解释 runtime：

```powershell
luxar run --explain --task "Explain the LUXAR runtime"
```

执行任务：

```powershell
luxar run --project DirectF1C --task "Inspect the project and explain the next build step"
```

检查工作区：

```powershell
luxar workspace inspect
```

编译、烧录、监控：

```powershell
luxar workspace build --project DirectF1C
luxar workspace flash --project DirectF1C --probe stlink
luxar workspace monitor --project DirectF1C --port COM3 --baudrate 115200
```

静态工程探测：

```powershell
luxar workspace probe --project DirectF1C --probe-type uart
```

技能管理：

```powershell
luxar skills list
luxar skills view init_project_framework
```

记忆和经验：

```powershell
luxar memory read
luxar memory search "stm32 uart gate"
luxar memory lessons
```

## 工作区与自定义文件放置

LUXAR 将本地工程、固件库、工具链、技能、驱动、记忆和模板放在 `workspace/` 下。

```text
workspace/
  docs/
  driver_library/
  firmware_library/
    stm32/
      STM32Cube_FW_F1/
      STM32Cube_FW_F1_V1.8.7/
  lessons/
  memory/
  projects/
  prompts/
  skills/
  templates/
  toolchains/
```

自定义文件放置位置：

| 用途 | 路径 |
| --- | --- |
| STM32Cube 固件包 | `workspace/firmware_library/stm32/STM32Cube_FW_<family>[_version]/` |
| CMake | `workspace/toolchains/cmake/` |
| ARM GCC | `workspace/toolchains/gcc-arm/` |
| Ninja | `workspace/toolchains/ninja/` |
| STM32CubeProgrammer CLI | `workspace/toolchains/programmer/` |
| OpenOCD | `workspace/toolchains/openocd/` |
| 用户工程 | `workspace/projects/` |
| 可复用驱动 | `workspace/driver_library/<chip-or-driver>/` |
| runtime 技能 | `workspace/skills/<category>/<skill-name>/SKILL.md` |
| 文档和数据手册 | `workspace/docs/` |
| 工程模板 | `workspace/templates/` |

工具链查找顺序：

1. `config/luxar.yaml` 中的显式路径。
2. `workspace/toolchains/` 下的 bundled binary。
3. 系统 `PATH`。

## STM32Cube 固件包

`stm32firmware` 工程需要 STM32Cube 固件包。

示例：

```text
workspace/firmware_library/stm32/STM32Cube_FW_F1/
workspace/firmware_library/stm32/STM32Cube_FW_F1_V1.8.7/
workspace/firmware_library/stm32/STM32Cube_FW_F4_Vx.y.z/
```

固件包应包含 ST 标准结构：

```text
Drivers/
Middlewares/
Projects/
Utilities/
```

`v0.2.3` 会根据 MCU 名称推断需要的 STM32Cube 固件包家族。如果缺少匹配包，创建工程时会明确报错。

## 平台说明

### `stm32cubemx`

适合希望 STM32CubeMX 负责生成固件结构的项目。

重要规则：

- LUXAR 的 CubeMX 模板只保留 `App/` 和 `BSP/`。
- `Core/`、`Drivers/`、`.ioc`、CMake/toolchain 文件、启动文件、链接脚本和外设初始化都由 CubeMX 生成。
- CubeMX 平台下不要让 LUXAR 随意改动 CubeMX 的工具链配置。
- 刚创建的 CubeMX 空工程不能直接 build，必须先由 CubeMX 生成代码。

### `stm32firmware`

适合使用 LUXAR-native 模板，不把项目变成 CubeMX 工程。

支持系统：

- `baremetal`
- `freertos`

模板包含：

- CMake presets。
- VSCode `.vscode/tasks.json`、`launch.json`、`settings.json`。
- 稳定的 Debug 输出路径。
- `.elf`、`.bin`、`.hex` 产物生成。
- 通过 `STM32_Programmer_CLI` 的烧录任务。
- 在安装相关 VSCode 工具后，通过 Cortex-Debug/ST-Link 进行 F5 调试。

## 硬件门禁和证据

硬件证据和静态工程 probe 是分开的。

- `workspace_probe`：静态工程配置探测，例如 UART/I2C/SPI 配置证据。
- `workspace_hw_probe`：真实 ST-Link/SWD 硬件证据，包括 ST-Link 序列号、目标电压、Device ID/名称和 flash readback。

UART monitor gate 不会静默生成。用户必须确认：

- USART 实例，例如 `USART2`。
- TX/RX 引脚，例如 `PA2/PA3`。
- 波特率，例如 `115200`。

确认后，runtime/API 工具 `workspace_uart_gate` 可以生成周期输出以下内容的门禁固件：

```text
LUXAR_HW_GATE_OK
```

`v0.2.3` 已验证的硬件门禁路径：

```text
build -> flash -> workspace_hw_probe -> monitor
```

## 配置

主配置文件：

```text
config/luxar.yaml
```

常见配置：

- `llm.api_key_env`：LLM provider API key 的环境变量名。
- `toolchains.*`：显式工具链路径。
- `stm32.firmware_package`：默认固件包覆盖项。
- `platform.default_platform`：默认平台。
- `platform.default_runtime`：默认系统。
- `review.*`：审查门禁和自动修复行为。

设置 API key 示例：

```powershell
$env:DEEPSEEK_API_KEY="your-api-key"
```

## 当前限制

- 尚未发布 PyPI 包。
- 尚未提供一键安装器。
- GitHub Releases 当前主要是 Git tag，没有默认二进制资产。
- 固件包和工具链体积较大，可能需要手动放置。
- STM32 硬件证据需要真实硬件和正确串口接线。
- VSCode F5 调试依赖本机 VSCode 扩展和 ST-Link GDB Server。

## 下载安装规划

未来版本计划提供下载包结构：

```text
LUXAR-v0.2.x/
  src/
  config/
  workspace/
  README.md
  install.ps1
  start.ps1
```

计划中的 `install.ps1`：

- 创建 `.venv`。
- 从 wheel 或源码安装 LUXAR。
- 验证 `luxar --help`。
- 打印固件包、工具链、项目、文档、驱动、技能等工作区路径。

计划中的 PyPI 包：

- 只安装 CLI 和 Python 包。
- 固件包和工具链因为体积和许可证原因保持外部/手动管理。

计划中的 GitHub Release assets：

- 源码包。
- 如果许可证允许，提供可选 Windows 工具链包。

## 开发检查

运行单元测试：

```powershell
py -m pytest tests\unit -q --basetemp .pytest_tmp
```

检查 CLI：

```powershell
py -m luxar.cli --help
py -m luxar.cli workspace --help
```
