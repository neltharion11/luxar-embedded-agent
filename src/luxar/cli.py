from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

import rich_click as click

from luxar.core.backup_manager import BackupManager
from luxar.core.config_manager import ConfigManager
from luxar.core.firmware_library_manager import FirmwareLibraryManager
from luxar.core.git_manager import GitManager
from luxar.core.project_manager import ProjectManager
from luxar.core.skill_manager import SkillManager
from luxar.core.toolchain_manager import ToolchainManager
from luxar.tools.assemble_project import run_assemble_project
from luxar.tools.build_project import run_build_project
from luxar.tools.check_ioc import run_check_ioc
from luxar.tools.debug_loop_project import run_debug_loop_project
from luxar.tools.flash_project import run_flash_project
from luxar.tools.fix_code import run_fix_code
from luxar.tools.forge_project import run_forge_project
from luxar.tools.generate_driver import run_generate_driver
from luxar.tools.generate_driver_loop import run_generate_driver_loop
from luxar.tools.init_project import run_init_project
from luxar.tools.monitor_project import run_monitor_project
from luxar.tools.parse_doc import run_parse_doc
from luxar.tools.review_code import run_review_project
from luxar.tools.search_driver import run_search_driver
from luxar.tools.run_workflow import run_debug_workflow, run_driver_workflow
from luxar.tools.run_task import run_task
from luxar.tools.update_skill import run_update_skill


def _echo_json(data) -> None:
    try:
        click.echo(json.dumps(data, ensure_ascii=False, indent=2))
    except UnicodeEncodeError:
        click.echo(json.dumps(data, ensure_ascii=True, indent=2))


def _service_state_path(manager: ConfigManager) -> Path:
    state_dir = manager.project_root() / ".luxar"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "dashboard-service.json"


def _load_service_state(manager: ConfigManager) -> dict[str, object] | None:
    state_path = _service_state_path(manager)
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_service_state(manager: ConfigManager, state: dict[str, object]) -> None:
    state_path = _service_state_path(manager)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_service_state(manager: ConfigManager) -> None:
    state_path = _service_state_path(manager)
    if state_path.exists():
        state_path.unlink()


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _running_service_state(manager: ConfigManager) -> dict[str, object] | None:
    state = _load_service_state(manager)
    if not state:
        return None
    pid = int(state.get("pid", 0) or 0)
    if _is_process_running(pid):
        return state
    _clear_service_state(manager)
    return None


def _stop_service_process(manager: ConfigManager, timeout_sec: float = 5.0) -> dict[str, object]:
    state = _load_service_state(manager)
    if not state:
        return {"stopped": False, "reason": "not_running"}

    pid = int(state.get("pid", 0) or 0)
    host = str(state.get("host", "127.0.0.1"))
    port = int(state.get("port", 8000) or 8000)

    if not _is_process_running(pid):
        _clear_service_state(manager)
        return {"stopped": False, "reason": "stale_state", "pid": pid, "host": host, "port": port}

    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if not _is_process_running(pid):
            _clear_service_state(manager)
            return {"stopped": True, "pid": pid, "host": host, "port": port}
        time.sleep(0.1)

    raise click.ClickException(
        f"Timed out while stopping Luxar dashboard process {pid}. "
        "Please terminate it manually and try again."
    )


class LuxarGroup(click.Group):
    def resolve_command(self, ctx: click.Context, args: list[str]):
        if args:
            cmd_name = args[0]
            cmd = self.get_command(ctx, cmd_name)
            if cmd is not None:
                return cmd_name, cmd, args[1:]
            task_cmd = self.get_command(ctx, "__task__")
            if task_cmd is not None and not cmd_name.startswith("-"):
                return "__task__", task_cmd, args
        return super().resolve_command(ctx, args)


