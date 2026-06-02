from __future__ import annotations

from luxar.agent.context_builder import RuntimeWorkspace
from luxar.core.config_manager import ConfigManager

_cm_instance: ConfigManager | None = None

def _get_cm() -> ConfigManager:
    global _cm_instance
    if _cm_instance is None:
        _cm_instance = ConfigManager()
    return _cm_instance
from luxar.core.project_manager import ProjectManager
from luxar.tools.build_project import run_build_project
from luxar.tools.flash_project import run_flash_project
from luxar.core.monitor_manager import MonitorManager
from luxar.tools.monitor_project import run_monitor_project
from luxar.tools.probe_project import run_probe_project


def workspace_inspect() -> dict[str, object]:
    workspace = RuntimeWorkspace.from_manager(_get_cm())
    return {"success": True, "workspace": workspace.snapshot()}



def workspace_read_file(project: str, path: str) -> dict[str, object]:
    """Read a file from a project directory."""
    if not project or not project.strip():
        return {"success": False, "error": "No project selected. Select a project in the sidebar first."}
    if not path or not path.strip():
        return {"success": False, "error": "No file path specified. Provide a relative path like Core/Src/main.c."}
    import os
    cfg_manager = _get_cm()
    ws = cfg_manager.workspace_root()
    full = (ws / project / path).resolve()
    if not full.is_relative_to(ws):
        return {"success": False, "error": "Access denied: path outside workspace"}
    if not full.exists():
        return {"success": False, "error": f"File not found: {path}"}
    if not full.is_file():
        return {"success": False, "error": f"Not a file: {path}"}
    try:
        content = full.read_text(encoding="utf-8", errors="replace")
        total = len(content)
        if total > 10000:
            content = content[:8000] + f"\n... (truncated, {total} total chars)"
        return {"success": True, "content": content, "path": str(full), "size": total}
    except Exception as exc:
        return {"success": False, "error": str(exc)}

def workspace_list_projects() -> dict[str, object]:
    import json as _json
    cfg_manager = _get_cm()
    ws = cfg_manager.workspace_root()
    projects = []
    for meta_file in sorted(ws.glob("*/.agent_project.json")):
        try:
            data = _json.loads(meta_file.read_text(encoding="utf-8"))
            # Add CubeMX initialization detection fields
            proj_dir = meta_file.parent
            data["has_ioc"] = any(proj_dir.glob("*.ioc"))
            data["has_core"] = (proj_dir / "Core").is_dir()
            projects.append(data)
        except Exception:
            projects.append({"name": meta_file.parent.name, "error": "invalid metadata"})
    return {"success": True, "projects": projects}


