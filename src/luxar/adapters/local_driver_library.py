"""Filesystem-backed, immutable public driver packages."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from luxar.domain.drivers import (
    DriverCandidate,
    DriverFile,
    DriverManifest,
    DriverPackage,
    DriverPublishSpec,
    DriverVerification,
)


class DriverLibraryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class LocalDriverLibrary:
    """Keep exact source packages separate from project-scoped vector knowledge."""

    _MAX_FILE_BYTES = 1_048_576
    _MAX_PACKAGE_BYTES = 4 * 1_048_576
    _ALLOWED_NAMES = {"CMakeLists.txt", "Kconfig", "Kconfig.projbuild"}
    _ALLOWED_SUFFIXES = {
        ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".inc",
        ".md", ".txt", ".cmake", ".json", ".yml", ".yaml",
    }

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self._lock = threading.RLock()

    @staticmethod
    def _linked(path: Path) -> bool:
        return path.is_symlink() or (
            hasattr(path, "is_junction") and path.is_junction()
        )

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self._linked(self.root):
            raise DriverLibraryError("unsafe_root", "公共驱动库目录不能是链接")

    @classmethod
    def _contains_link(cls, path: Path, base: Path) -> bool:
        """Check each lexical path component before resolve() hides links."""

        try:
            relative = path.relative_to(base)
        except ValueError:
            return True
        current = base
        for part in relative.parts:
            current = current / part
            if current.exists() and cls._linked(current):
                return True
        return False

    @classmethod
    def _relative_path(cls, raw: str) -> PurePosixPath:
        value = raw.strip().replace("\\", "/")
        path = PurePosixPath(value)
        if (
            not value
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or ":" in path.parts[0]
        ):
            raise DriverLibraryError("invalid_path", "驱动文件必须是安全的项目相对路径")
        if path.name not in cls._ALLOWED_NAMES and path.suffix.casefold() not in cls._ALLOWED_SUFFIXES:
            raise DriverLibraryError("invalid_file_type", f"驱动文件类型不受支持：{path.name}")
        return path

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {
            token
            for token in re.split(r"[^a-z0-9]+", value.casefold())
            if len(token) >= 2
        }

    def _manifests(self) -> list[DriverManifest]:
        if not self.root.is_dir():
            return []
        manifests: list[DriverManifest] = []
        for driver_dir in sorted(self.root.iterdir(), key=lambda item: item.name.casefold()):
            if not driver_dir.is_dir() or driver_dir.name.startswith(".") or self._linked(driver_dir):
                continue
            for version_dir in sorted(driver_dir.iterdir(), key=lambda item: item.name.casefold()):
                manifest_path = version_dir / "manifest.json"
                if not version_dir.is_dir() or self._linked(version_dir) or not manifest_path.is_file():
                    continue
                try:
                    manifest = DriverManifest.model_validate_json(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    if (
                        manifest.driver_id != driver_dir.name
                        or manifest.version != version_dir.name
                    ):
                        continue
                    manifests.append(manifest)
                except (OSError, UnicodeError, ValidationError):
                    continue
        return manifests

    def _latest_manifests(self) -> list[DriverManifest]:
        """Return one current version per driver for search and dashboard counts."""

        latest: dict[str, DriverManifest] = {}
        for manifest in self._manifests():
            current = latest.get(manifest.driver_id)
            if current is None or (manifest.published_at, manifest.version) > (
                current.published_at,
                current.version,
            ):
                latest[manifest.driver_id] = manifest
        return list(latest.values())

    def search(
        self,
        *,
        query: str = "",
        hardware: str = "",
        protocol: str = "",
        target_chip: str = "",
        limit: int = 20,
    ) -> list[DriverCandidate]:
        if not 1 <= limit <= 100:
            raise DriverLibraryError("invalid_limit", "驱动检索数量必须在 1 到 100 之间")
        query_tokens = self._tokens(query)
        hardware_text = hardware.strip().casefold()
        protocol_text = protocol.strip().casefold()
        target_text = target_chip.strip().casefold()
        candidates: list[DriverCandidate] = []
        for manifest in self._latest_manifests():
            searchable = " ".join([
                manifest.driver_id,
                manifest.name,
                manifest.vendor,
                manifest.hardware,
                *manifest.protocols,
                *manifest.targets,
                manifest.description,
            ]).casefold()
            score = 0.0
            reasons: list[str] = []
            if hardware_text:
                if hardware_text in {manifest.hardware.casefold(), manifest.driver_id.casefold()}:
                    score += 100
                    reasons.append("硬件精确匹配")
                elif hardware_text in searchable:
                    score += 60
                    reasons.append("硬件名称匹配")
            if protocol_text:
                if protocol_text in {item.casefold() for item in manifest.protocols}:
                    score += 80
                    reasons.append("协议精确匹配")
                elif protocol_text in searchable:
                    score += 35
                    reasons.append("协议描述匹配")
            if target_text and target_text in {item.casefold() for item in manifest.targets}:
                score += 20
                reasons.append("目标芯片匹配")
            matched_tokens = sorted(token for token in query_tokens if token in searchable)
            if matched_tokens:
                score += min(40, len(matched_tokens) * 5)
                reasons.append("任务关键词：" + "、".join(matched_tokens[:6]))
            if manifest.verification.hardware_verified:
                score += 8
            elif manifest.verification.build_verified:
                score += 4
            filtered = bool(query_tokens or hardware_text or protocol_text or target_text)
            if filtered and score <= 0:
                continue
            candidates.append(DriverCandidate(
                driver_id=manifest.driver_id,
                version=manifest.version,
                name=manifest.name,
                vendor=manifest.vendor,
                hardware=manifest.hardware,
                protocols=list(manifest.protocols),
                targets=list(manifest.targets),
                description=manifest.description,
                verification=manifest.verification,
                files=list(manifest.files),
                score=score,
                match_reasons=reasons,
            ))
        candidates.sort(key=lambda item: (-item.score, item.driver_id, item.version), reverse=False)
        return candidates[:limit]

    def read(self, driver_id: str, version: str | None = None) -> DriverPackage:
        matches = [item for item in self._manifests() if item.driver_id == driver_id and (version is None or item.version == version)]
        if not matches:
            raise DriverLibraryError("not_found", "公共驱动不存在")
        manifest = sorted(matches, key=lambda item: (item.published_at, item.version), reverse=True)[0]
        package_root = self.root / manifest.driver_id / manifest.version
        try:
            files_root = (package_root / "files").resolve(strict=True)
        except FileNotFoundError as error:
            raise DriverLibraryError("corrupt_package", "驱动包源码目录不存在") from error
        sources: dict[str, str] = {}
        total = 0
        for file in manifest.files:
            relative = self._relative_path(file.path)
            lexical_source = files_root / Path(*relative.parts)
            if self._contains_link(lexical_source, files_root):
                raise DriverLibraryError("unsafe_package", "驱动包包含不安全链接")
            try:
                source = lexical_source.resolve(strict=True)
            except FileNotFoundError as error:
                raise DriverLibraryError(
                    "corrupt_package", "驱动包清单引用的文件不存在"
                ) from error
            try:
                source.relative_to(files_root)
            except ValueError as error:
                raise DriverLibraryError("unsafe_package", "驱动包文件越出库目录") from error
            if self._linked(source) or not source.is_file():
                raise DriverLibraryError("unsafe_package", "驱动包包含不安全文件")
            data = source.read_bytes()
            total += len(data)
            if total > self._MAX_PACKAGE_BYTES or hashlib.sha256(data).hexdigest() != file.sha256:
                raise DriverLibraryError("corrupt_package", "驱动包内容校验失败")
            try:
                sources[file.path] = data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise DriverLibraryError("corrupt_package", "驱动包源码不是 UTF-8 文本") from error
        return DriverPackage(manifest=manifest, sources=sources)

    def publish(
        self,
        *,
        project_path: Path,
        project_key: str,
        spec: DriverPublishSpec,
        verification: DriverVerification,
    ) -> DriverManifest:
        try:
            project_root = project_path.expanduser().resolve(strict=True)
        except FileNotFoundError as error:
            raise DriverLibraryError("invalid_project", "驱动来源项目目录不存在") from error
        if not project_root.is_dir() or self._linked(project_root):
            raise DriverLibraryError("invalid_project", "驱动来源项目目录无效")
        files: list[tuple[PurePosixPath, bytes, DriverFile]] = []
        total = 0
        for raw_path in spec.file_paths:
            relative = self._relative_path(raw_path)
            lexical_source = project_root / Path(*relative.parts)
            if self._contains_link(lexical_source, project_root):
                raise DriverLibraryError("invalid_path", "驱动文件不能经过链接或联接")
            try:
                source = lexical_source.resolve(strict=True)
            except FileNotFoundError as error:
                raise DriverLibraryError(
                    "invalid_path", f"驱动文件不存在：{relative}"
                ) from error
            try:
                source.relative_to(project_root)
            except ValueError as error:
                raise DriverLibraryError("invalid_path", "驱动文件越出当前项目") from error
            if self._linked(source) or not source.is_file():
                raise DriverLibraryError("invalid_path", "驱动文件不存在或是不安全链接")
            data = source.read_bytes()
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise DriverLibraryError("invalid_source", "驱动源码必须是 UTF-8 文本") from error
            if len(data) > self._MAX_FILE_BYTES:
                raise DriverLibraryError("package_too_large", f"驱动文件超过 1 MiB：{relative}")
            total += len(data)
            if total > self._MAX_PACKAGE_BYTES:
                raise DriverLibraryError("package_too_large", "驱动包总大小超过 4 MiB")
            digest = hashlib.sha256(data).hexdigest()
            files.append((relative, data, DriverFile(path=relative.as_posix(), size=len(data), sha256=digest)))
        digest_input = json.dumps(
            {
                "driver_id": spec.driver_id,
                "version": spec.version,
                "name": spec.name,
                "vendor": spec.vendor,
                "hardware": spec.hardware,
                "protocols": spec.protocols,
                "targets": spec.targets,
                "description": spec.description,
                "files": [item.model_dump(mode="json") for _, _, item in files],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        content_hash = hashlib.sha256(digest_input).hexdigest()
        manifest = DriverManifest(
            driver_id=spec.driver_id,
            version=spec.version,
            name=spec.name.strip(),
            vendor=spec.vendor.strip(),
            hardware=spec.hardware.strip(),
            protocols=list(spec.protocols),
            targets=list(spec.targets),
            description=spec.description.strip(),
            files=[item for _, _, item in files],
            verification=verification,
            source_project_key=project_key,
            content_hash=content_hash,
            published_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._ensure_root()
            target = self.root / spec.driver_id / spec.version
            existing_manifest = target / "manifest.json"
            if existing_manifest.is_file():
                try:
                    existing = DriverManifest.model_validate_json(existing_manifest.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, ValidationError) as error:
                    raise DriverLibraryError("corrupt_package", "已存在的驱动版本清单无效") from error
                if existing.content_hash == manifest.content_hash:
                    return existing
                raise DriverLibraryError("version_conflict", "相同驱动版本已存在且内容不同")
            staging = self.root / f".publish-{uuid.uuid4().hex}"
            try:
                for relative, data, _ in files:
                    destination = staging / "files" / Path(*relative.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
                (staging / "manifest.json").write_text(
                    manifest.model_dump_json(indent=2),
                    encoding="utf-8",
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, target)
            except OSError as error:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
                raise DriverLibraryError("write_failed", "公共驱动包写入失败") from error
        return manifest


__all__ = ["DriverLibraryError", "LocalDriverLibrary"]