@click.group(
    cls=LuxarGroup,
    invoke_without_command=True,
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.option("--project", "task_project_name", default="")
@click.option("--doc", "task_docs", multiple=True)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--plan-only", is_flag=True, default=False)
@click.option("--no-build", is_flag=True, default=False)
@click.option("--no-flash", is_flag=True, default=False)
@click.option("--no-monitor", is_flag=True, default=False)
@click.pass_context
def main(
    ctx: click.Context,
    task_project_name: str,
    task_docs: tuple[str, ...],
    dry_run: bool,
    plan_only: bool,
    no_build: bool,
    no_flash: bool,
    no_monitor: bool,
) -> None:
    """Luxar CLI."""
    if ctx.invoked_subcommand is not None:
        return
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.group()
def config() -> None:
    """Configuration commands."""


@main.group()
def workflow() -> None:
    """Workflow commands."""


@main.command("__task__", hidden=True)
@click.argument("task", nargs=-1, required=True)
@click.pass_context
def task_entry(ctx: click.Context, task: tuple[str, ...]) -> None:
    parent = ctx.parent
    assert parent is not None
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    result = run_task(
        config=cfg,
        project_root=str(manager.project_root()),
        workspace_root=str(manager.workspace_root()),
        driver_library_root=str(manager.driver_library_root()),
        task=" ".join(task).strip(),
        project_name=parent.params.get("task_project_name", ""),
        docs=list(parent.params.get("task_docs", ())),
        dry_run=bool(parent.params.get("dry_run", False)),
        plan_only=bool(parent.params.get("plan_only", False)),
        no_build=bool(parent.params.get("no_build", False)),
        no_flash=bool(parent.params.get("no_flash", False)),
        no_monitor=bool(parent.params.get("no_monitor", False)),
    )
    _echo_json(result)


@main.command("run")
@click.option("--project", "project_name", default="")
@click.option("--doc", "docs", multiple=True)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--plan-only", is_flag=True, default=False)
@click.option("--no-build", is_flag=True, default=False)
@click.option("--no-flash", is_flag=True, default=False)
@click.option("--no-monitor", is_flag=True, default=False)
@click.option("--task", "task_text", required=True)
def run_task_command(
    project_name: str,
    docs: tuple[str, ...],
    dry_run: bool,
    plan_only: bool,
    no_build: bool,
    no_flash: bool,
    no_monitor: bool,
    task_text: str,
) -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    result = run_task(
        config=cfg,
        project_root=str(manager.project_root()),
        workspace_root=str(manager.workspace_root()),
        driver_library_root=str(manager.driver_library_root()),
        task=task_text,
        project_name=project_name,
        docs=list(docs),
        dry_run=dry_run,
        plan_only=plan_only,
        no_build=no_build,
        no_flash=no_flash,
        no_monitor=no_monitor,
    )
    _echo_json(result)


@config.command("show")
def config_show() -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    _echo_json(cfg.model_dump(mode="json"))


@config.command("toolchains")
def config_toolchains() -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    _echo_json(ToolchainManager(cfg, manager.project_root()).status())


@config.command("firmware")
def config_firmware() -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    firmware_root = manager.firmware_library_root()
    library = FirmwareLibraryManager(firmware_root)
    _echo_json(
        {
            "firmware_root": str(firmware_root),
            "stm32_packages": library.list_stm32_packages(),
            "default_mode": cfg.stm32.project_mode,
            "use_cubemx": cfg.stm32.use_cubemx,
        }
    )


@config.command("workspace")
def config_workspace() -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    workspace_root = manager.workspace_root()
    workspace_root.mkdir(parents=True, exist_ok=True)
    projects: list[dict[str, str]] = []
    for metadata in sorted(workspace_root.glob("*/.agent_project.json")):
        project_dir = metadata.parent
        try:
            project = ProjectManager(str(workspace_root)).load_project(project_dir.name)
            projects.append(
                {
                    "name": project.name,
                    "path": project.path,
                    "platform": project.platform,
                    "runtime": project.runtime,
                    "project_mode": project.project_mode,
                    "mcu": project.mcu,
                }
            )
        except Exception:
            projects.append({"name": project_dir.name, "path": str(project_dir.resolve())})
    _echo_json(
        {
            "workspace_root": str(workspace_root),
            "configured_workspace": cfg.agent.workspace,
            "project_count": len(projects),
            "projects": projects,
        }
    )


@main.command("init")
@click.option("--name", required=True)
@click.option("--mcu", required=True)
@click.option("--platform", default="stm32cubemx", show_default=True)
@click.option("--runtime", default="baremetal", show_default=True)
@click.option(
    "--project-mode",
    type=click.Choice(["cubemx", "firmware"]),
    default=None,
)
@click.option("--firmware-package", default="")
def init_project(
    name: str,
    mcu: str,
    platform: str,
    runtime: str,
    project_mode: str | None,
    firmware_package: str,
) -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    project = run_init_project(
        workspace=str(manager.workspace_root()),
        name=name,
        mcu=mcu,
        platform=platform,
        runtime=runtime,
        project_mode=project_mode or cfg.stm32.project_mode,
        firmware_package=firmware_package or cfg.stm32.firmware_package,
    )
    _echo_json(project.model_dump(mode="json"))


@main.command("generate-driver")
@click.option("--chip", required=True)
@click.option("--interface", required=True)
@click.option("--doc-summary", required=True)
@click.option("--register-summary", default="")
@click.option("--vendor", default="")
@click.option("--device", default="")
@click.option("--output-dir", default="")
def generate_driver(
    chip: str,
    interface: str,
    doc_summary: str,
    register_summary: str,
    vendor: str,
    device: str,
    output_dir: str,
) -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    result = run_generate_driver(
        config=cfg,
        project_root=str(manager.project_root()),
        chip=chip,
        interface=interface,
        doc_summary=doc_summary,
        register_summary=register_summary,
        vendor=vendor,
        device=device,
        output_dir=output_dir,
    )
    _echo_json(result.model_dump(mode="json"))


@main.command("generate-driver-loop")
@click.option("--chip", required=True)
@click.option("--interface", required=True)
@click.option("--doc-summary", required=True)
@click.option("--register-summary", default="")
@click.option("--vendor", default="")
@click.option("--device", default="")
@click.option("--output-dir", default="")
@click.option("--max-fix-iterations", default=None, type=int)
def generate_driver_loop(
    chip: str,
    interface: str,
    doc_summary: str,
    register_summary: str,
    vendor: str,
    device: str,
    output_dir: str,
    max_fix_iterations: int | None,
) -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    result = run_generate_driver_loop(
        config=cfg,
        project_root=str(manager.project_root()),
        chip=chip,
        interface=interface,
        doc_summary=doc_summary,
        register_summary=register_summary,
        vendor=vendor,
        device=device,
        output_dir=output_dir,
        max_fix_iterations=max_fix_iterations,
    )
    _echo_json(result.model_dump(mode="json"))


@main.command("search-driver")
@click.option("--keyword", default="")
@click.option("--protocol", default="")
@click.option("--vendor", default="")
@click.option("--limit", default=20, show_default=True, type=int)
def search_driver(keyword: str, protocol: str, vendor: str, limit: int) -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    result = run_search_driver(
        config=cfg,
        project_root=str(manager.project_root()),
        keyword=keyword,
        protocol=protocol,
        vendor=vendor,
        limit=limit,
    )
    _echo_json(result)


@main.command("snapshot")
@click.option("--project", "project_name", required=True)
@click.option("--label", required=True)
def snapshot_project(project_name: str, label: str) -> None:
    manager = ConfigManager()
    project = ProjectManager(str(manager.workspace_root())).load_project(project_name)
    snapshot = BackupManager(project.path).create_snapshot(label=label)
    _echo_json({"project": project_name, "snapshot_path": str(snapshot)})


@main.command("check-ioc")
@click.option("--project", "project_name", required=True)
def check_ioc(project_name: str) -> None:
    manager = ConfigManager()
    project = ProjectManager(str(manager.workspace_root())).load_project(project_name)
    result = run_check_ioc(project.path)
    _echo_json(result)


@main.command("assemble")
@click.option("--project", "project_name", required=True)
@click.option("--drivers", default="")
def assemble_project(project_name: str, drivers: str) -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    project_manager = ProjectManager(str(manager.workspace_root()))
    project = project_manager.load_project(project_name)
    firmware_root = str(manager.firmware_library_root())
    driver_root = str(manager.driver_library_root())
    driver_queries = [item.strip() for item in drivers.split(",") if item.strip()]
    result = run_assemble_project(
        project,
        firmware_library_root=firmware_root,
        driver_library_root=driver_root,
        drivers=driver_queries,
    )

    if cfg.git.auto_commit and result["created_files"]:
        repo = GitManager(project.path)
        relative_files = [
            str(Path(path).resolve().relative_to(Path(project.path).resolve()))
            for path in result["created_files"]
        ]
        action = (
            "assemble_stm32_firmware_project"
            if project.project_mode == "firmware"
            else "assemble_minimal_app"
        )
        repo.commit_agent_action(action, relative_files)

    _echo_json(result)


@main.command("diff")
@click.option("--project", "project_name", required=True)
def diff_project(project_name: str) -> None:
    manager = ConfigManager()
    project = ProjectManager(str(manager.workspace_root())).load_project(project_name)
    git_manager = GitManager(project.path)
    click.echo(git_manager.get_diff_since_last_human_commit())


@main.command("build")
@click.option("--project", "project_name", required=True)
@click.option("--clean", is_flag=True, default=False)
@click.option("--skip-review", is_flag=True, default=False)
def build_project(project_name: str, clean: bool, skip_review: bool) -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    project = ProjectManager(str(manager.workspace_root())).load_project(project_name)
    result = run_build_project(
        project.path,
        config=cfg,
        project_root=str(manager.project_root()),
        clean=clean,
        skip_review=skip_review,
    )
    _echo_json(result.model_dump(mode="json"))


@main.command("flash")
@click.option("--project", "project_name", required=True)
@click.option("--probe", default=None)
def flash_project(project_name: str, probe: str | None) -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    project = ProjectManager(str(manager.workspace_root())).load_project(project_name)
    result = run_flash_project(
        project.path,
        config=cfg,
        project_root=str(manager.project_root()),
        probe=probe,
    )
    _echo_json(result.model_dump(mode="json"))


@main.command("monitor")
@click.option("--project", "project_name", required=True)
@click.option("--port", default="")
def monitor_project(project_name: str, port: str) -> None:
    manager = ConfigManager()
    project = ProjectManager(str(manager.workspace_root())).load_project(project_name)
    result = run_monitor_project(project.path, port=port)
    _echo_json(result.model_dump(mode="json"))


@main.command("review")
@click.option("--project", "project_name", required=True)
@click.option("--file", "file_path", default=None)
def review_project(project_name: str, file_path: str | None) -> None:
    manager = ConfigManager()
    project = ProjectManager(str(manager.workspace_root())).load_project(project_name)
    result = run_review_project(project.path, file_path=file_path)
    _echo_json(result)


@main.command("parse-doc")
@click.option("--doc", "doc_path", required=True)
@click.option("--query", default="")
@click.option("--chunk-size", default=1200, show_default=True, type=int)
@click.option("--overlap", default=120, show_default=True, type=int)
def parse_doc(doc_path: str, query: str, chunk_size: int, overlap: int) -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    result = run_parse_doc(
        config=cfg,
        project_root=str(manager.project_root()),
        source_path=doc_path,
        query=query,
        chunk_size=chunk_size,
        overlap=overlap,
    )
    _echo_json(result)


@main.command("update-skill")
@click.option("--protocol", required=True)
@click.option("--device", "device_name", required=True)
@click.option("--summary", required=True)
@click.option("--lesson", "lessons", multiple=True)
@click.option("--platform", "platforms", multiple=True)
@click.option("--runtime", "runtimes", multiple=True)
@click.option("--source-project", required=True)
def update_skill(
    protocol: str,
    device_name: str,
    summary: str,
    lessons: tuple[str, ...],
    platforms: tuple[str, ...],
    runtimes: tuple[str, ...],
    source_project: str,
) -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    artifact = run_update_skill(
        config=cfg,
        project_root=str(manager.project_root()),
        protocol=protocol,
        device_name=device_name,
        summary=summary,
        lessons_learned=list(lessons),
        platforms=list(platforms) or [cfg.platform.default_platform],
        runtimes=list(runtimes) or [cfg.platform.default_runtime],
        source_project=source_project,
    )
    _echo_json(artifact.model_dump(mode="json"))


@main.command("list-skills")
@click.option("--protocol", default=None)
def list_skills(protocol: str | None) -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    skill_mgr = SkillManager(config=cfg, project_root=str(manager.project_root()))
    skills = skill_mgr.list_skills(protocol=protocol)
    _echo_json({"count": len(skills), "skills": skills})


@main.command("status")
@click.option("--project", "project_name", required=True)
def status_project(project_name: str) -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    workspace_root = manager.workspace_root()
    project_manager = ProjectManager(str(workspace_root))
    project = project_manager.load_project(project_name)
    project_root = manager.project_root()
    toolchain_status = ToolchainManager(cfg, str(project_root)).status()
    skill_mgr = SkillManager(config=cfg, project_root=str(project_root))
    skills = skill_mgr.list_skills()
    try:
        repo = GitManager(project.path)
        diff = repo.get_diff_since_last_human_commit()
    except Exception:
        diff = ""
    _echo_json({
        "project": {
            "name": project.name,
            "path": project.path,
            "platform": project.platform,
            "runtime": project.runtime,
            "project_mode": project.project_mode,
            "mcu": project.mcu,
        },
        "toolchains": toolchain_status,
        "skills": {"count": len(skills)},
        "git": {"uncommitted_changes": bool(diff.strip())},
    })


@main.command("fix-code")
@click.option("--project", "project_name", required=True)
@click.option("--file", "file_path", required=True)
@click.option("--dry-run", is_flag=True, default=False)
def fix_code(project_name: str, file_path: str, dry_run: bool) -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    project = ProjectManager(str(manager.workspace_root())).load_project(project_name)
    result = run_fix_code(
        config=cfg,
        project_path=project.path,
        file_path=file_path,
        apply_changes=not dry_run,
    )
    _echo_json(result.model_dump(mode="json"))


@main.command("debug-loop")
@click.option("--project", "project_name", required=True)
@click.option("--probe", default=None)
@click.option("--port", default="")
@click.option("--clean", is_flag=True, default=False)
@click.option("--lines", default=10, show_default=True)
@click.option("--baudrate", default=None, type=int)
def debug_loop_project(
    project_name: str,
    probe: str | None,
    port: str,
    clean: bool,
    lines: int,
    baudrate: int | None,
) -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    project = ProjectManager(str(manager.workspace_root())).load_project(project_name)
    result = run_debug_loop_project(
        project.path,
        config=cfg,
        project_root=str(manager.project_root()),
        probe=probe,
        port=port,
        clean=clean,
        lines=lines,
        baudrate=baudrate,
    )
    _echo_json(result.model_dump(mode="json"))


@main.command("forge")
@click.option("--project", "project_name", required=True)
@click.option("--prompt", "requirement", required=True)
@click.option("--drivers", default="")
@click.option("--clean", is_flag=True, default=False)
@click.option("--no-build", is_flag=True, default=False)
@click.option("--no-flash", is_flag=True, default=False)
@click.option("--no-monitor", is_flag=True, default=False)
@click.option("--plan-only", is_flag=True, default=False)
@click.option("--doc", "docs", multiple=True)
@click.option("--doc-query", default="")
@click.option("--probe", default=None)
@click.option("--port", default="")
@click.option("--baudrate", default=None, type=int)
def forge_project(
    project_name: str,
    requirement: str,
    drivers: str,
    clean: bool,
    no_build: bool,
    no_flash: bool,
    no_monitor: bool,
    plan_only: bool,
    docs: tuple[str, ...],
    doc_query: str,
    probe: str | None,
    port: str,
    baudrate: int | None,
) -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    project = ProjectManager(str(manager.workspace_root())).load_project(project_name)
    driver_queries = [item.strip() for item in drivers.split(",") if item.strip()]
    result = run_forge_project(
        config=cfg,
        project_root=str(manager.project_root()),
        project=project,
        requirement=requirement,
        driver_library_root=str(manager.driver_library_root()),
        drivers=driver_queries,
        clean=clean,
        build=not no_build,
        plan_only=plan_only,
        no_flash=no_flash,
        no_monitor=no_monitor,
        docs=list(docs),
        doc_query=doc_query,
        probe=probe,
        port=port,
        baudrate=baudrate,
    )
    _echo_json(result.model_dump(mode="json"))


@workflow.command("driver")
@click.option("--chip", required=True)
@click.option("--interface", required=True)
@click.option("--doc-summary", required=True)
@click.option("--register-summary", default="")
@click.option("--vendor", default="")
@click.option("--device", default="")
@click.option("--output-dir", default="")
@click.option("--max-fix-iterations", default=None, type=int)
def workflow_driver(
    chip: str,
    interface: str,
    doc_summary: str,
    register_summary: str,
    vendor: str,
    device: str,
    output_dir: str,
    max_fix_iterations: int | None,
) -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    result = run_driver_workflow(
        config=cfg,
        project_root=str(manager.project_root()),
        chip=chip,
        interface=interface,
        doc_summary=doc_summary,
        register_summary=register_summary,
        vendor=vendor,
        device=device,
        output_dir=output_dir,
        max_fix_iterations=max_fix_iterations,
    )
    _echo_json(result.model_dump(mode="json"))


@workflow.command("debug")
@click.option("--project", "project_name", required=True)
@click.option("--probe", default=None)
@click.option("--port", default="")
@click.option("--clean", is_flag=True, default=False)
@click.option("--lines", default=10, show_default=True)
@click.option("--baudrate", default=None, type=int)
def workflow_debug(
    project_name: str,
    probe: str | None,
    port: str,
    clean: bool,
    lines: int,
    baudrate: int | None,
) -> None:
    manager = ConfigManager()
    cfg = manager.ensure_default_config()
    project = ProjectManager(str(manager.workspace_root())).load_project(project_name)
    result = run_debug_workflow(
        config=cfg,
        project_root=str(manager.project_root()),
        project_path=project.path,
        probe=probe,
        port=port,
        clean=clean,
        lines=lines,
        baudrate=baudrate,
    )
    _echo_json(result.model_dump(mode="json"))


@main.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True, type=int)
@click.option("--reload", is_flag=True, default=False, help="Auto-reload on code changes")
def serve(host: str, port: int, reload: bool) -> None:
    """Start the Luxar web dashboard."""
    import uvicorn
    from luxar.server.app import create_app

    manager = ConfigManager()
    existing = _running_service_state(manager)
    if existing:
        raise click.ClickException(
            f"Luxar dashboard is already running at http://{existing.get('host', host)}:"
            f"{existing.get('port', port)} (pid {existing.get('pid')}). "
            "Run `luxar stop` first if you want to replace it."
        )

    app = create_app()
    _write_service_state(
        manager,
        {
            "pid": os.getpid(),
            "host": host,
            "port": port,
            "reload": reload,
        },
    )
    click.echo(f"Luxar dashboard starting at http://{host}:{port}")
    try:
        uvicorn.run(app, host=host, port=port, reload=reload)
    finally:
        state = _load_service_state(manager)
        if state and int(state.get("pid", 0) or 0) == os.getpid():
            _clear_service_state(manager)


