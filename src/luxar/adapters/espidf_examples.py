"""Read-only retrieval over examples bundled with the active ESP-IDF SDK."""

from __future__ import annotations

import re
from pathlib import Path

from luxar.domain.idf_examples import EspIdfExampleReference
from luxar.domain.repairs import ProjectFile
from luxar.domain.requirements import FirmwareRequirement
from luxar.sdk_knowledge import SdkExampleDocument, SdkExampleKnowledgeBase


_TOKEN_RE = re.compile(r"[a-z][a-z0-9]{1,31}")
_IGNORED_TERMS = {
    "application",
    "create",
    "esp32",
    "espidf",
    "firmware",
    "implement",
    "project",
    "read",
    "use",
    "write",
}
_MAX_README_CHARS = 6000
_MAX_SOURCE_CHARS = 12000
_MAX_FILES = 6
_API_RE = re.compile(r"\b([a-z][a-z0-9_]{3,})\s*\(")
_VERSION_RE = re.compile(r"IDF_VERSION_(MAJOR|MINOR|PATCH)\s+([0-9]+)")


class LocalEspIdfExampleLibrary:
    """Rank official examples by requirement terms and return bounded sources."""

    def __init__(
        self,
        idf_path: Path,
        *,
        knowledge: SdkExampleKnowledgeBase | None = None,
    ) -> None:
        self._idf_path = idf_path.resolve()
        self._examples_path = self._idf_path / "examples"
        self._knowledge = knowledge
        self._projects: list[Path] | None = None
        self._documents: list[SdkExampleDocument] | None = None
        self._version = self._detect_version()

    def _detect_version(self) -> str:
        content = self._read_text(
            self._idf_path / "tools" / "cmake" / "version.cmake",
            3000,
        )
        values = {name: value for name, value in _VERSION_RE.findall(content)}
        if {"MAJOR", "MINOR", "PATCH"} <= values.keys():
            return ".".join(values[name] for name in ("MAJOR", "MINOR", "PATCH"))
        return self._idf_path.name

    @staticmethod
    def _terms(requirement: FirmwareRequirement) -> set[str]:
        raw = [requirement.goal]
        for peripheral in requirement.peripherals:
            raw.extend((peripheral.kind, peripheral.purpose))
        terms = set(_TOKEN_RE.findall(" ".join(raw).casefold()))
        return terms - _IGNORED_TERMS

    @staticmethod
    def _read_text(path: Path, limit: int) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:limit]
        except OSError:
            return ""

    def _project_directories(self) -> list[Path]:
        if self._projects is not None:
            return list(self._projects)
        if not self._examples_path.is_dir():
            return []
        projects: list[Path] = []
        for cmake_path in self._examples_path.rglob("CMakeLists.txt"):
            content = self._read_text(cmake_path, 3000)
            if "project.cmake" in content:
                projects.append(cmake_path.parent)
        self._projects = projects
        return list(projects)

    @staticmethod
    def _requirement_query(requirement: FirmwareRequirement) -> str:
        parts = [requirement.goal]
        for peripheral in requirement.peripherals:
            parts.extend((peripheral.kind, peripheral.purpose))
        return " ".join(part for part in parts if part.strip())

    def _knowledge_documents(self) -> list[SdkExampleDocument]:
        if self._documents is not None:
            return list(self._documents)
        documents: list[SdkExampleDocument] = []
        for project_dir in self._project_directories():
            relative = project_dir.relative_to(self._examples_path).as_posix()
            readme_path = next(iter(sorted(project_dir.glob("README*"))), None)
            readme = (
                self._read_text(readme_path, _MAX_README_CHARS)
                if readme_path is not None
                else ""
            )
            api_names: set[str] = set()
            main_dir = project_dir / "main"
            if main_dir.is_dir():
                for source in sorted(main_dir.rglob("*")):
                    if source.is_file() and source.suffix.casefold() in {
                        ".c",
                        ".cc",
                        ".cpp",
                        ".h",
                        ".hpp",
                    }:
                        api_names.update(
                            _API_RE.findall(self._read_text(source, 6000))
                        )
                    if len(api_names) >= 80:
                        break
            title = relative
            for line in readme.splitlines():
                candidate = line.strip().lstrip("# ")
                if len(candidate) >= 4:
                    title = candidate[:160]
                    break
            content = (
                f"ESP-IDF example path: {relative}\n"
                f"Title: {title}\n"
                f"APIs: {' '.join(sorted(api_names)[:80])}\n"
                f"README:\n{readme}"
            )
            documents.append(
                SdkExampleDocument(
                    path=relative,
                    title=title,
                    content=content,
                    metadata={"path": relative, "kind": "official_example"},
                )
            )
        self._documents = documents
        return list(documents)

    def search(
        self,
        requirement: FirmwareRequirement,
        *,
        limit: int = 2,
    ) -> list[EspIdfExampleReference]:
        terms = self._terms(requirement)
        if limit < 1:
            return []

        ranked: dict[str, EspIdfExampleReference] = {}
        for project_dir in self._project_directories():
            relative = project_dir.relative_to(self._examples_path).as_posix()
            path_text = relative.casefold()
            readme_path = next(
                iter(sorted(project_dir.glob("README*"))),
                None,
            )
            readme = (
                self._read_text(readme_path, _MAX_README_CHARS).casefold()
                if readme_path is not None
                else ""
            )
            path_matches = {term for term in terms if term in path_text}
            readme_matches = {term for term in terms if term in readme}
            matched = path_matches | readme_matches
            score = len(path_matches) * 8 + len(readme_matches) * 2
            if (project_dir / f"sdkconfig.defaults.{requirement.target}").is_file():
                score += 2
            if score < 1:
                continue
            summary = ""
            for line in readme.splitlines():
                candidate = line.strip().lstrip("# ")
                if len(candidate) >= 12:
                    summary = candidate[:240]
                    break
            ranked[relative] = EspIdfExampleReference(
                path=relative,
                score=score,
                matched_terms=sorted(matched),
                summary=summary,
            )

        if self._knowledge is not None:
            if not self._knowledge.synchronized(self._version):
                self._knowledge.sync(
                    version=self._version,
                    documents=self._knowledge_documents(),
                )
            matches = self._knowledge.search(
                version=self._version,
                query=self._requirement_query(requirement),
                limit=max(limit * 4, 8),
            )
            for match in matches:
                prefix = "espidf-example://"
                if not match.source_uri.startswith(prefix):
                    continue
                path = match.source_uri.removeprefix(prefix)
                if not (self._examples_path / path).is_dir():
                    continue
                rag_score = max(1, round(match.score * 20))
                existing = ranked.get(path)
                ranked[path] = EspIdfExampleReference(
                    path=path,
                    score=(existing.score if existing is not None else 0)
                    + rag_score,
                    matched_terms=sorted(
                        set(existing.matched_terms if existing is not None else [])
                        | {"sdk-rag"}
                    ),
                    summary=(
                        existing.summary
                        if existing is not None and existing.summary
                        else match.title[:240]
                    ),
                )

        ordered = sorted(ranked.values(), key=lambda item: (-item.score, item.path))
        return ordered[:limit]

    def read(
        self,
        reference: EspIdfExampleReference,
    ) -> list[ProjectFile]:
        project_dir = (self._examples_path / reference.path).resolve()
        try:
            project_dir.relative_to(self._examples_path.resolve())
        except ValueError:
            return []
        if not project_dir.is_dir():
            return []

        candidates: list[Path] = []
        candidates.extend(sorted(project_dir.glob("README*"))[:1])
        top_cmake = project_dir / "CMakeLists.txt"
        if top_cmake.is_file():
            candidates.append(top_cmake)
        main_dir = project_dir / "main"
        if main_dir.is_dir():
            candidates.extend(
                path
                for path in sorted(main_dir.rglob("*"))
                if path.is_file()
                and path.suffix.casefold()
                in {".c", ".cc", ".cpp", ".h", ".hpp", ".txt"}
            )

        files: list[ProjectFile] = []
        for path in candidates:
            if len(files) >= _MAX_FILES:
                break
            content = self._read_text(path, _MAX_SOURCE_CHARS)
            if not content:
                continue
            relative_file = path.relative_to(project_dir).as_posix()
            files.append(
                ProjectFile(
                    path=f"examples/{reference.path}/{relative_file}",
                    content=content,
                )
            )
        return files
