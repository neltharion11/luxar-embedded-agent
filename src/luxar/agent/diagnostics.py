from __future__ import annotations


def summarize_runtime_state(skills: int, executable_skills: int, lessons: int) -> dict[str, int]:
    return {
        "skills": int(skills),
        "executable_skills": int(executable_skills),
        "lessons": int(lessons),
    }
