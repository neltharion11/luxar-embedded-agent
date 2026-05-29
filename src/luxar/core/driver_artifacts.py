from __future__ import annotations

import re
import shutil
from pathlib import Path


_LOCAL_INCLUDE_RE = re.compile(r'^(?P<prefix>\s*#\s*include\s+")(?P<name>[^"]+)(?P<suffix>"\s*)$', re.MULTILINE)


def copy_driver_artifacts(
    source_header: Path,
    source_source: Path,
    target_header: Path,
    target_source: Path,
) -> tuple[str, str]:
    target_header.parent.mkdir(parents=True, exist_ok=True)
    target_source.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_header, target_header)
    source_text = source_source.read_text(encoding="utf-8")
    target_source.write_text(_align_local_header_include(source_text, target_header.name), encoding="utf-8")
    return str(target_header), str(target_source)


def _align_local_header_include(source_text: str, target_header_name: str) -> str:
    matches = list(_LOCAL_INCLUDE_RE.finditer(source_text))
    if not matches:
        return source_text
    if any(Path(match.group("name")).name == target_header_name for match in matches):
        return source_text

    first_match = matches[0]
    include_name = Path(first_match.group("name")).name
    if not include_name.lower().endswith(".h"):
        return source_text

    return (
        source_text[: first_match.start("name")]
        + target_header_name
        + source_text[first_match.end("name") :]
    )
