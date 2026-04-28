from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from luxar.core.config_manager import ConfigManager
from luxar.core.llm_client import LLMClient, LLMClientError
from luxar.models.schemas import ReviewIssue, ReviewReport
from luxar.prompts.semantic_review import (
    SEMANTIC_REVIEW_SYSTEM_PROMPT,
    build_semantic_review_prompt,
)


class ReviewEngine:
    def __init__(self, project_path: str):
        self.project_path = Path(project_path).resolve()
        self.project_mode = self._load_project_mode()
        self.clang_tidy_config = self.project_path / ".clang-tidy"
        self.config = ConfigManager().ensure_default_config()

    def review_file(self, file_path: str, code: str | None = None) -> ReviewReport:
        path = Path(file_path).resolve()
        source = code if code is not None else path.read_text(encoding="utf-8")

        static_report = self._run_clang_tidy(path)
        custom_report = self._run_custom_rules(path, source)
        semantic_report = self._run_semantic_review(path, source)
        return self._merge_reports(static_report, custom_report, semantic_report)

    def review_files(self, file_paths: list[str]) -> ReviewReport:
        aggregate = self._empty_report()
        for file_path in file_paths:
            aggregate = self._merge_reports(aggregate, self.review_file(file_path))
        return aggregate

    def review_project(self) -> ReviewReport:
        """Review App/ source files + Core/ files with USER CODE sections (user-editable)."""
        app_root = self.project_path / "App"
        core_root = self.project_path / "Core"
        files = []

        # Always include App/ files
        if app_root.exists():
            for f in app_root.rglob("*.[ch]"):
                files.append(str(f.resolve()))

        # Include Core/ files that contain USER CODE BEGIN markers (user-editable)
        if core_root.exists():
            for f in core_root.rglob("*.[ch]"):
                try:
                    content = f.read_text(encoding="utf-8")
                    if "USER CODE BEGIN" in content:
                        files.append(str(f.resolve()))
                except Exception:
                    pass

        if not files:
            return self._build_report(
                issues=[],
                raw_logs={"review_project": {"warning": "No reviewable source files found"}},
            )
        return self.review_files(files)

    def discover_project_files(self) -> list[str]:
        candidates: list[Path] = []
        for root_name in ("App", "Core"):
            root = self.project_path / root_name
            if root.exists():
                candidates.extend(root.rglob("*.[ch]"))
        return sorted(str(path.resolve()) for path in candidates)

    def _run_custom_rules(self, path: Path, source: str) -> ReviewReport:
        if not self.config.review.layers.custom_rules:
            return self._build_report(
                issues=[],
                raw_logs={"custom_rules": {"enabled": False, "reason": "custom rules disabled by config"}},
            )
        issues: list[ReviewIssue] = []
        parts = self._path_parts_lower(path)

        # Skip CubeMX-generated Core/ files for most rules (only EMB-002 applies)
        is_core = "core" in parts

        if self._is_driver_like(parts) and self._has_global_handle_reference(source):
            issues.append(
                self._issue(
                    path,
                    self._first_line(source, r"\b(hspi\d+|hi2c\d+|huart\d+)\b"),
                    "error",
                    "EMB-001",
                    "Driver-like code must not directly reference CubeMX global handles.",
                    "Inject HAL operations through an interface struct or callbacks.",
                )
            )

        if self._is_cubemx_protected_file(path, parts) and "USER CODE BEGIN" not in source:
            issues.append(
                self._issue(
                    path,
                    1,
                    "error",
                    "EMB-002",
                    "CubeMX-managed main.c is missing USER CODE markers.",
                    "Regenerate the file from CubeMX or restore USER CODE BEGIN/END sections.",
                )
            )

        # Core/ files: skip EMB-010 (header requirement) since CubeMX files don't need headers
        should_check_header = not is_core
        if path.suffix == ".c" and should_check_header and self._should_require_header(path) and not self._has_corresponding_header(path):
            issues.append(
                self._issue(
                    path,
                    1,
                    "error",
                    "EMB-010",
                    "Each C source file must have a corresponding header file.",
                    "Add a matching header in the module include directory.",
                )
            )

        if self._is_driver_like(parts) and "printf" in source:
            issues.append(
                self._issue(
                    path,
                    self._first_line(source, r"\bprintf\s*\("),
                    "error",
                    "EMB-004",
                    "Driver code must not use printf.",
                    "Return errors via status codes or callbacks instead of stdout.",
                )
            )

        malloc_line = self._first_line(source, r"\b(malloc|calloc|realloc|free)\s*\(")
        if malloc_line:
            issues.append(
                self._issue(
                    path,
                    malloc_line,
                    "warning",
                    "EMB-008",
                    "Dynamic allocation detected.",
                    "Prefer static allocation or explicit caller-owned buffers in embedded targets.",
                )
            )

        if self._has_hardcoded_register_address(source):
            issues.append(
                self._issue(
                    path,
                    self._first_line(source, r"0x4[0-9A-Fa-f]{7}u?"),
                    "error",
                    "EMB-006",
                    "Hardcoded peripheral register address detected.",
                    "Use CMSIS device headers or central register macros instead of literal addresses.",
                )
            )

        blocking_isr_line = self._find_blocking_hal_call_in_isr(source)
        if blocking_isr_line:
            issues.append(
                self._issue(
                    path,
                    blocking_isr_line,
                    "error",
                    "EMB-007",
                    "Blocking HAL call detected inside an interrupt handler.",
                    "Move blocking work out of the ISR or use non-blocking mechanisms.",
                )
            )

        complexity_line = self._complexity_warning_line(source)
        if complexity_line:
            issues.append(
                self._issue(
                    path,
                    complexity_line,
                    "warning",
                    "EMB-009",
                    "Function complexity appears to exceed the project threshold.",
                    "Split the function into smaller helpers to reduce branch complexity.",
                )
            )

        if self._is_driver_like(parts):
            missing_doxygen_lines = self._missing_doxygen_lines(source)
            for line in missing_doxygen_lines:
                issues.append(
                    self._issue(
                        path,
                        line,
                        "warning",
                        "EMB-003",
                        "Exported function is missing a Doxygen-style comment.",
                        "Add a /** ... */ comment above non-static function declarations or definitions.",
                    )
                )

        missing_null_check_lines = self._missing_null_check_lines(source)
        for line in missing_null_check_lines:
            issues.append(
                self._issue(
                    path,
                    line,
                    "error",
                    "EMB-005",
                    "Pointer parameter is not validated before use.",
                    "Add an early NULL check for pointer parameters.",
                )
            )

        return self._build_report(
            issues=issues,
            raw_logs={"custom_rules": {"enabled": True, "file": str(path)}},
        )

    def _run_clang_tidy(self, path: Path) -> ReviewReport:
        if not self.config.review.layers.static_analysis:
            return self._build_report(
                issues=[],
                raw_logs={"clang_tidy": {"enabled": False, "reason": "static analysis disabled by config"}},
            )
        clang_tidy = shutil.which("clang-tidy")
        if not clang_tidy:
            return self._build_report(
                issues=[],
                raw_logs={"clang_tidy": {"enabled": False, "reason": "clang-tidy not found"}},
            )

        command = [clang_tidy, str(path), "--"]
        if self.clang_tidy_config.exists():
            command.insert(1, f"--config-file={self.clang_tidy_config}")
        command.extend(self._clang_tidy_compile_args(path))
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=self.project_path,
        )
        return self._parse_clang_tidy_output(
            stdout=result.stdout,
            stderr=result.stderr,
            return_code=result.returncode,
            file_path=path,
        )

    def _run_semantic_review(self, path: Path, source: str) -> ReviewReport:
        if not self.config.review.layers.semantic_review:
            return self._build_report(
                issues=[],
                raw_logs={"semantic_review": {"enabled": False, "reason": "semantic review disabled by config"}},
            )
        if not self._should_run_semantic_review(path):
            return self._build_report(
                issues=[],
                raw_logs={"semantic_review": {"enabled": False, "reason": "file not eligible"}},
            )

        try:
            llm_client = LLMClient(self.config)
            response = llm_client.complete(
                prompt=build_semantic_review_prompt(source),
                system_prompt=SEMANTIC_REVIEW_SYSTEM_PROMPT,
            )
        except LLMClientError as exc:
            return self._build_report(
                issues=[],
                raw_logs={"semantic_review": {"enabled": False, "reason": str(exc)}},
            )

        try:
            payload = self._extract_semantic_review_payload(response.content)
            report = self._parse_semantic_review_payload(payload, path)
            report.raw_logs.update(
                {
                    "semantic_review": {
                        "enabled": True,
                        "provider": response.provider,
                        "model": response.model,
                        "raw_response": response.content,
                    }
                }
            )
            return report
        except ValueError as exc:
            return self._build_report(
                issues=[],
                raw_logs={
                    "semantic_review": {
                        "enabled": False,
                        "reason": f"invalid semantic review payload: {exc}",
                        "raw_response": response.content,
                    }
                },
            )

    def _issue(
        self,
        path: Path,
        line: int,
        severity: str,
        rule_id: str,
        message: str,
        suggestion: str,
    ) -> ReviewIssue:
        return ReviewIssue(
            file=str(path),
            line=max(1, line),
            column=0,
            severity=severity,
            rule_id=rule_id,
            message=message,
            suggestion=suggestion,
        )

    def _empty_report(self) -> ReviewReport:
        return ReviewReport(
            passed=True,
            total_issues=0,
            critical_count=0,
            error_count=0,
            warning_count=0,
            issues=[],
            raw_logs={},
        )

    def _build_report(self, issues: list[ReviewIssue], raw_logs: dict | None = None) -> ReviewReport:
        critical_count = sum(1 for issue in issues if issue.severity == "critical")
        error_count = sum(1 for issue in issues if issue.severity == "error")
        warning_count = sum(1 for issue in issues if issue.severity == "warning")
        return ReviewReport(
            passed=(critical_count == 0 and error_count == 0),
            total_issues=len(issues),
            critical_count=critical_count,
            error_count=error_count,
            warning_count=warning_count,
            issues=issues,
            raw_logs=raw_logs or {},
        )

    def _merge_reports(self, *reports: ReviewReport) -> ReviewReport:
        issues: list[ReviewIssue] = []
        raw_logs: dict[str, object] = {}
        for report in reports:
            issues.extend(report.issues)
            raw_logs.update(report.raw_logs)
        return self._build_report(issues=issues, raw_logs=raw_logs)

    def _parse_clang_tidy_output(
        self,
        stdout: str,
        stderr: str,
        return_code: int,
        file_path: Path,
    ) -> ReviewReport:
        issues: list[ReviewIssue] = []
        pattern = re.compile(
            r"^(?P<file>[A-Za-z]:[\\/].*?|[^:]+):(?P<line>\d+):(?P<column>\d+):\s+"
            r"(?P<severity>warning|error|note):\s+(?P<message>.*?)(?:\s+\[(?P<check>[^\]]+)\])?$"
        )
        for line in (stdout + "\n" + stderr).splitlines():
            match = pattern.match(line.strip())
            if not match:
                continue
            severity = match.group("severity")
            if severity == "note":
                normalized = "info"
            elif severity == "error":
                normalized = "error"
            else:
                normalized = "warning"
            issues.append(
                ReviewIssue(
                    file=match.group("file") or str(file_path),
                    line=int(match.group("line")),
                    column=int(match.group("column")),
                    severity=normalized,
                    rule_id=f"CLANG-{(match.group('check') or severity).upper().replace('-', '_')}",
                    message=match.group("message").strip(),
                    suggestion="Review the static-analysis warning and adjust code or suppressions as needed.",
                )
            )

        return self._build_report(
            issues=issues,
            raw_logs={
                "clang_tidy": {
                    "enabled": True,
                    "config_file": str(self.clang_tidy_config) if self.clang_tidy_config.exists() else "",
                    "return_code": return_code,
                    "stdout": stdout,
                    "stderr": stderr,
                }
            },
        )

    def _load_project_mode(self) -> str:
        metadata_path = self.project_path / ".agent_project.json"
        if not metadata_path.exists():
            return "cubemx"
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return "cubemx"
        return str(data.get("project_mode", "cubemx"))

    def _should_run_semantic_review(self, path: Path) -> bool:
        parts = self._path_parts_lower(path)
        return self._is_driver_like(parts)

    def _clang_tidy_compile_args(self, path: Path) -> list[str]:
        args = ["-std=c11"]
        suffix = path.suffix.lower()
        if suffix in {".c", ".h"}:
            args.extend(["-x", "c"])

        include_candidates = [
            self.project_path / "App" / "Inc",
            self.project_path / "Core" / "Inc",
            self.project_path / "Drivers" / "CMSIS" / "Include",
        ]
        cmsis_core_include = self.project_path / "Drivers" / "CMSIS" / "Core" / "Include"
        if cmsis_core_include.exists():
            include_candidates.append(cmsis_core_include)
        include_candidates.extend((self.project_path / "Drivers").glob("STM32*HAL_Driver/Inc"))
        include_candidates.extend((self.project_path / "Drivers" / "CMSIS" / "Device" / "ST").glob("*/Include"))

        for include_dir in include_candidates:
            if include_dir.exists():
                args.extend(["-I", str(include_dir)])

        args.extend(["-DUSE_HAL_DRIVER"])
        family = self._load_stm32_family()
        if family:
            args.append(f"-DSTM32{family.upper()}xx")
        return args

    def _load_stm32_family(self) -> str:
        family_path = self.project_path / "STM32_FAMILY.txt"
        if not family_path.exists():
            return ""
        return family_path.read_text(encoding="utf-8").strip()

    def _path_parts_lower(self, path: Path) -> tuple[str, ...]:
        try:
            relative = path.resolve().relative_to(self.project_path)
            parts = relative.parts
        except ValueError:
            parts = path.parts
        return tuple(part.lower() for part in parts)

    def _is_driver_like(self, parts: tuple[str, ...]) -> bool:
        return "drivers" in parts or ("app" in parts and "src" in parts)

    def _is_cubemx_protected_file(self, path: Path, parts: tuple[str, ...]) -> bool:
        return (
            self.project_mode == "cubemx"
            and "core" in parts
            and path.name.lower() == "main.c"
        )

    def _has_global_handle_reference(self, source: str) -> bool:
        return re.search(r"\b(hspi\d+|hi2c\d+|huart\d+)\b", source) is not None

    def _has_corresponding_header(self, path: Path) -> bool:
        stem = path.stem
        candidates = [
            path.with_suffix(".h"),
            path.parent.parent / "Inc" / f"{stem}.h",
            path.parent.parent / "include" / f"{stem}.h",
            self.project_path / "App" / "Inc" / f"{stem}.h",
            self.project_path / "Core" / "Inc" / f"{stem}.h",
            self.project_path / "Drivers" / f"{stem}.h",
        ]
        return any(candidate.exists() for candidate in candidates)

    def _should_require_header(self, path: Path) -> bool:
        stem = path.stem.lower()
        parts = self._path_parts_lower(path)
        # CubeMX-generated Core/ files don't need corresponding headers
        if "core" in parts:
            return False
        return stem not in {"main"} and not stem.startswith("system_") and not stem.startswith("startup_")

    def _has_hardcoded_register_address(self, source: str) -> bool:
        return re.search(r"0x4[0-9A-Fa-f]{7}u?", source) is not None

    def _find_blocking_hal_call_in_isr(self, source: str) -> int:
        pattern = re.compile(
            r"(?ms)^[\w\s\*]+?\b\w*(IRQHandler|ISR)\s*\([^)]*\)\s*\{(?P<body>.*?)^\}"
        )
        for match in pattern.finditer(source):
            body = match.group("body")
            if re.search(r"\bHAL_[A-Za-z0-9_]+_(Transmit|Receive|Delay|PollFor)\b", body):
                return source[: match.start()].count("\n") + 1
        return 0

    def _complexity_warning_line(self, source: str) -> int:
        function_pattern = re.compile(r"(?ms)^[\w\s\*]+?\b(?P<name>\w+)\s*\([^;]*\)\s*\{(?P<body>.*?)^\}")
        tokens = ("if", "for", "while", "case", "&&", "||", "?")
        for match in function_pattern.finditer(source):
            body = match.group("body")
            score = sum(body.count(token) for token in tokens)
            if score > 15:
                return source[: match.start()].count("\n") + 1
        return 0

    def _missing_doxygen_lines(self, source: str) -> list[int]:
        issues: list[int] = []
        for match in re.finditer(
            r"(?m)^(?!static\b)(?:[A-Za-z_][\w\s\*]*\s+)+(?P<name>[A-Za-z_]\w*)\s*\([^;]*\)\s*\{",
            source,
        ):
            start = match.start()
            prefix = source[max(0, start - 200):start]
            if "/**" not in prefix:
                issues.append(source[:start].count("\n") + 1)
        return issues

    def _missing_null_check_lines(self, source: str) -> list[int]:
        issues: list[int] = []
        function_pattern = re.compile(
            r"(?ms)^(?!static\b)(?P<signature>(?:[A-Za-z_][\w\s\*]*\s+)+(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^)]*)\))\s*\{(?P<body>.*?)^\}"
        )
        for match in function_pattern.finditer(source):
            params = match.group("params")
            pointer_params = []
            for raw in params.split(","):
                raw = raw.strip()
                if "*" not in raw or raw == "void":
                    continue
                param_name = raw.split("*")[-1].strip()
                param_name = param_name.split("[")[0].strip()
                if param_name:
                    pointer_params.append(param_name)

            body = match.group("body")
            missing = [
                param_name
                for param_name in pointer_params
                if not re.search(rf"\bif\s*\(\s*{re.escape(param_name)}\s*==\s*(NULL|0)\s*\)", body)
                and not re.search(rf"\bif\s*\(\s*(NULL|0)\s*==\s*{re.escape(param_name)}\s*\)", body)
                and not re.search(rf"\bif\s*\(\s*!\s*{re.escape(param_name)}\s*\)", body)
            ]
            if missing:
                issues.append(source[: match.start()].count("\n") + 1)
        return issues

    def _first_line(self, source: str, pattern: str) -> int:
        match = re.search(pattern, source)
        if not match:
            return 0
        return source[: match.start()].count("\n") + 1

    def _extract_semantic_review_payload(self, content: str) -> dict:
        fenced_match = re.search(r"```(?:json)?\n(.*?)```", content, flags=re.DOTALL | re.IGNORECASE)
        candidate = fenced_match.group(1).strip() if fenced_match else content.strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise ValueError("no JSON object found")
            return json.loads(candidate[start:end + 1])

    def _parse_semantic_review_payload(self, payload: dict, file_path: Path) -> ReviewReport:
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
        raw_issues = payload.get("issues", [])
        if not isinstance(raw_issues, list):
            raise ValueError("issues is not a list")

        issues: list[ReviewIssue] = []
        for raw_issue in raw_issues:
            if not isinstance(raw_issue, dict):
                continue
            severity = str(raw_issue.get("severity", "warning")).lower()
            if severity not in {"critical", "error", "warning", "info"}:
                severity = "warning"
            rule_name = str(raw_issue.get("rule", "semantic_review")).strip() or "semantic_review"
            issues.append(
                ReviewIssue(
                    file=str(file_path),
                    line=max(1, int(raw_issue.get("line", 1) or 1)),
                    column=0,
                    severity=severity,
                    rule_id=f"LLM-{rule_name.upper().replace(' ', '_')}",
                    message=str(raw_issue.get("description", "Semantic review issue")).strip(),
                    suggestion=str(raw_issue.get("suggestion", "")).strip(),
                )
            )
        return self._build_report(issues=issues, raw_logs={})