@main.command("stop")
def stop() -> None:
    """Stop the Luxar web dashboard."""
    manager = ConfigManager()
    result = _stop_service_process(manager)
    if result["stopped"]:
        click.echo(
            f"Luxar dashboard stopped on http://{result['host']}:{result['port']} "
            f"(pid {result['pid']})."
        )
        return
    if result["reason"] == "stale_state":
        click.echo(
            f"Removed stale Luxar dashboard state for pid {result['pid']} "
            f"on http://{result['host']}:{result['port']}."
        )
        return
    click.echo("Luxar dashboard is not running.")


@main.command("restart")
@click.option("--host", default=None, type=str, help="Override the dashboard host")
@click.option("--port", default=None, type=int, help="Override the dashboard port")
@click.option("--reload/--no-reload", default=None, help="Override auto-reload behavior")
def restart(host: str | None, port: int | None, reload: bool | None) -> None:
    """Restart the Luxar web dashboard."""
    manager = ConfigManager()
    previous = _load_service_state(manager) or {}
    stop_result = _stop_service_process(manager)
    if stop_result["stopped"]:
        click.echo(
            f"Stopped Luxar dashboard on http://{stop_result['host']}:{stop_result['port']} "
            f"(pid {stop_result['pid']})."
        )
    elif stop_result["reason"] == "stale_state":
        click.echo("Removed stale Luxar dashboard state before restart.")
    else:
        click.echo("Luxar dashboard was not running. Starting a new instance.")

    resolved_host = host or str(previous.get("host", "127.0.0.1"))
    resolved_port = port or int(previous.get("port", 8000) or 8000)
    resolved_reload = reload if reload is not None else bool(previous.get("reload", False))
    ctx = click.get_current_context()
    ctx.invoke(serve, host=resolved_host, port=resolved_port, reload=resolved_reload)


if __name__ == "__main__":
    main()



