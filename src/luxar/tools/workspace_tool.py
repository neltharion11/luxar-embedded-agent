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
    try:
        project = manager.create_project(
            name=name,
            mcu=mcu,
            platform=platform,
            runtime=runtime,
            project_mode="cubemx" if platform == "stm32cubemx" else "firmware",
            firmware_package=firmware_package or cfg.stm32.firmware_package,
            overwrite=overwrite,
        )
        # Copy template files via skill framework
        try:
            from luxar.tools.skills_tool import skill_execute
            skill_execute("init_project_framework", category="project", project=name)
        except Exception:
            pass  # template copy is best-effort; project metadata already written
        return {"success": True, "project": project.model_dump(mode="json")}
    except FileExistsError as exc:
        return {"success": False, "error": str(exc), "detail": "Project already exists"}
    except Exception as exc:
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

    # Method 0: Windows PnP device manager (catches ST-Link without COM port)
    if not stlink_connected and platform.system() == "Windows":
        try:
            ps_cmd = 'Get-PnpDevice | Where-Object { $_.FriendlyName -like "*stlink*" -or $_.FriendlyName -like "*ST-Link*" -or $_.InstanceId -like "USB\\\\VID_0483*" } | Select-Object -First 1 FriendlyName, InstanceId, Status | ConvertTo-Json'
            result = subprocess.run(
                ["powershell", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=8
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
        programmer_cli = shutil.which("STM32_Programmer_CLI") or shutil.which("STM32CubeProgrammer")
        if not programmer_cli:
            programmer_cli = shutil.which("openocd")
        if programmer_cli:
            try:
                if "openocd" in programmer_cli.lower():
                    result = subprocess.run(
                        [programmer_cli, "--version"],
                        capture_output=True, text=True, timeout=5
                    )
                    stlink_connected = result.returncode == 0
                    stlink_info = "openocd available"
                else:
                    result = subprocess.run(
                        [programmer_cli, "-l", "stlink"],
                        capture_output=True, text=True, timeout=10
                    )
                    output = (result.stdout + result.stderr).lower()
                    stlink_connected = "st-link" in output or "stlink" in output
                    if stlink_connected:
                        for line in (result.stdout + result.stderr).split("\n"):
                            if "st-link" in line.lower() or "stlink" in line.lower():
                                stlink_info = line.strip()[:80]
                                break
            except Exception:
                pass

    # Method 3: pyocd / stlink Python package
    if not stlink_connected:
        try:
            import pyocd
            stlink_connected = True
            stlink_info = "pyOCD available"
        except ImportError:
            pass
    if not stlink_connected:
        try:
            import stlink
            stlink_connected = True
            stlink_info = "stlink package available"
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

    return {
        "success": True,
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


def workspace_probe(project: str, probe_type: str = "i2c") -> dict[str, object]:
    cfg_manager = _get_cm()
    cfg = cfg_manager.ensure_default_config()
    return run_probe_project(
        project_path=str(cfg_manager.workspace_root() / project),
        config=cfg,
        project_root=str(cfg_manager.project_root()),
        probe_type=probe_type,
    )


def workspace_write_file(project: str, path: str, content: str) -> dict:
    """Write content to a file within a project directory."""
    from pathlib import Path
    cfg_manager = _get_cm()
    project_dir = cfg_manager.workspace_root() / project
    if not project_dir.exists():
        return {"success": False, "error": f"Project '{project}' not found"}
    full_path = (project_dir / path).resolve()
    if not str(full_path).startswith(str(project_dir.resolve())):
        return {"success": False, "error": "Access denied: path outside workspace"}
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    return {"success": True, "path": str(full_path), "size": len(content)}