def workspace_create_project(
    name: str,
    mcu: str = "STM32F103C8",
    platform: str = "stm32cubemx",
    runtime: str = "baremetal",
    firmware_package: str = "",
    overwrite: bool = False,
) -> dict[str, object]:
    cfg_manager = _get_cm()
    cfg = cfg_manager.ensure_default_config()
    manager = ProjectManager(str(cfg_manager.workspace_root()))
    project_dir = cfg_manager.workspace_root() / name
    was_new_project = not project_dir.exists()
    try:
        resolved_firmware_package = firmware_package or cfg.stm32.firmware_package
        family = ""
        device_define = ""
        if platform == "stm32firmware":
            from luxar.core.firmware_library_manager import FirmwareLibraryManager

            firmware_manager = FirmwareLibraryManager(cfg_manager.firmware_library_root())
            if firmware_package:
                resolved = firmware_manager.resolve_stm32_package(firmware_package)
                if resolved is None:
                    raise FileNotFoundError(f"Firmware package not found: {firmware_package}")
            else:
                resolved = firmware_manager.resolve_stm32_package_for_mcu(mcu)
                if resolved is None:
                    expected_family = firmware_manager.infer_stm32_family(mcu)
                    expected = f"STM32Cube_FW_{expected_family}" if expected_family != "UNKNOWN" else "STM32Cube_FW_<family>"
                    raise FileNotFoundError(
                        f"No STM32Cube firmware package found for {mcu}. "
                        f"Expected {expected} under {cfg_manager.firmware_library_root() / 'stm32'}."
                    )
            profile = firmware_manager.build_stm32_profile(mcu, resolved)
            resolved_firmware_package = profile["firmware_package"]
            family = profile["family"]
            device_define = profile["device_define"]

        project = manager.create_project(
            name=name,
            mcu=mcu,
            platform=platform,
            runtime=runtime,
            project_mode="cubemx" if platform == "stm32cubemx" else "firmware",
            firmware_package=resolved_firmware_package,
            family=family,
            device_define=device_define,
            overwrite=overwrite,
        )
        # Copy template files via skill framework (skip for CubeMX — user generates via CubeMX tool)
        if platform != "stm32cubemx":
            try:
                from luxar.tools.skills_tool import skill_execute
                template_result = skill_execute("init_project_framework", category="project", project=name)
                if not template_result.get("success", False):
                    raise RuntimeError(str(template_result.get("error", "Template generation failed")))
            except Exception:
                raise
        return {"success": True, "project": project.model_dump(mode="json")}
    except FileExistsError as exc:
        return {"success": False, "error": str(exc), "detail": "Project already exists"}
    except Exception as exc:
        if was_new_project and project_dir.exists():
            import shutil

            shutil.rmtree(project_dir, ignore_errors=True)
        return {"success": False, "error": str(exc)}

