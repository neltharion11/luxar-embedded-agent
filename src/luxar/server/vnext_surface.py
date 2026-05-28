from __future__ import annotations

from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from sse_starlette.sse import EventSourceResponse
import asyncio
import json
from sse_starlette.sse import EventSourceResponse
import asyncio
import json


def register_vnext_http_surface(
    app: FastAPI,
    *,
    cfg,
    cm,
    run_runtime,
    explain_runtime_tool,
    memory_read,
    memory_write,
    memory_lessons,
    memory_lesson_record,
    memory_lesson_promote,
    memory_search,
    workspace_inspect,
    workspace_list_projects,
    workspace_create_project,
    workspace_status,
    workspace_build,
    workspace_flash,
    workspace_monitor,
    workspace_monitor_start,
    workspace_monitor_stop,
    workspace_monitor_status,
    workspace_probe,
    skills_list,
    skill_view,
    skill_manage,
    skill_promote,
    skill_execute,
) -> None:
    @app.post("/api/runtime/run")
    def api_runtime_run(body: dict):
        task = str(body.get("task", "") or body.get("message", "")).strip()
        return run_runtime(task=task, project=str(body.get("project", "")))

    @app.get("/api/runtime/explain")
    def api_runtime_explain():
        return explain_runtime_tool()

    @app.get("/api/memory")
    def api_memory(target: str = Query("memory")):
        return memory_read(target=target)

    @app.post("/api/memory")
    def api_memory_write(body: dict):
        return memory_write(
            content=str(body.get("content", "")),
            target=str(body.get("target", "memory")),
            append=bool(body.get("append", True)),
        )

    @app.get("/api/memory/lessons")
    def api_memory_lessons(query: str = Query(""), limit: int = Query(5)):
        return memory_lessons(query=query, limit=limit)

    @app.post("/api/memory/lessons")
    def api_memory_record_lesson(body: dict):
        try:
            return memory_lesson_record(payload=body, promoted=bool(body.get("promoted", False)))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/api/memory/lessons/promote")
    def api_memory_promote_lesson(body: dict):
        try:
            return memory_lesson_promote(
                slug=str(body.get("slug", "")),
                evidence_count=int(body.get("evidence_count", 1)),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/session-search")
    def api_session_search(query: str = Query(...)):
        return memory_search(query=query)

    @app.get("/api/workspace/status")
    def api_workspace_status():
        return workspace_status()

    @app.get("/api/workspace")
    def api_workspace_inspect():
        return workspace_inspect()

    @app.get("/api/workspace/projects")
    def api_workspace_list_projects():
        return workspace_list_projects()

    @app.post("/api/workspace/create-project")
    def api_workspace_create_project(body: dict):
        result = workspace_create_project(
            name=str(body.get("name", "")).strip(),
            mcu=str(body.get("mcu", "STM32F103C8")).strip(),
            platform=str(body.get("platform", "stm32cubemx")).strip(),
            runtime=str(body.get("runtime", "baremetal")).strip(),
            firmware_package=str(body.get("firmware_package", "")).strip(),
            overwrite=bool(body.get("overwrite", False)),
        )
        if not result.get("success"):
            raise HTTPException(status_code=409, detail=result.get("error", "Project creation failed"))
        return result

    @app.post("/api/workspace/build")
    def api_workspace_build(body: dict):
        result = workspace_build(project=str(body.get("project", "")), clean=bool(body.get("clean", False)))
        return result.model_dump(mode="json") if hasattr(result, "model_dump") else result

    @app.post("/api/workspace/flash")
    def api_workspace_flash(body: dict):
        result = workspace_flash(project=str(body.get("project", "")), probe=str(body.get("probe", "")))
        return result.model_dump(mode="json") if hasattr(result, "model_dump") else result

    @app.post("/api/workspace/monitor")
    def api_workspace_monitor(body: dict):
        result = workspace_monitor(
            project=str(body.get("project", "")),
            port=str(body.get("port", "")),
            baudrate=int(body.get("baudrate", 115200)),
        )
        return result.model_dump(mode="json") if hasattr(result, "model_dump") else result

    @app.post("/api/workspace/monitor/start")
    def api_workspace_monitor_start(body: dict):
        from luxar.tools.workspace_tool import workspace_monitor_start
        result = workspace_monitor_start(
            project=str(body.get("project", "")),
            port=str(body.get("port", "")),
            baudrate=int(body.get("baudrate", 115200)),
        )
        return result if isinstance(result, dict) else result.model_dump(mode="json") if hasattr(result, "model_dump") else result

    @app.post("/api/workspace/monitor/stop")
    def api_workspace_monitor_stop(body: dict):
        from luxar.tools.workspace_tool import workspace_monitor_stop
        result = workspace_monitor_stop(project=str(body.get("project", "")))
        return result if isinstance(result, dict) else result.model_dump(mode="json") if hasattr(result, "model_dump") else result

    @app.get("/api/workspace/monitor/stream")
    async def api_workspace_monitor_stream(project: str = Query("")):
        from luxar.core.monitor_manager import MonitorManager
        mgr = MonitorManager.instance()
        async def event_stream():
            while True:
                lines = mgr.read_buffer(max_lines=20)
                for line in lines:
                    yield {"event": "serial_line", "data": line}
                if mgr.state == "stopped":
                    yield {"event": "serial_status", "data": json.dumps({"state": "stopped"})}
                    break
                await asyncio.sleep(0.2)
        return EventSourceResponse(event_stream())

    @app.get("/api/drivers")
    def api_list_drivers():
        from pathlib import Path as _P
        _root = _P(__file__).resolve().parent.parent.parent.parent
        _dr = _root / "workspace" / "driver_library" / "generated"
        if not _dr.exists():
            return {"drivers": []}
        drivers = []
        for periph_dir in sorted(_dr.iterdir()):
            if not periph_dir.is_dir():
                continue
            peripheral = periph_dir.name
            for vendor_dir in sorted(periph_dir.iterdir()):
                if not vendor_dir.is_dir():
                    continue
                vendor = vendor_dir.name
                for drv_dir in sorted(vendor_dir.iterdir()):
                    if not drv_dir.is_dir():
                        continue
                    files = []
                    for f in sorted(drv_dir.iterdir()):
                        if f.suffix in (".c", ".h"):
                            files.append({"name": f.name, "size": f.stat().st_size})
                    if files:
                        drivers.append({"name": drv_dir.name, "peripheral": peripheral, "vendor": vendor, "files": files})
        return {"drivers": drivers}

    
    @app.delete("/api/projects/{name}")
    def api_delete_project(name: str):
        import shutil
        target = cm.workspace_root() / name
        if not target.exists():
            raise HTTPException(status_code=404, detail=f"Project '{name}' not found")
        try:
            shutil.rmtree(target)
            return {"success": True, "project": name}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/projects/import")
    def api_import_project(body: dict):
        import shutil, json as _json
        source = str(body.get("source_path", "") or body.get("path", "")).strip()
        if not source:
            raise HTTPException(status_code=400, detail="source_path or path is required")
        name = str(body.get("name", "")).strip() or Path(source).name
        src = Path(source)
        if not src.exists():
            raise HTTPException(status_code=404, detail=f"Source path not found: {source}")
        dst = cm.workspace_root() / name
        if dst.exists():
            raise HTTPException(status_code=409, detail=f"Project '{name}' already exists")
        shutil.copytree(src, dst)
        meta = {"name": name, "platform": "baremetal", "runtime": "baremetal", "mcu": "STM32F103C8", "project_mode": "firmware"}
        (dst / ".agent_project.json").write_text(_json.dumps(meta, indent=2), encoding="utf-8")
        return {"success": True, "project": meta}

    @app.post("/api/workspace/probe")
    def api_workspace_probe(body: dict):
        result = workspace_probe(project=str(body.get("project", "")), probe_type=str(body.get("probe_type", "i2c")))
        return result.model_dump(mode="json") if hasattr(result, "model_dump") else result

    @app.get("/api/skills")
    def api_skills(category: str | None = Query(None)):
        return skills_list(category=category)

    @app.get("/api/skills/{name}")
    def api_skill_view(name: str):
        result = skill_view(name=name)
        if not result.get("success"):
            raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
        return result

    @app.post("/api/skills/manage")
    def api_skill_manage(body: dict):
        return skill_manage(
            action=str(body.get("action", "")),
            name=str(body.get("name", "")),
            category=str(body.get("category", "workflows")),
            content=str(body.get("content", "")),
            old_string=str(body.get("old_string", "")),
            new_string=str(body.get("new_string", "")),
        )

    @app.post("/api/skills/{name}/promote")
    def api_skill_promote(name: str, body: dict):
        return skill_promote(
            name=name,
            category=str(body.get("category", "")),
            promotion_level=str(body.get("promotion_level", "validated")),
        )

    @app.post("/api/skills/{name}/execute")
    def api_skill_execute(name: str, body: dict):
        return skill_execute(
            name=name,
            category=str(body.get("category", "")),
            project=str(body.get("project", "")),
            port=str(body.get("port", "")),
            baudrate=int(body.get("baudrate", 115200)),
        )


    @app.get("/api/pick-files")
    def api_pick_files():
        """Open a tkinter file/directory picker dialog and return selected paths."""
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        paths = list(filedialog.askopenfilenames(
            title="Select files to attach",
        ))
        root.destroy()
        return {"paths": paths}

