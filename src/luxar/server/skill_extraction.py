from __future__ import annotations


def try_extract_skill(conv: list[dict], project: str, *, cm, client) -> str:
    """After a successful tool workflow, try auto-extracting a reusable skill."""
    try:
        from luxar.core.skill_extractor import SkillExtractor

        conv_text = "\n".join(
            f"[{message.get('role','?')}]: {str(message.get('content',''))[:500]}"
            for message in conv[-10:]
        )
        workflow_result = {
            "success": True,
            "workflow": {"steps": [{"status": "completed"}, {"status": "completed"}, {"status": "completed"}]},
        }
        has_tool_calls = any(message.get("role") == "tool" for message in conv)
        if not has_tool_calls:
            return "I've reached the maximum number of tool call rounds."

        extractor = SkillExtractor(skill_root=cm.skills_root())
        data = extractor.extract(conv_text, workflow_result, client)
        if data:
            extractor.save_skill(data, project or "global")
            return f"Workflow completed. {chr(10)}📝 Auto-extracted skill: {data.get('device','')} ({data.get('protocol','')})"
    except Exception:
        pass
    return "I've reached the maximum number of tool call rounds. Please ask me to continue if needed."
