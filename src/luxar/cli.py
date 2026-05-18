from __future__ import annotations

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


def _echo_json(data: object) -> None:
    try:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
    except UnicodeEncodeError:
        click.echo(json.dumps(data, ensure_ascii=True, indent=2))


@click.group()
def main() -> None:
    """LUXAR 0.2.0 CLI."""


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
    _echo_json(workspace_probe(project=project_name, probe_type=probe_type))


if __name__ == "__main__":
    main()
