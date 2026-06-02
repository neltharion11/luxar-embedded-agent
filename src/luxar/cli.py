from __future__ import annotations

import logging
import json

import rich_click as click

from luxar.tools.lessons_tool import lesson_promote, lesson_record, lesson_search, lessons_list
from luxar.tools.memory_tool import memory_read, memory_search, memory_write
from luxar.tools.runtime_tool import explain_runtime_tool, run_runtime
from luxar.tools.skills_tool import (
    skill_execute,
    skill_manage,
    skill_promote,
    skill_view,
    skills_list,
)
from luxar.tools.workspace_tool import (
    workspace_build,
    workspace_flash,
    workspace_inspect,
    workspace_monitor,
    workspace_probe,
)

class _StatusFilter(logging.Filter):
    """Suppress access logs for /api/workspace/status polling."""
    def filter(self, record):
        msg = record.getMessage()
        if "/api/workspace/status" in msg:
            return False
        return True




def _echo_json(data: object) -> None:
    try:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
    except UnicodeEncodeError:
        click.echo(json.dumps(data, ensure_ascii=True, indent=2))


@click.group()
def main() -> None:
    """LUXAR 0.2.3 CLI."""




@main.command("start")
@click.option("--host", default="127.0.0.1", help="Bind address")
@click.option("--port", default=8000, type=int, help="Bind port")
@click.option("--no-reload", is_flag=True, default=False, help="Disable auto-reload")
def start_command(host: str, port: int, no_reload: bool) -> None:
    """Start the LUXAR web UI and API server."""
    import uvicorn
    click.echo(f"LUXAR server starting on http://{host}:{port}")

    _log_config = uvicorn.config.LOGGING_CONFIG
    _log_config["filters"] = {"status_filter": {"()": "luxar.cli._StatusFilter"}}
    _log_config["formatters"]["access"]["fmt"] = '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
    for _logger_name in ("uvicorn.access", "uvicorn.asgi"):
        _handlers = _log_config["loggers"].get(_logger_name, {}).get("handlers", [])
        for _h in _handlers:
            _filters = _log_config["handlers"].setdefault(_h, {}).setdefault("filters", [])
            if "status_filter" not in _filters:
                _filters.append("status_filter")

    uvicorn.run(
        "luxar.server.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=not no_reload,
        log_config=_log_config,
    )