def workspace_status() -> dict[str, object]:
    """Check ST-Link probe and serial port connection status."""
    stlink_connected = False
    stlink_info = ""
    serial_connected = False
    serial_port = ""

    # --- ST-Link detection ---
    import subprocess
    import shutil
    import platform

    # Method 1: USB descriptor detection via serial ports (no CLI needed)
    stlink_vids = {"0483:374b", "0483:3748", "0483:3744", "0483:374f"}
    try:
        from serial.tools import list_ports
        for p in list_ports.comports():
            hwid = (getattr(p, "hwid", "") or "").upper()
            desc = ((getattr(p, "description", "") or "") + " " + (getattr(p, "manufacturer", "") or "")).upper()
            if any(vid.upper() in hwid for vid in stlink_vids):
                stlink_connected = True
                stlink_info = f"ST-Link ({p.device})"
                break
            if "STLINK" in hwid or "ST-LINK" in hwid or "STLINK" in desc or "ST-LINK" in desc:
                stlink_connected = True
                stlink_info = f"ST-Link ({p.device})"
                break
    except Exception:
        pass

    # Method 2: STM32_Programmer_CLI or openocd
    if not stlink_connected:
        try:
            from luxar.core.toolchain_manager import ToolchainManager
            _st_cm = _get_cm()
            _st_cfg = _st_cm.ensure_default_config()
            _st_tm = ToolchainManager(config=_st_cfg, project_root=str(_st_cm.project_root()))
            programmer_cli = _st_tm.resolve_programmer_cli() or _st_tm.resolve_openocd()
        except Exception:
            programmer_cli = None
        if programmer_cli:
            try:
                if "openocd" not in programmer_cli.lower():
                    result = subprocess.run(
                        [programmer_cli, "-l", "stlink"],
                        capture_output=True, text=True, timeout=10
                    )
                    output = (result.stdout + result.stderr).lower()
                    stlink_connected = "st-link" in output or "stlink" in output
                    if stlink_connected:
                        stlink_info = "ST-Link detected"
                        for ln in (result.stdout + result.stderr).split("\n"):
                            if "ST-LINK SN" in ln or "ST-LINK FW" in ln:
                                stlink_info = ln.strip()[:80]
                                break
            except Exception:
                pass

    # Method 3: pyocd / stlink Python package - only mark connected if actual probe enumeration succeeds
    if not stlink_connected:
        try:
            import pyocd
            from pyocd.probe import aggregator
            probes = aggregator.DebugProbeAggregator.get_all_connected_probes()
            if probes:
                stlink_connected = True
                stlink_info = f"pyOCD: {probes[0].product_name} ({probes[0].unique_id})"
        except ImportError:
            pass
    if not stlink_connected:
        try:
            import stlink
            devices = stlink.enum_devices()
            if devices:
                stlink_connected = True
                stlink_info = f"stlink: {devices[0].description}"
        except ImportError:
            pass

    # Method 4: probe-rs
    if not stlink_connected:
        try:
            result = subprocess.run(
                ["probe-rs", "list"],
                capture_output=True, text=True, timeout=10
            )
            output = (result.stdout + result.stderr).lower()
            if "debug probe" in output or "st-link" in output or "stlink" in output:
                stlink_connected = True
                for line in (result.stdout + result.stderr).split(chr(92) + 'n'):
                    if "st-link" in line.lower() or "stlink" in line.lower():
                        stlink_info = f"probe-rs: {line.strip()[:80]}"
                        break
                if not stlink_info or stlink_info == "stlink package available":
                    stlink_info = "probe-rs detected ST-Link"
        except Exception:
            pass

    # Method 5: Windows PnP (slow fallback) device manager (catches ST-Link without COM port)
    if not stlink_connected and platform.system() == "Windows":
        try:
            ps_cmd = 'Get-PnpDevice | Where-Object { $_.FriendlyName -like "*stlink*" -or $_.FriendlyName -like "*ST-Link*" -or $_.InstanceId -like "USB\\\\VID_0483*" } | Select-Object -First 1 FriendlyName, InstanceId, Status | ConvertTo-Json'
            result = subprocess.run(
                ["powershell", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=3
            )
            if result.stdout.strip():
                import json as _json
                try:
                    dev = _json.loads(result.stdout.strip())
                    name = dev.get("FriendlyName", "") or "ST-Link"
                    status = dev.get("Status", "")
                    stlink_connected = status.upper() == "OK" or status == ""
                    stlink_info = f"{name} (PnP)"
                except Exception:
                    if "STLink" in result.stdout or "ST-Link" in result.stdout:
                        stlink_connected = True
                        stlink_info = "ST-Link (PnP)"
        except Exception:
            pass

    # --- Serial port detection ---
    try:
        from serial.tools import list_ports
        ports = list(list_ports.comports())
        candidates = []
        for p in ports:
            text = " ".join(str(getattr(p, a, "") or "") for a in ("device", "description", "hwid")).lower()
            if any(t in text for t in ("usb-serial", "ch340", "ch341", "ch343", "cp210", "ftdi", "stlink", "st-link", "uart", "serial")):
                candidates.append(p)
        if candidates:
            serial_connected = True
            serial_port = candidates[0].device or str(candidates[0])
        elif ports:
            serial_port = ports[0].device or str(ports[0])
    except ImportError:
        try:
            import serial.tools.list_ports as lp
            ports = list(lp.comports())
            if ports:
                serial_connected = True
                serial_port = ports[0].device or str(ports[0])
        except Exception:
            pass
    except Exception:
        pass

    # Toolchain status
    tc_available = 0
    tc_total = 0
    try:
        cfg_manager = _get_cm()
        cfg = cfg_manager.ensure_default_config()
        from luxar.core.toolchain_manager import ToolchainManager
        tm = ToolchainManager(config=cfg, project_root=str(cfg_manager.project_root()))
        ts = tm.status()
        tc_available = sum(1 for v in ts.values() if v)
        tc_total = len(ts)
    except Exception:
        pass

    return {
        "success": True,
        "toolchains": {"available": tc_available, "total": tc_total},
        "stlink": {
            "connected": stlink_connected,
            "info": stlink_info or ("ST-Link detected" if stlink_connected else "Not found"),
        },
        "serial": {
            "connected": serial_connected,
            "port": serial_port or ("COM port" if serial_connected else "Not found"),
        },
    }


def workspace_build(project: str, clean: bool = False) -> object:
    cfg_manager = _get_cm()
    cfg = cfg_manager.ensure_default_config()
    project_path = str(cfg_manager.workspace_root() / project)
    return run_build_project(
        project_path=project_path,
        config=cfg,
        project_root=str(cfg_manager.project_root()),
        clean=clean,
    )


def workspace_flash(project: str, probe: str = "") -> object:
    cfg_manager = _get_cm()
    cfg = cfg_manager.ensure_default_config()
    project_path = str(cfg_manager.workspace_root() / project)
    return run_flash_project(
        project_path=project_path,
        config=cfg,
        project_root=str(cfg_manager.project_root()),
        probe=probe or None,
    )


def workspace_monitor(project: str, port: str, baudrate: int = 115200) -> object:
    cfg_manager = _get_cm()
    cfg = cfg_manager.ensure_default_config()
    project_path = str(cfg_manager.workspace_root() / project)
    return run_monitor_project(
        project_path=project_path,
        config=cfg,
        project_root=str(cfg_manager.project_root()),
        port=port,
        baudrate=baudrate,
    )



def workspace_monitor_start(project: str, port: str, baudrate: int = 115200) -> dict:
    """Start persistent background serial monitoring. Output streams to frontend via SSE."""
    mgr = MonitorManager.instance()
    ok = mgr.start(port=port, baudrate=baudrate)
    return {
        "success": ok,
        "state": mgr.state,
        "port": mgr.port,
        "baudrate": mgr.baudrate,
        "message": f"Monitor {"started" if ok else "failed to start"} on {port}",
    }


def workspace_monitor_stop(project: str) -> dict:
    """Stop persistent background serial monitoring and release the port."""
    mgr = MonitorManager.instance()
    was_running = mgr.stop()
    return {
        "success": True,
        "state": mgr.state,
        "was_running": was_running,
        "message": "Monitor stopped" if was_running else "Monitor was not running",
    }


def workspace_monitor_status(project: str) -> dict:
    """Get current state of background serial monitor."""
    mgr = MonitorManager.instance()
    result = mgr.status_dict()
    recent = mgr.read_buffer(max_lines=20)
    if recent:
        result["recent_lines"] = recent
    result["success"] = True
    return result
def workspace_probe(project: str, probe_type: str = "i2c") -> dict[str, object]:
    cfg_manager = _get_cm()
    cfg = cfg_manager.ensure_default_config()
    return run_probe_project(
        project_path=str(cfg_manager.workspace_root() / project),
        config=cfg,
        project_root=str(cfg_manager.project_root()),
        probe_type=probe_type,
    )


def workspace_hw_probe(project: str, probe: str = "stlink", address: str = "0x08000000", words: int = 1) -> dict[str, object]:
    """Run a real hardware-level SWD/ST-Link probe and return structured evidence."""
    import re
    import subprocess
    from pathlib import Path

    cfg_manager = _get_cm()
    cfg = cfg_manager.ensure_default_config()
    project_dir = (cfg_manager.workspace_root() / project).resolve()
    if not project or not project.strip():
        return {"success": False, "error": "No project selected."}
    if not project_dir.exists():
        return {"success": False, "error": f"Project '{project}' not found"}

    normalized_probe = (probe or "stlink").strip().lower()
    if normalized_probe not in {"stlink", "swd"}:
        return {
            "success": False,
            "error": f"Unsupported hardware probe '{probe}'. Supported probes: stlink.",
            "probe": probe,
        }

    try:
        read_words = max(1, min(int(words), 16))
    except (TypeError, ValueError):
        read_words = 1
    read_bytes = read_words * 4
    read_address = str(address or "0x08000000")

    try:
        from luxar.core.toolchain_manager import ToolchainManager

        tm = ToolchainManager(config=cfg, project_root=str(cfg_manager.project_root()))
        programmer_cli = tm.resolve_programmer_cli()
    except Exception:
        programmer_cli = None
    if not programmer_cli:
        return {
            "success": False,
            "error": "STM32_Programmer_CLI not found. Configure LUXAR toolchains.programmer_cli or install STM32CubeProgrammer.",
            "probe": "stlink",
        }

    command = [programmer_cli, "-c", "port=SWD", "-r32", read_address, str(read_bytes)]
    try:
        proc = subprocess.run(
            command,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "ST-Link hardware probe timed out.",
            "probe": "stlink",
            "command": command,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc), "probe": "stlink", "command": command}

    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")

    def _field(label: str) -> str:
        match = re.search(rf"^\s*{re.escape(label)}\s*:\s*(.+?)\s*$", output, re.MULTILINE | re.IGNORECASE)
        return match.group(1).strip() if match else ""

    readback: list[dict[str, str]] = []
    for match in re.finditer(r"^\s*(0x[0-9A-Fa-f]+)\s*:\s*([0-9A-Fa-f]{8})\s*$", output, re.MULTILINE):
        readback.append({"address": match.group(1), "value": match.group(2).upper()})

    stlink = {
        "serial": _field("ST-LINK SN"),
        "firmware": _field("ST-LINK FW"),
    }
    target = {
        "voltage": _field("Voltage"),
        "device_id": _field("Device ID"),
        "revision_id": _field("Revision ID"),
        "device_name": _field("Device name"),
        "nvm_size": _field("NVM size"),
        "cpu": _field("Device CPU"),
    }
    success = proc.returncode == 0 and bool(readback)
    return {
        "success": success,
        "probe": "stlink",
        "interface": "SWD",
        "project": project,
        "project_path": str(project_dir),
        "command": command,
        "return_code": proc.returncode,
        "stlink": stlink,
        "target": target,
        "readback": readback,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "error": "" if success else "ST-Link hardware probe failed or produced no flash readback.",
    }


