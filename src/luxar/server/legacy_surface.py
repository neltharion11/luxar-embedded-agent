from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, HTTPException
from sse_starlette.sse import EventSourceResponse

from luxar.core.config_manager import ConfigManager
from luxar.core.project_manager import ProjectManager
from luxar.core.conversation_store import ConversationStore
from luxar.tools.init_project import run_init_project


PROJECT_TEMPLATE_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("项目名", "project name", "name"),
    "mcu": ("mcu",),
    "platform": ("平台", "platform"),
    "runtime": ("运行时", "runtime"),
    "firmware_package": ("固件包", "firmware package"),
    "target": ("目标功能", "target behavior", "target function"),
    "peripherals": ("外设/通信", "peripherals / buses", "peripherals", "buses"),
    "reference_docs": ("参考文档", "reference docs", "reference documents"),
}


def normalize_project_name(project: str) -> str:
    return "" if project in {"", "__global__"} else project


def _normalize_template_label(label: str) -> str:
    return re.sub(r"\s+", " ", label.strip().lower())


def _match_template_field(label: str) -> str:
    normalized = _normalize_template_label(label)
    for field, aliases in PROJECT_TEMPLATE_ALIASES.items():
        for alias in aliases:
            if normalized == _normalize_template_label(alias):
                return field
    return ""


def parse_project_creation_request(message: str) -> dict[str, str] | None:
    parsed: dict[str, str] = {}
    seen_values: dict[str, set[str]] = {}
    labeled_lines = 0
    for raw_line in message.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^\s*[-*]?\s*([^:：]+?)\s*[:：]\s*(.+?)\s*$", line)
        if not match:
            continue
        field = _match_template_field(match.group(1))
        if not field:
            continue
        value = match.group(2).strip()
        if not value:
            continue
        labeled_lines += 1
        seen_values.setdefault(field, set()).add(value)
        if field not in parsed:
            parsed[field] = value

    if labeled_lines < 2:
        return None
    if "name" not in parsed or "mcu" not in parsed:
        return None
    if len(seen_values.get("name", set())) > 1:
        return {
            "error": "检测到多个不同的项目名。请一次只提交一个项目创建模板。",
        }
    return parsed


def build_project_creation_summary(project_payload: dict[str, str], created_name: str) -> str:
    parts = [
        f"项目 `{created_name}` 已创建。",
        f"MCU: {project_payload.get('mcu', '')}",
        f"平台: {project_payload.get('platform', 'stm32cubemx')}",
        f"运行时: {project_payload.get('runtime', 'baremetal')}",
        f"固件包: {project_payload.get('firmware_package', 'STM32Cube_FW_F1')}",
    ]
    if project_payload.get("target"):
        parts.append(f"目标功能: {project_payload['target']}")
    if project_payload.get("peripherals"):
        parts.append(f"外设/通信: {project_payload['peripherals']}")
    if project_payload.get("reference_docs"):
        parts.append(f"参考文档: {project_payload['reference_docs']}")
    return "\n".join(parts)


def create_project_from_template(project_payload: dict[str, str], cfg: Any, cm: ConfigManager):
    platform = (project_payload.get("platform", "stm32cubemx") or "stm32cubemx").strip().lower()
    runtime = (project_payload.get("runtime", "baremetal") or "baremetal").strip().lower()
    if platform not in {"stm32cubemx", "stm32firmware"}:
        raise ValueError("平台必须是 stm32cubemx 或 stm32firmware。")
    if runtime not in {"baremetal", "freertos"}:
        raise ValueError("运行时必须是 baremetal 或 freertos。")

    result = run_init_project(
        workspace=str(cm.workspace_root()),
        name=project_payload["name"].strip(),
        mcu=project_payload["mcu"].strip(),
        platform=platform,
        runtime=runtime,
        project_mode="cubemx" if platform == "stm32cubemx" else "firmware",
        firmware_package=(project_payload.get("firmware_package", "") or cfg.stm32.firmware_package).strip(),
    )

    # Copy template files (skills_tool template engine)
    from luxar.tools.skills_tool import skill_execute
    project_name = project_payload["name"].strip()
    skill_execute("init_project_framework", category="project", project=project_name)

    return result