@main.command("stop")
def stop_command() -> None:
    """Stop the running LUXAR server."""
    import subprocess, sys

    killed = False
    try:
        if sys.platform == "win32":
            ps_cmd = (
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object { $_.CommandLine -like '*luxar.cli start*' } | "
                "ForEach-Object { $_.ProcessId }"
            )
            result = subprocess.run(
                ["powershell", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=10
            )
            pids = [p.strip() for p in result.stdout.strip().split() if p.strip().isdigit()]
            for p in pids:
                subprocess.run(["taskkill", "/f", "/pid", p], capture_output=True)
                click.echo(f"Server (PID {p}) stopped.")
                killed = True
        else:
            result = subprocess.run(["pgrep", "-f", "uvicorn.*luxar"], capture_output=True, text=True)
            pids = [p.strip() for p in result.stdout.strip().split() if p.strip().isdigit()]
            for p in pids:
                import os, signal
                os.kill(int(p), signal.SIGTERM)
                click.echo(f"Server (PID {p}) stopped.")
                killed = True
    except Exception as exc:
        click.echo(f"Failed to stop: {exc}")
        return

    if not killed:
        click.echo("No running LUXAR server found.")


@main.command("run")
@click.option("--task", "task_text", required=True)
@click.option("--project", "project_name", default="")
@click.option("--explain", is_flag=True, default=False, help="Return the runtime model instead of running a task.")
def run_command(task_text: str, project_name: str, explain: bool) -> None:
    if explain:
        _echo_json(explain_runtime_tool())
        return
    _echo_json(run_runtime(task=task_text, project=project_name))


@main.group()
def skills() -> None:
    """Manage runtime skills."""


@skills.command("list")
@click.option("--category", default=None)
def skills_list_command(category: str | None) -> None:
    _echo_json(skills_list(category=category))


@skills.command("view")
@click.argument("name")
def skills_view_command(name: str) -> None:
    _echo_json(skill_view(name=name))


@skills.command("manage")
@click.option("--action", type=click.Choice(["create", "edit", "patch", "archive"]), required=True)
@click.option("--name", required=True)
@click.option("--category", default="workflows")
@click.option("--content", default="")
@click.option("--old-string", default="")
@click.option("--new-string", default="")
def skills_manage_command(
    action: str,
    name: str,
    category: str,
    content: str,
    old_string: str,
    new_string: str,
) -> None:
    _echo_json(
        skill_manage(
            action=action,
            name=name,
            category=category,
            content=content,
            old_string=old_string,
            new_string=new_string,
        )
    )


@skills.command("promote")
@click.argument("name")
@click.option("--category", default="")
@click.option("--promotion-level", default="validated")
def skills_promote_command(name: str, category: str, promotion_level: str) -> None:
    _echo_json(skill_promote(name=name, category=category, promotion_level=promotion_level))


@skills.command("execute")
@click.argument("name")
@click.option("--category", default="")
@click.option("--project", "project_name", default="")
@click.option("--port", default="")
@click.option("--baudrate", default=115200, type=int)
def skills_execute_command(name: str, category: str, project_name: str, port: str, baudrate: int) -> None:
    _echo_json(skill_execute(name=name, category=category, project=project_name, port=port, baudrate=baudrate))


@main.group()
def memory() -> None:
    """Manage durable memory, recall, and lessons."""


@memory.command("read")
@click.option("--target", type=click.Choice(["memory", "user"]), default="memory")
def memory_read_command(target: str) -> None:
    _echo_json(memory_read(target=target))


@memory.command("write")
@click.option("--content", required=True)
@click.option("--target", type=click.Choice(["memory", "user"]), default="memory")
@click.option("--replace", is_flag=True, default=False)
def memory_write_command(content: str, target: str, replace: bool) -> None:
    _echo_json(memory_write(content=content, target=target, append=not replace))


@memory.command("search")
@click.option("--query", required=True)
def memory_search_command(query: str) -> None:
    _echo_json(memory_search(query=query))


@memory.command("lessons")
def memory_lessons_list_command() -> None:
    _echo_json(lessons_list())


@memory.command("lesson-search")
@click.option("--query", required=True)
@click.option("--limit", default=5, type=int)
def memory_lesson_search_command(query: str, limit: int) -> None:
    _echo_json(lesson_search(query=query, limit=limit))


@memory.command("lesson-record")
@click.option("--payload", required=True, help="JSON object describing the lesson payload")
@click.option("--promoted", is_flag=True, default=False)
def memory_lesson_record_command(payload: str, promoted: bool) -> None:
    _echo_json(lesson_record(payload=json.loads(payload), promoted=promoted))


@memory.command("lesson-promote")
@click.option("--slug", required=True)
@click.option("--evidence-count", default=1, type=int)
def memory_lesson_promote_command(slug: str, evidence_count: int) -> None:
    _echo_json(lesson_promote(slug=slug, evidence_count=evidence_count))


@main.group()
def workspace() -> None:
    """Inspect workspace and execute runtime primitives."""


@workspace.command("inspect")
def workspace_inspect_command() -> None:
    _echo_json(workspace_inspect())


@workspace.command("build")
@click.option("--project", "project_name", required=True)
@click.option("--clean", is_flag=True, default=False)
def workspace_build_command(project_name: str, clean: bool) -> None:
    result = workspace_build(project=project_name, clean=clean)
    _echo_json(result.model_dump(mode="json") if hasattr(result, "model_dump") else result)


@workspace.command("flash")
@click.option("--project", "project_name", required=True)
@click.option("--probe", default="")
def workspace_flash_command(project_name: str, probe: str) -> None:
    result = workspace_flash(project=project_name, probe=probe)
    _echo_json(result.model_dump(mode="json") if hasattr(result, "model_dump") else result)


@workspace.command("monitor")
@click.option("--project", "project_name", required=True)
@click.option("--port", required=True)
@click.option("--baudrate", default=115200, type=int)
def workspace_monitor_command(project_name: str, port: str, baudrate: int) -> None:
    result = workspace_monitor(project=project_name, port=port, baudrate=baudrate)
    _echo_json(result.model_dump(mode="json") if hasattr(result, "model_dump") else result)


@workspace.command("probe")
@click.option("--project", "project_name", required=True)
@click.option("--probe-type", default="i2c")
def workspace_probe_command(project_name: str, probe_type: str) -> None:
    result = workspace_probe(project=project_name, probe_type=probe_type)
    _echo_json(result.model_dump(mode="json") if hasattr(result, "model_dump") else result)


if __name__ == "__main__":
    main()