_UART_GATE_MAPPINGS = {
    "USART1": ("PA9", "PA10"),
    "USART2": ("PA2", "PA3"),
    "USART3": ("PB10", "PB11"),
}


def _gpio_port(pin: str) -> str:
    return f"GPIO{pin[1]}"


def _gpio_pin_mask(pin: str) -> str:
    return f"GPIO_PIN_{int(pin[2:])}"


def _gpio_clock_enable(pin: str) -> str:
    return f"__HAL_RCC_GPIO{pin[1]}_CLK_ENABLE();"


def workspace_uart_gate(project: str, usart: str, tx_pin: str, rx_pin: str, baudrate: int = 115200) -> dict[str, object]:
    """Generate an explicit UART hardware-gate app after the user confirms UART wiring."""
    import json
    import re

    cfg_manager = _get_cm()
    project_dir = (cfg_manager.workspace_root() / project).resolve()
    if not project or not project.strip():
        return {"success": False, "error": "No project selected."}
    if not project_dir.exists():
        return {"success": False, "error": f"Project '{project}' not found"}

    meta_path = project_dir / ".agent_project.json"
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    except Exception:
        metadata = {}
    platform = str(metadata.get("platform", ""))
    runtime = str(metadata.get("runtime", ""))
    family = str(metadata.get("family", "F1") or "F1").upper()
    if platform != "stm32firmware":
        return {"success": False, "error": "UART gate generation currently supports stm32firmware projects only."}
    if runtime not in {"baremetal", "freertos"}:
        return {"success": False, "error": f"Unsupported system '{runtime}' for UART gate generation."}
    if family != "F1":
        return {
            "success": False,
            "error": f"UART gate pin template currently supports STM32F1 projects. Project family is {family}.",
        }

    usart_name = (usart or "").strip().upper()
    tx = (tx_pin or "").strip().upper()
    rx = (rx_pin or "").strip().upper()
    if usart_name not in _UART_GATE_MAPPINGS:
        return {"success": False, "error": "Unsupported USART. Choose USART1, USART2, or USART3."}
    if not re.fullmatch(r"P[A-Z][0-9]{1,2}", tx) or not re.fullmatch(r"P[A-Z][0-9]{1,2}", rx):
        return {"success": False, "error": "Invalid TX/RX pin format. Use values like PA9 and PA10."}
    expected_tx, expected_rx = _UART_GATE_MAPPINGS[usart_name]
    if (tx, rx) != (expected_tx, expected_rx):
        return {
            "success": False,
            "error": f"{usart_name} currently supports {expected_tx}/{expected_rx} on STM32F1 gate firmware.",
            "recommended": {"usart": usart_name, "tx_pin": expected_tx, "rx_pin": expected_rx},
        }
    try:
        baud = int(baudrate)
    except (TypeError, ValueError):
        baud = 115200
    if baud <= 0:
        return {"success": False, "error": "Baudrate must be a positive integer."}

    app_inc = project_dir / "App" / "Inc"
    app_src = project_dir / "App" / "Src"
    app_inc.mkdir(parents=True, exist_ok=True)
    app_src.mkdir(parents=True, exist_ok=True)
    hal_conf = project_dir / "Core" / "Inc" / "stm32f1xx_hal_conf.h"
    if hal_conf.exists():
        conf_text = hal_conf.read_text(encoding="utf-8")
        if "#define HAL_UART_MODULE_ENABLED" not in conf_text:
            conf_text = conf_text.replace(
                "#define HAL_RCC_MODULE_ENABLED",
                "#define HAL_RCC_MODULE_ENABLED\n#define HAL_UART_MODULE_ENABLED",
            )
        if '#include "stm32f1xx_hal_uart.h"' not in conf_text:
            conf_text = conf_text.replace(
                '#include "stm32f1xx_hal_pwr.h"\n#endif',
                '#include "stm32f1xx_hal_pwr.h"\n#endif\n#ifdef HAL_UART_MODULE_ENABLED\n #include "stm32f1xx_hal_uart.h"\n#endif',
            )
        hal_conf.write_text(conf_text, encoding="utf-8")
    cmake_file = project_dir / "CMakeLists.txt"
    if cmake_file.exists():
        cmake_text = cmake_file.read_text(encoding="utf-8")
        uart_source = "${HAL_DRIVER}/Src/stm32f1xx_hal_uart.c"
        if uart_source not in cmake_text:
            cmake_text = cmake_text.replace(
                "    ${HAL_DRIVER}/Src/stm32f1xx_hal_pwr.c\n",
                "    ${HAL_DRIVER}/Src/stm32f1xx_hal_pwr.c\n    ${HAL_DRIVER}/Src/stm32f1xx_hal_uart.c\n",
            )
            cmake_file.write_text(cmake_text, encoding="utf-8")

    include_header = "stm32f1xx_hal.h"
    tx_port = _gpio_port(tx)
    rx_port = _gpio_port(rx)
    tx_mask = _gpio_pin_mask(tx)
    rx_mask = _gpio_pin_mask(rx)
    clock_lines = "\n    ".join(sorted({_gpio_clock_enable(tx), _gpio_clock_enable(rx)}))
    usart_clock = f"__HAL_RCC_{usart_name}_CLK_ENABLE();"
    handle_name = f"huart_{usart_name.lower()}"

    header = f"""#ifndef APP_MAIN_H
#define APP_MAIN_H

#ifdef __cplusplus
extern "C" {{
#endif

#include "{include_header}"

void App_Init(void);
void App_Loop(void);
void App_DefaultTask(void *argument);

#ifdef __cplusplus
}}
#endif

#endif /* APP_MAIN_H */
"""
    if runtime == "baremetal":
        source = f"""#include "app_main.h"

#include <string.h>

static UART_HandleTypeDef {handle_name};

void App_Init(void)
{{
    GPIO_InitTypeDef gpio = {{0}};

    {clock_lines}
    {usart_clock}

    gpio.Pin = {tx_mask};
    gpio.Mode = GPIO_MODE_AF_PP;
    gpio.Pull = GPIO_NOPULL;
    gpio.Speed = GPIO_SPEED_FREQ_HIGH;
    HAL_GPIO_Init({tx_port}, &gpio);

    gpio.Pin = {rx_mask};
    gpio.Mode = GPIO_MODE_INPUT;
    gpio.Pull = GPIO_NOPULL;
    HAL_GPIO_Init({rx_port}, &gpio);

    {handle_name}.Instance = {usart_name};
    {handle_name}.Init.BaudRate = {baud};
    {handle_name}.Init.WordLength = UART_WORDLENGTH_8B;
    {handle_name}.Init.StopBits = UART_STOPBITS_1;
    {handle_name}.Init.Parity = UART_PARITY_NONE;
    {handle_name}.Init.Mode = UART_MODE_TX_RX;
    {handle_name}.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    {handle_name}.Init.OverSampling = UART_OVERSAMPLING_16;
    HAL_UART_Init(&{handle_name});
}}

void App_Loop(void)
{{
    static const char msg[] = "LUXAR_HW_GATE_OK\\r\\n";
    HAL_UART_Transmit(&{handle_name}, (uint8_t *)msg, (uint16_t)strlen(msg), HAL_MAX_DELAY);
    HAL_Delay(1000);
}}
"""
    else:
        source = f"""#include "app_main.h"

#include "cmsis_os.h"
#include <string.h>

static UART_HandleTypeDef {handle_name};

static void Gate_UART_Init(void)
{{
    GPIO_InitTypeDef gpio = {{0}};

    {clock_lines}
    {usart_clock}

    gpio.Pin = {tx_mask};
    gpio.Mode = GPIO_MODE_AF_PP;
    gpio.Pull = GPIO_NOPULL;
    gpio.Speed = GPIO_SPEED_FREQ_HIGH;
    HAL_GPIO_Init({tx_port}, &gpio);

    gpio.Pin = {rx_mask};
    gpio.Mode = GPIO_MODE_INPUT;
    gpio.Pull = GPIO_NOPULL;
    HAL_GPIO_Init({rx_port}, &gpio);

    {handle_name}.Instance = {usart_name};
    {handle_name}.Init.BaudRate = {baud};
    {handle_name}.Init.WordLength = UART_WORDLENGTH_8B;
    {handle_name}.Init.StopBits = UART_STOPBITS_1;
    {handle_name}.Init.Parity = UART_PARITY_NONE;
    {handle_name}.Init.Mode = UART_MODE_TX_RX;
    {handle_name}.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    {handle_name}.Init.OverSampling = UART_OVERSAMPLING_16;
    HAL_UART_Init(&{handle_name});
}}

void App_Init(void)
{{
    Gate_UART_Init();
}}

void App_Loop(void)
{{
    static const char msg[] = "LUXAR_HW_GATE_OK\\r\\n";
    HAL_UART_Transmit(&{handle_name}, (uint8_t *)msg, (uint16_t)strlen(msg), HAL_MAX_DELAY);
}}

void App_DefaultTask(void *argument)
{{
    if (argument == NULL) {{
        return;
    }}
    (void)argument;
    App_Init();
    for (;;) {{
        App_Loop();
        osDelay(1000);
    }}
}}
"""
    (app_inc / "app_main.h").write_text(header, encoding="utf-8")
    (app_src / "app_main.c").write_text(source, encoding="utf-8")
    return {
        "success": True,
        "project": project,
        "runtime": runtime,
        "usart": usart_name,
        "tx_pin": tx,
        "rx_pin": rx,
        "baudrate": baud,
        "message": "UART hardware gate firmware generated. Build, flash, then monitor for LUXAR_HW_GATE_OK.",
        "files": [str(app_inc / "app_main.h"), str(app_src / "app_main.c")],
    }


