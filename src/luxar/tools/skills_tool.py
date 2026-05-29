from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML

from luxar.agent.context_builder import RuntimeWorkspace
from luxar.core.config_manager import ConfigManager
from luxar.skills.manager import SkillManagerVNext
from luxar.tools.workspace_tool import workspace_build, workspace_flash, workspace_monitor, workspace_probe

_yaml = YAML()


_cm_instance: ConfigManager | None = None

def _get_cm() -> ConfigManager:
    global _cm_instance
    if _cm_instance is None:
        _cm_instance = ConfigManager()
    return _cm_instance


def _manager() -> SkillManagerVNext:
    workspace = RuntimeWorkspace.from_manager(_get_cm())
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
    import re, os
    skill = _manager().view(name)
    if not skill:
        return {"success": False, "error": f"Skill '{name}' not found."}
    normalized = name.strip().lower()
    evidence: list[dict[str, object]] = []

    # ── Template-based skill: copy from workspace/templates/ ──
    template_path = skill.get("metadata", {}).get("template", "")
    if template_path and project:
        import shutil
        from luxar.core.config_manager import ConfigManager
        cm = ConfigManager()
        tpl_src = cm.project_root() / template_path
        tpl_dst = cm.workspace_root() / project
        if tpl_src.exists():
            shutil.copytree(tpl_src, tpl_dst, dirs_exist_ok=True)
            # Replace {PROJECT_NAME} placeholder in CMakeLists.txt
            cmake_file = tpl_dst / "CMakeLists.txt"
            if cmake_file.exists():
                cm_text = cmake_file.read_text(encoding="utf-8")
                cm_text = cm_text.replace("{PROJECT_NAME}", project)
                cmake_file.write_text(cm_text, encoding="utf-8")

            # Write .agent_project.json so WebUI sidebar discovers this project
            agent_file = tpl_dst / ".agent_project.json"
            import json as _json2
            agent_file.write_text(_json2.dumps({
                "name": project,
                "mcu": "STM32F103C8",
                "platform": "baremetal",
                "runtime": "baremetal",
                "project_mode": "firmware",
                "firmware_package": "STM32Cube_FW_F1_V1.8.7"
            }, indent=2), encoding="utf-8")
            # Link HAL/CMSIS from firmware_library if available
            from luxar.core.config_manager import ConfigManager
            cm2 = ConfigManager()
            fl = cm2.project_root() / "workspace" / "firmware_library" / "stm32" / "STM32Cube_FW_F1"
            hal_src = fl / "Drivers" / "STM32F1xx_HAL_Driver"
            cmsis_src = fl / "Drivers" / "CMSIS"
            hal_dst = tpl_dst / "Drivers" / "STM32F1xx_HAL_Driver"
            cmsis_dst = tpl_dst / "Drivers" / "CMSIS"
            hal_installed = (hal_dst / "Inc" / "stm32f1xx_hal.h").exists()
            cmsis_installed = (cmsis_dst / "Include" / "core_cm3.h").exists()
            # Check if existing files are just placeholders (< 200 bytes)
            hal_is_placeholder = hal_installed and (hal_dst / "Inc" / "stm32f1xx_hal.h").stat().st_size < 200
            cmsis_is_placeholder = cmsis_installed and (cmsis_dst / "Include" / "core_cm3.h").stat().st_size < 200
            if hal_src.exists() and (not hal_installed or hal_is_placeholder):
                shutil.copytree(hal_src, hal_dst, dirs_exist_ok=True)
            if cmsis_src.exists() and (not cmsis_installed or cmsis_is_placeholder):
                shutil.copytree(cmsis_src, cmsis_dst, dirs_exist_ok=True)
            # If baremetal project now has HAL, inject HAL paths into CMakeLists.txt
            hal_final = (tpl_dst / "Drivers" / "STM32F1xx_HAL_Driver" / "Inc" / "stm32f1xx_hal.h").exists()
            cmakelists = tpl_dst / "CMakeLists.txt"
            if hal_final and cmakelists.exists():
                cm_text = cmakelists.read_text(encoding="utf-8")
                if "STM32F1xx_HAL_Driver" not in cm_text:
                    hal_lines = (
                        "    Drivers/CMSIS/Include\n"
                        "    Drivers/CMSIS/Device/ST/STM32F1xx/Include\n"
                        "    Drivers/STM32F1xx_HAL_Driver/Inc\n"
                        "    Drivers/STM32F1xx_HAL_Driver/Inc/Legacy\n"
                    )
                    cm_text = cm_text.replace(
                        "target_include_directories(${CMAKE_PROJECT_NAME} PRIVATE inc)",
                        "target_include_directories(${CMAKE_PROJECT_NAME} PRIVATE inc\n"
                        + hal_lines +
                        ")"
                    )
                    hal_sources = (
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
                    cm_text = cm_text.replace(
                        "    src/system_stm32f1xx.c\n)",
                        "    src/system_stm32f1xx.c\n" + hal_sources + ")"
                    )
                    cm_text = cm_text.replace(
                        ")\nadd_custom_command",
                        ")\ntarget_compile_definitions(${CMAKE_PROJECT_NAME} PRIVATE STM32F103xB USE_HAL_DRIVER)\nadd_custom_command"
                    )
                    cmakelists.write_text(cm_text, encoding="utf-8")
            return {"success": True, "skill": name, "project": project, "files_created": [], "evidence": evidence}
        return {"success": False, "error": f"Template not found: {tpl_src}"}

    # ── Generic skill execution: parse SKILL.md for file blocks ──
    skill_content = skill.get("content", "") or ""
    skill_dir = skill.get("path", "")
    if not skill_content and skill_dir:
        skmd = os.path.join(skill_dir, "SKILL.md")
        if os.path.exists(skmd):
            with open(skmd, "r", encoding="utf-8") as f:
                skill_content = f.read()

    if skill_content and project:
        # Determine project root from context or config
        from luxar.core.config_manager import ConfigManager
        cm = ConfigManager()
        project_dir = cm.workspace_root() / project
        project_dir.mkdir(parents=True, exist_ok=True)

        # Parse SKILL.md: find code blocks with file paths and content
        # Pattern: ### Step N: ... followed by ```...``` blocks
        # or lines like "Create xxx/FILENAME:" followed by ``` blocks
        created_files = []
        current_file = None
        current_content_lines = []
        in_code_block = False

        for line in skill_content.split("\n"):
            # Detect file creation intent
            file_match = re.match(r'.*[Cc]reate\s+.*?[:：]\s*$', line)
            path_match = re.match(r'.*[Cc]reate\s+(\S+)\s*$', line)
            dir_match = re.match(r'.*[Cc]reate\s+directory\s+(\S+)', line)

            if dir_match and not in_code_block:
                d = dir_match.group(1).strip().rstrip(":")
                full_dir = project_dir / d
                full_dir.mkdir(parents=True, exist_ok=True)
                created_files.append(f"dir:{d}")

            if path_match and not in_code_block:
                current_file = path_match.group(1).strip().rstrip(":")
                current_content_lines = []

            if "```" in line:
                if in_code_block:
                    # End of code block - write file
                    in_code_block = False
                    if current_file and current_content_lines:
                        full_path = project_dir / current_file
                        full_path.parent.mkdir(parents=True, exist_ok=True)
                        code = "\n".join(current_content_lines) + "\n"
                        with open(full_path, "w", encoding="utf-8") as f:
                            f.write(code)
                        created_files.append(current_file)
                    current_file = None
                    current_content_lines = []
                else:
                    in_code_block = True
                    # Check if the line before the code block names a file
                    lang = line.strip().strip("`").strip()
                    if lang in ("c", "makefile", "ld", "s", "h", "asm", ""):
                        pass  # code block starts
            elif in_code_block and current_file:
                current_content_lines.append(line)

        # Also detect step headers: "### Step N: Create NAME (filename.ext)"
        # Find all step headers with filenames and capture ALL code blocks until next step
        step_pattern = re.compile(
            r'###\s*Step\s*\d+[：:]\s*(?:Create\s+)?.*?([\w./-]+\.\w+)',
            re.IGNORECASE
        )
        step_matches = list(step_pattern.finditer(skill_content))
        # Also find ALL ### Step headers as boundaries
        all_step_headers = list(re.finditer(r'^###\s*Step\s*\d+', skill_content, re.MULTILINE))
        for idx, match in enumerate(step_matches):
            fname = match.group(1).strip().lstrip("(").rstrip(")")
            start_pos = match.end()
            # Find the next ### Step header (any step, not just matched ones)
            end_pos = len(skill_content)
            header_pos = match.start()
            for h in all_step_headers:
                if h.start() > header_pos:
                    end_pos = h.start()
                    break
            section = skill_content[start_pos:end_pos]

            # Extract ALL code blocks from this section
            code_blocks = re.findall(r'```\w*\n(.*?)```', section, re.DOTALL)
            if code_blocks:
                code = "\n".join(block.strip() for block in code_blocks if block.strip())
                if len(code) > 50:
                    full_path = project_dir / fname.strip()
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(full_path, "w", encoding="utf-8") as f:
                        f.write(code + "\n")
                    created_files.append(fname.strip())

        # Also detect markdown-style file markers: **filename.ext**
        if not created_files:
            file_blocks = re.findall(r'\*\*([\w./-]+\.\w+)\*\*\s*\n```(\w*)\n(.*?)```', skill_content, re.DOTALL)
            for fname, lang, code in file_blocks:
                full_path = project_dir / fname.strip()
                full_path.parent.mkdir(parents=True, exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(code.strip() + "\n")
                created_files.append(fname.strip())

        if created_files:
            evidence.append({"kind": "files_created", "files": created_files})
            return {"success": True, "skill": name, "project": project, "files_created": created_files, "evidence": evidence}

    # ── Skill routing: init_project_framework -> picks correct template ──
    if normalized == "init_project_framework" and project:
        import json as _json, shutil
        meta_file = project_dir / ".agent_project.json"
        platform = "stm32cubemx"
        if meta_file.exists():
            try:
                meta = _json.loads(meta_file.read_text(encoding="utf-8"))
                platform = meta.get("platform", "stm32cubemx")
            except Exception:
                pass
        # Determine template path based on platform
        from luxar.core.config_manager import ConfigManager
        cm = ConfigManager()
        tpl_name = "cubemx" if platform == "stm32cubemx" else "baremetal"
        tpl_src = cm.project_root() / "workspace" / "templates" / tpl_name
        tpl_dst = cm.workspace_root() / project
        if not tpl_src.exists():
            return {"success": False, "error": f"Template not found: {tpl_src}"}
        shutil.copytree(tpl_src, tpl_dst, dirs_exist_ok=True)
        # Replace {PROJECT_NAME} placeholder
        cmake_file = tpl_dst / "CMakeLists.txt"
        if cmake_file.exists():
            cm_text = cmake_file.read_text(encoding="utf-8")
            cm_text = cm_text.replace("{PROJECT_NAME}", project)
            cmake_file.write_text(cm_text, encoding="utf-8")
        # Copy HAL/CMSIS if baremetal
        if tpl_name == "baremetal":
            fl = cm.project_root() / "workspace" / "firmware_library" / "stm32" / "STM32Cube_FW_F1_V1.8.7"
            hal_src = fl / "Drivers" / "STM32F1xx_HAL_Driver"
            cmsis_src = fl / "Drivers" / "CMSIS"
            hal_dst = tpl_dst / "Drivers" / "STM32F1xx_HAL_Driver"
            cmsis_dst = tpl_dst / "Drivers" / "CMSIS"
            if hal_src.exists():
                shutil.copytree(hal_src, hal_dst, dirs_exist_ok=True)
            if cmsis_src.exists():
                shutil.copytree(cmsis_src, cmsis_dst, dirs_exist_ok=True)
            # Inject HAL into CMakeLists.txt
            if cmake_file.exists():
                cm_text = cmake_file.read_text(encoding="utf-8")
                if "STM32F1xx_HAL_Driver" not in cm_text:
                    hal_lines = (
                        "    Drivers/CMSIS/Include\n"
                        "    Drivers/CMSIS/Device/ST/STM32F1xx/Include\n"
                        "    Drivers/STM32F1xx_HAL_Driver/Inc\n"
                        "    Drivers/STM32F1xx_HAL_Driver/Inc/Legacy\n"
                    )
                    cm_text = cm_text.replace(
                        "target_include_directories(${CMAKE_PROJECT_NAME} PRIVATE inc)",
                        "target_include_directories(${CMAKE_PROJECT_NAME} PRIVATE inc\n" + hal_lines + ")"
                    )
                    hal_sources = (
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
                    cm_text = cm_text.replace(
                        "    src/system_stm32f1xx.c\n)",
                        "    src/system_stm32f1xx.c\n" + hal_sources + ")"
                    )
                    cm_text = cm_text.replace(
                        ")\nadd_custom_command",
                        ")\ntarget_compile_definitions(${CMAKE_PROJECT_NAME} PRIVATE STM32F103xB USE_HAL_DRIVER)\nadd_custom_command"
                    )
                    cmake_file.write_text(cm_text, encoding="utf-8")
        return {"success": True, "skill": name, "project": project, "files_created": [], "evidence": evidence}

    # ── Hardcoded executable skills ──
    mode = str(skill.get("metadata", {}).get("mode", "")).strip().lower()
    if normalized == "oled-i2c-minimal" and project:
        if mode == "executable":
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
