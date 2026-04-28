from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from luxar.core.driver_library import DriverLibrary
from luxar.core.knowledge_base import KnowledgeBase
from luxar.models.schemas import DriverMetadata


REUSE_CONFIDENCE_THRESHOLD = 0.5


class AssetReuseAdvisor:
    def __init__(self, project_root: str | Path, driver_library_root: str | Path, skill_library_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self.driver_library = DriverLibrary(driver_library_root)
        self.knowledge_base = KnowledgeBase(Path(driver_library_root).resolve() / "knowledge_base")
        self.skill_root = Path(skill_library_root).resolve()

    def build_context(
        self,
        chip: str,
        interface: str,
        vendor: str = "",
        device: str = "",
        register_summary: str = "",
    ) -> dict:
        protocol = interface.strip().upper()
        keywords = [chip.strip(), device.strip(), vendor.strip(), protocol, register_summary.strip()]
        query = " ".join(item for item in keywords if item)

        driver_matches = self.driver_library.search_drivers(
            keyword=device.strip() or chip.strip(),
            protocol=protocol,
            vendor=vendor.strip(),
            limit=5,
        )
        knowledge_matches = self.knowledge_base.search(query=query, limit=3) if query else []
        skill_path = self.skill_root / "protocols" / interface.strip().lower() / "SKILL.md"
        skill_content = skill_path.read_text(encoding="utf-8") if skill_path.exists() else ""

        lines: list[str] = []
        sources: list[str] = []
        if driver_matches:
            lines.append("【可复用驱动库】")
            for item in driver_matches:
                lines.append(
                    f"- {item.name} | protocol={item.protocol} | chip={item.chip} | vendor={item.vendor} | review_passed={item.review_passed}"
                )
                if item.source_doc:
                    lines.append(f"  source_doc: {item.source_doc[:200]}")
                sources.append(f"driver:{item.name}")
        if knowledge_matches:
            lines.append("【知识库片段】")
            for item in knowledge_matches:
                excerpt = item.content[:240].strip()
                lines.append(f"- {item.title}: {excerpt}")
                sources.append(f"kb:{item.chunk_id}")
        if skill_content.strip():
            lines.append("【协议技能摘要】")
            lines.append(skill_content[:1200].strip())
            sources.append(f"skill:{interface.strip().lower()}")

        reuse_candidate, confidence = self._score_candidates(
            chip=chip,
            interface=interface,
            vendor=vendor,
            device=device,
            driver_matches=driver_matches,
        ) if driver_matches else (None, 0.0)

        summary = "\n".join(lines).strip()
        return {
            "summary": summary,
            "sources": sources,
            "driver_matches": [item.model_dump(mode="json") for item in driver_matches],
            "knowledge_matches": [item.model_dump(mode="json") for item in knowledge_matches],
            "skill_path": str(skill_path) if skill_path.exists() else "",
            "reuse_candidate": reuse_candidate.model_dump(mode="json") if reuse_candidate else None,
            "confidence": confidence,
        }

    def select_reuse_candidate(
        self,
        chip: str,
        interface: str,
        vendor: str = "",
        device: str = "",
        driver_matches: list[DriverMetadata] | None = None,
        threshold: float = REUSE_CONFIDENCE_THRESHOLD,
    ) -> DriverMetadata | None:
        if driver_matches is None:
            protocol = interface.strip().upper()
            normalized_device = device.strip().lower()
            normalized_chip = chip.strip().lower()
            normalized_vendor = vendor.strip().lower()
            driver_matches = self.driver_library.search_drivers(
                keyword=normalized_device or normalized_chip,
                protocol=protocol,
                vendor=normalized_vendor,
                limit=5,
            )
        candidate, confidence = self._score_candidates(
            chip=chip,
            interface=interface,
            vendor=vendor,
            device=device,
            driver_matches=driver_matches,
        )
        if candidate is not None and confidence >= threshold:
            return candidate
        return None

    def _score_candidates(
        self,
        chip: str,
        interface: str,
        vendor: str = "",
        device: str = "",
        driver_matches: Optional[list[DriverMetadata]] = None,
    ) -> tuple[Optional[DriverMetadata], float]:
        protocol = interface.strip().upper()
        normalized_chip = chip.strip().lower()
        normalized_device = device.strip().lower()
        normalized_vendor = vendor.strip().lower()
        candidates = driver_matches or []

        best_score = 0.0
        best_candidate: Optional[DriverMetadata] = None

        for item in candidates:
            if not item.review_passed:
                continue
            same_protocol = 1.0 if item.protocol.strip().upper() == protocol else 0.0
            device_match = 1.0 if normalized_device and item.device.strip().lower() == normalized_device else (0.5 if normalized_device and normalized_device in item.device.strip().lower() else 0.0)
            chip_match = 1.0 if item.chip.strip().lower() == normalized_chip else (0.5 if normalized_chip and normalized_chip in item.chip.strip().lower() else 0.0)
            vendor_ok = 1.0 if not normalized_vendor or item.vendor.strip().lower() == normalized_vendor else 0.0
            reuse_bonus = min(item.reuse_count / 10.0, 0.3)
            review_quality = 1.0 / max(item.review_issue_count + 1, 1)
            kb_factor = item.kb_score * 0.3

            score = (
                same_protocol * 0.25 +
                max(device_match, chip_match) * 0.30 +
                vendor_ok * 0.10 +
                review_quality * 0.10 +
                reuse_bonus * 0.10 +
                kb_factor * 0.15
            )

            if score > best_score:
                best_score = score
                best_candidate = item

        return best_candidate, best_score

    def materialize_reused_driver(
        self,
        candidate: DriverMetadata,
        output_dir: str | Path,
        target_stem: str,
    ) -> tuple[str, str]:
        resolved_output = Path(output_dir).resolve()
        resolved_output.mkdir(parents=True, exist_ok=True)
        source_header = Path(candidate.header_path) if candidate.header_path else None
        source_source = Path(candidate.source_path or candidate.path)
        if source_header is None or not source_header.exists() or not source_source.exists():
            raise FileNotFoundError("Reuse candidate source files are incomplete or missing.")

        target_header = resolved_output / f"{target_stem}.h"
        target_source = resolved_output / f"{target_stem}.c"
        shutil.copy2(source_header, target_header)
        shutil.copy2(source_source, target_source)
        return str(target_header), str(target_source)