def workspace_write_file(project: str, path: str, content: str) -> dict:
    """Write content to a file within a project directory."""
    from pathlib import Path
    cfg_manager = _get_cm()
    project_dir = cfg_manager.workspace_root() / project
    if not project_dir.exists():
        return {"success": False, "error": f"Project '{project}' not found"}
    full_path = (project_dir / path).resolve()
    if not full_path.is_relative_to(project_dir.resolve()):
        return {"success": False, "error": "Access denied: path outside workspace"}
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    return {"success": True, "path": str(full_path), "size": len(content)}

# -- Safe shell commands --
_ALLOWED_COMMANDS = frozenset({"cat", "grep", "rg", "head", "tail", "wc", "find", "ls", "dir", "type", "findstr"})
_FORBIDDEN_PATTERNS = (";", "&&", "$(", "`", "&")
_SHELL_TIMEOUT_SEC = 10
_SHELL_MAX_OUTPUT = 16000


def workspace_shell(project: str, command: str) -> dict[str, object]:
    """Execute a safe shell command in the project directory."""
    if not project or not project.strip():
        return {"success": False, "error": "No project selected."}
    if not command or not command.strip():
        return {"success": False, "error": "No command specified."}

    for pattern in _FORBIDDEN_PATTERNS:
        if pattern in command:
            return {
                "success": False,
                "error": f"Forbidden pattern '{pattern}' in command. Use single safe commands only.",
            }

    cmd_parts = command.strip().split()
    base_cmd = cmd_parts[0].lower().replace(".exe", "")
    if base_cmd not in _ALLOWED_COMMANDS:
        return {
            "success": False,
            "error": f"Command '{base_cmd}' not allowed. Allowed: {', '.join(sorted(_ALLOWED_COMMANDS))}.",
        }

    cfg_manager = _get_cm()
    ws = cfg_manager.workspace_root()
    cwd = (ws / project).resolve()

    if not cwd.is_relative_to(ws):
        return {"success": False, "error": "Access denied: project outside workspace"}

    import subprocess
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_SHELL_TIMEOUT_SEC,
            encoding="utf-8",
            errors="replace",
        )
        stdout = proc.stdout
        if len(stdout) > _SHELL_MAX_OUTPUT:
            stdout = stdout[:_SHELL_MAX_OUTPUT] + f"\n... [truncated from {len(proc.stdout)} chars]"
        stderr = proc.stderr
        if len(stderr) > 2000:
            stderr = stderr[:2000] + "\n... [stderr truncated]"
        return {
            "success": proc.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": proc.returncode,
            "cwd": str(cwd),
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Command timed out after {_SHELL_TIMEOUT_SEC}s"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}

def workspace_publish_driver(
    project: str,
    header_path: str,
    source_path: str,
    variant: str = "",
    force: bool = False,
) -> dict[str, object]:
    """Publish a manually-written driver from a project to the shared driver library.

    Copies .h/.c files from workspace/projects/{project}/{header_path} into
    workspace/driver_library/{vendor}/{chip}/{variant}/ with content dedup.
    """
    cm = _get_cm()
    ws = cm.workspace_root()
    project_dir = ws / project
    h_src = (project_dir / header_path).resolve()
    c_src = (project_dir / source_path).resolve() if source_path else None
    lib_path = cm.project_root() / "workspace" / "driver_library"

    if not h_src.exists():
        return {"success": False, "error": f"Header file not found: {h_src}"}
    if c_src and not c_src.exists():
        return {"success": False, "error": f"Source file not found: {c_src}"}

    from luxar.tools.driver_indexer import publish_driver_to_library

    result = publish_driver_to_library(
        library_path=str(lib_path),
        header_path=str(h_src),
        source_path=str(c_src) if c_src else "",
        variant=variant,
        force=force,
    )
    return result