async def stream_project_template_creation(
    conv: list[dict],
    project_payload: dict[str, str],
    cfg: Any,
    cm: ConfigManager,
    storage_project: str,
    save_conv: Callable[[str], None],
):
    yield {
        "event": "phase_changed",
        "data": json.dumps({"phase": "project_creation", "status": "started"}, ensure_ascii=False),
    }

    try:
        project = create_project_from_template(project_payload, cfg, cm)
    except Exception as exc:
        yield {"event": "error", "data": json.dumps({"error": str(exc)})}
        save_conv(storage_project)
        return

    project_result = project.model_dump(mode="json") if hasattr(project, "model_dump") else project
    yield {
        "event": "project_created",
        "data": json.dumps({"project": project_result}, ensure_ascii=False),
    }
    yield {
        "event": "phase_changed",
        "data": json.dumps({"phase": "project_creation", "status": "completed"}, ensure_ascii=False),
    }

    summary = build_project_creation_summary(project_payload, project_payload["name"].strip())
    conv.append({
        "id": str(uuid.uuid4()),
        "role": "assistant",
        "content": summary,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    save_conv(storage_project)
    yield {"event": "token", "data": json.dumps({"token": summary})}
    yield {"event": "done", "data": "[DONE]"}


def register_legacy_http_surface(
    app: FastAPI,
    cfg: Any,
    cm: ConfigManager,
    *,
    conv_cache: dict[str, list[dict]],
    conv_store: ConversationStore | None,
    project_status: Callable[[Path], dict],
) -> None:
    @app.get("/api/projects")
    def list_projects():
        ws = cm.workspace_root()
        projects = []
        for meta_file in sorted(ws.glob("*/.agent_project.json")):
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                projects.append(data)
            except Exception:
                projects.append({"name": meta_file.parent.name, "error": "invalid metadata"})
        return {"projects": projects}

    def _copy_project_template_impl(project_name, platform, cm):
        """Copy template files to project directory."""
        import shutil
        tpl_name = "cubemx" if platform == "stm32cubemx" else "baremetal"
        tpl_src = cm.project_root() / "workspace" / "templates" / tpl_name
        tpl_dst = cm.workspace_root() / project_name
        tpl_dst.mkdir(parents=True, exist_ok=True)
        if tpl_src.exists():
            shutil.copytree(tpl_src, tpl_dst, dirs_exist_ok=True)
        cmake_file = tpl_dst / "CMakeLists.txt"
        if cmake_file.exists():
            text = cmake_file.read_text(encoding="utf-8")
            cmake_file.write_text(text.replace("{PROJECT_NAME}", project_name), encoding="utf-8")
        if tpl_name == "baremetal":
            fl = cm.project_root() / "workspace" / "firmware_library" / "stm32" / "STM32Cube_FW_F1_V1.8.7"
            for sub in ["Drivers/STM32F1xx_HAL_Driver", "Drivers/CMSIS"]:
                src = fl / sub
                dst = tpl_dst / "Drivers" / sub.split("/")[-1]
                if src.exists():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
            # Inject HAL into CMakeLists.txt
            if cmake_file.exists():
                text = cmake_file.read_text(encoding="utf-8")
                if "STM32F1xx_HAL_Driver" not in text:
                    hal_inc = (
                        "    Drivers/CMSIS/Include\n"
                        "    Drivers/CMSIS/Device/ST/STM32F1xx/Include\n"
                        "    Drivers/STM32F1xx_HAL_Driver/Inc\n"
                        "    Drivers/STM32F1xx_HAL_Driver/Inc/Legacy\n"
                    )
                    cm_var_proj = "${CMAKE_PROJECT_NAME}"
                    text = text.replace(
                        "target_include_directories(" + cm_var_proj + " PRIVATE inc)",
                        "target_include_directories(" + cm_var_proj + " PRIVATE inc\n" + hal_inc + ")"
                    )
                    hal_srcs = (
                        "    Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal.c\n"
                        "    Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_rcc.c\n"
                        "    Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_rcc_ex.c\n"
                        "    Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_gpio.c\n"
                        "    Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_gpio_ex.c\n"
                        "    Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_cortex.c\n"
                        "    Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_flash.c\n"
                        "    Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_flash_ex.c\n"
                        "    Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_dma.c\n"
                        "    Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_exti.c\n"
                        "    Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal_pwr.c\n"
                    )
                    text = text.replace(
                        "    src/system_stm32f1xx.c\n)",
                        "    src/system_stm32f1xx.c\n" + hal_srcs + ")"
                    )
                    text = text.replace(
                        ")\nadd_custom_command",
                        ")\ntarget_compile_definitions(" + cm_var_proj + " PRIVATE STM32F103xB USE_HAL_DRIVER)\nadd_custom_command"
                    )
                    cmake_file.write_text(text, encoding="utf-8")

    @app.post("/api/projects")
    async def create_project(body: dict):
        name = (body.get("name", "") or "").strip()
        mcu = (body.get("mcu", "") or "").strip()
        if not name or not mcu:
            raise HTTPException(status_code=400, detail="Both 'name' and 'mcu' are required.")
        project = run_init_project(
            workspace=str(cm.workspace_root()),
            name=name,
            mcu=mcu,
            platform=body.get("platform", cfg.platform.default_platform),
            runtime=body.get("runtime", cfg.platform.default_runtime),
            project_mode=body.get("project_mode", cfg.stm32.project_mode),
            firmware_package=body.get("firmware_package", cfg.stm32.firmware_package),
        )
        # Copy template files directly
        _copy_project_template_impl(name, body.get("platform", cfg.platform.default_platform), cm)
        return {"project": project.model_dump(mode="json")}

    @app.post("/api/projects/import")
    async def import_project(body: dict):
        source_path = (body.get("source_path", "") or "").strip()
        if not source_path:
            raise HTTPException(status_code=400, detail="'source_path' is required.")
        manager = ProjectManager(str(cm.workspace_root()))
        project = manager.import_project(
            source_path=source_path,
            name=(body.get("name", "") or "").strip() or None,
            mcu=(body.get("mcu", "") or "").strip(),
            platform=body.get("platform", cfg.platform.default_platform),
            runtime=body.get("runtime", cfg.platform.default_runtime),
            project_mode=body.get("project_mode", cfg.stm32.project_mode),
            firmware_package=body.get("firmware_package", cfg.stm32.firmware_package),
        )
        return {"project": project.model_dump(mode="json")}

    @app.delete("/api/projects/{name}")
    def delete_project(name: str):
        ws = cm.workspace_root()
        project_dir = ws / name
        if not project_dir.exists():
            raise HTTPException(status_code=404, detail=f"Project '{name}' not found")
        conv_cache.pop(name, None)
        if conv_store:
            try:
                conv_store.delete(name)
            except Exception:
                pass
        try:
            shutil.rmtree(str(project_dir))
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to delete project: {exc}")
        return {"status": "ok", "deleted": name}

    @app.get("/api/pick-directory")
    def pick_directory():
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            selected = filedialog.askdirectory()
            root.destroy()
            return {"path": selected or ""}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Directory picker unavailable: {exc}") from exc

    @app.get("/api/pick-files")
    def pick_files():
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            selected = filedialog.askopenfilenames(
                filetypes=[
                    ("Documents", "*.pdf *.md *.txt *.docx"),
                    ("PDF", "*.pdf"),
                    ("Markdown", "*.md"),
                    ("Text", "*.txt"),
                    ("Word", "*.docx"),
                    ("All files", "*.*"),
                ]
            )
            root.destroy()
            return {"paths": list(selected or [])}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"File picker unavailable: {exc}") from exc

    @app.get("/api/projects/{name}")
    def get_project(name: str):
        ws = cm.workspace_root()
        meta_file = ws / name / ".agent_project.json"
        if not meta_file.exists():
            raise HTTPException(status_code=404, detail=f"Project '{name}' not found")
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        data["status"] = project_status(ws / name)
        return data

    @app.post("/api/analyze-docs")
    async def api_analyze_docs(body: dict):
        from luxar.core.document_engineering import DocumentEngineeringAnalyzer

        docs = body.get("docs", []) or []
        analyzer = DocumentEngineeringAnalyzer(cm.driver_library_root() / "knowledge_base")
        context = analyzer.analyze(docs=docs, query=body.get("query", ""))
        return {"engineering_context": context.model_dump(mode="json")}

    @app.get("/api/firmware-library")
    def get_firmware_library():
        from luxar.core.firmware_library_manager import FirmwareLibraryManager

        fm = FirmwareLibraryManager(cm.firmware_library_root())
        pkgs = fm.list_packages()
        return {"packages": pkgs}


def register_legacy_conversation_surface(
    app: FastAPI,
    cfg: Any,
    cm: ConfigManager,
    *,
    get_conv: Callable[[str], list[dict]],
    save_conv: Callable[[str], None],
    run_agent_loop: Callable[[list[dict], str, str, Any, ConfigManager, Any, list | None], Awaitable[dict[str, str]]],
    run_agent_loop_stream: Callable[[list[dict], str, str, Any, ConfigManager, Any, list | None], Awaitable[Any]],
    conv_cache: dict[str, list[dict]],
    conv_store: ConversationStore | None,
) -> None:
    @app.get("/api/conversations/{project}")
    def get_conversation(project: str):
        conv = get_conv(project)
        return {"messages": conv, "project": project}

    @app.post("/api/conversations/{project}")
    async def send_message(project: str, body: dict):
        msg_content = body.get("message", "") or body.get("content", "")
        stream = body.get("stream", False)
        conv = get_conv(project)
        normalized_project = normalize_project_name(project)

        user_msg = {
            "id": str(uuid.uuid4()),
            "role": "user",
            "content": msg_content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        conv.append(user_msg)
        docs = body.get("docs", []) or []
        project_payload = parse_project_creation_request(msg_content) if not normalized_project and not docs else None
        if project_payload and project_payload.get("error"):
            assistant_msg = {
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "content": project_payload["error"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            conv.append(assistant_msg)
            save_conv(project)
            if stream:
                async def _error_stream():
                    yield {"event": "token", "data": json.dumps({"token": project_payload["error"]})}
                    yield {"event": "done", "data": "[DONE]"}

                return EventSourceResponse(_error_stream())
            return {"message": assistant_msg, "project": project}

        if project_payload:
            if stream:
                return EventSourceResponse(stream_project_template_creation(conv, project_payload, cfg, cm, project, save_conv))
            try:
                created = create_project_from_template(project_payload, cfg, cm)
            except Exception as exc:
                assistant_msg = {
                    "id": str(uuid.uuid4()),
                    "role": "assistant",
                    "content": str(exc),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                conv.append(assistant_msg)
                save_conv(project)
                return {"message": assistant_msg, "project": project}
            summary = build_project_creation_summary(project_payload, project_payload["name"].strip())
            assistant_msg = {
                "id": str(uuid.uuid4()),
                "role": "assistant",
                "content": summary,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            conv.append(assistant_msg)
            save_conv(project)
            return {
                "message": assistant_msg,
                "project": project,
                "created_project": created.model_dump(mode="json") if hasattr(created, "model_dump") else created,
            }

        from luxar.core.llm_client import LLMClient

        client = LLMClient(cfg)

        if stream:
            return EventSourceResponse(
                run_agent_loop_stream(conv, msg_content, normalized_project, cfg, cm, client, docs)
            )
        reply = await run_agent_loop(conv, msg_content, normalized_project, cfg, cm, client, docs)
        assistant_msg = {
            "id": str(uuid.uuid4()),
            "role": "assistant",
            "content": reply.get("content", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if reply.get("reasoning_content"):
            assistant_msg["reasoning_content"] = reply["reasoning_content"]
        conv.append(assistant_msg)
        save_conv(project)
        return {"message": assistant_msg, "project": project}

    @app.post("/api/conversations/{project}/reset")
    def reset_conversation(project: str):
        conv_cache.pop(project, None)
        if conv_store:
            conv_store.delete(project)
        return {"status": "ok", "project": project}

    @app.post("/api/conversations/{project}/import")
    def import_conversation(project: str, body: dict):
        source_project = (body.get("source_project", "") or "").strip()
        replace = bool(body.get("replace", True))
        if not source_project:
            raise HTTPException(status_code=400, detail="'source_project' is required.")

        source_conv = list(get_conv(source_project))
        target_conv = [] if replace else list(get_conv(project))
        copied = [dict(message) for message in source_conv]
        merged = copied if replace else target_conv + copied
        conv_cache[project] = merged
        save_conv(project)
        return {
            "status": "ok",
            "project": project,
            "source_project": source_project,
            "imported_messages": len(copied),
            "total_messages": len(merged),
        }

