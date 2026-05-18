from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

from luxar.agent.context_builder import RuntimeWorkspace
from luxar.core.config_manager import ConfigManager
from luxar.skills.manager import SkillManagerVNext
from luxar.tools.workspace_tool import workspace_build, workspace_flash, workspace_monitor, workspace_probe

_yaml = YAML()


def _manager() -> SkillManagerVNext:
    workspace = RuntimeWorkspace.from_manager(ConfigManager())
    workspace.ensure_layout()
    return SkillManagerVNext(workspace.skills_root)


def skills_list(category: str | None = None) -> dict[str, object]:
    return {"success": True, "skills": _manager().list_skills(category=category)}


def skill_view(name: str) -> dict[str, object]:
    data = _manager().view(name)
    return {"success": data is not None, "skill": data}


def skill_manage(action: str, name: str, category: str = "workflows", content: str = "", old_string: str = "", new_string: str = "") -> dict[str, object]:
    return _manager().manage(
        action=action,
        name=name,
        category=category,
        content=content,
        old_string=old_string,
        new_string=new_string,
    )


def skill_promote(name: str, category: str = "", promotion_level: str = "validated") -> dict[str, object]:
    manager = _manager()
    skill = manager.view(name)
    if not skill:
        return {"success": False, "error": f"Skill '{name}' not found."}
    path = Path(str(skill["path"]))
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {"success": False, "error": f"Skill '{name}' does not contain YAML frontmatter."}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {"success": False, "error": f"Skill '{name}' frontmatter is malformed."}
    metadata = _yaml.load(parts[1]) or {}
    if category and str(metadata.get("category", "")).strip() != category.strip():
        return {
            "success": False,
            "error": f"Skill '{name}' is not in category '{category}'.",
        }
    metadata["promotion_level"] = promotion_level
    from io import StringIO

    stream = StringIO()
    _yaml.dump(metadata, stream)
    path.write_text(f"---\n{stream.getvalue().strip()}\n---{parts[2]}", encoding="utf-8")
    return {"success": True, "name": name, "path": str(path), "promotion_level": promotion_level}


def skill_execute(name: str, category: str = "", project: str = "", port: str = "", baudrate: int = 115200) -> dict[str, object]:
    skill = _manager().view(name)
    if not skill:
        return {"success": False, "error": f"Skill '{name}' not found."}
    mode = str(skill.get("metadata", {}).get("mode", "")).strip().lower()
    if mode != "executable":
        return {"success": False, "error": f"Skill '{name}' is not executable."}
    normalized = name.strip().lower()
    evidence: list[dict[str, object]] = []
    if normalized == "oled-i2c-minimal" and project:
        build_result = workspace_build(project=project, clean=False)
        evidence.append({
            "kind": "build",
            "success": bool(getattr(build_result, "success", False)),
            "details": build_result.model_dump(mode="json") if hasattr(build_result, "model_dump") else build_result,
        })
        flash_result = workspace_flash(project=project, probe="")
        evidence.append({
            "kind": "flash",
            "success": bool(getattr(flash_result, "success", False)),
            "details": flash_result.model_dump(mode="json") if hasattr(flash_result, "model_dump") else flash_result,
        })
        evidence.append({
            "kind": "probe",
            "success": True,
            "details": workspace_probe(project=project, probe_type="i2c"),
        })
        if port:
            monitor_result = workspace_monitor(project=project, port=port, baudrate=baudrate)
            evidence.append({
                "kind": "monitor",
                "success": bool(getattr(monitor_result, "success", False)),
                "details": monitor_result.model_dump(mode="json") if hasattr(monitor_result, "model_dump") else monitor_result,
            })
    else:
        evidence.append({
            "kind": "skill",
            "success": True,
            "details": {
                "status": "planned",
                "message": "Executable skill is registered but has no concrete workspace binding yet.",
            },
        })
    return {
        "success": True,
        "skill": skill,
        "mode": "executable",
        "evidence": evidence,
    }
