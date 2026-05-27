from __future__ import annotations

from luxar.core.platform_adapter import PlatformAdapter
from luxar.models.schemas import BuildResult, ReviewIssue
import re


class BuildSystem:
    def __init__(self, adapter: PlatformAdapter):
        self.adapter = adapter

    def build_project(self, project_path: str, clean: bool = False) -> BuildResult:
        return self.adapter.build(project_path, clean=clean)

def parse_build_error_lines(build_stderr: str, stdout: str = "") -> list[ReviewIssue]:
    issues: list[ReviewIssue] = []
    combined = (build_stderr or "") + "\n" + (stdout or "")
    pattern = re.compile(
        r"^(?P<file>[^:]+):(?P<line>\d+):(?P<column>\d+):\s+(?:fatal )?(?P<severity>error|warning):\s+(?P<message>.+)$",
        re.MULTILINE,
    )
    for match in pattern.finditer(combined):
        issues.append(ReviewIssue(
            file=match.group("file"),
            line=int(match.group("line")),
            column=int(match.group("column")),
            severity="error" if match.group("severity") == "error" else "warning",
            rule_id="BUILD",
            message=match.group("message").strip(),
            suggestion="Fix the compilation error so the file compiles successfully.",
        ))
    return issues
