from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luxar.core.asset_reuse import AssetReuseAdvisor
from luxar.core.driver_library import DriverLibrary
from luxar.core.knowledge_base import KnowledgeBase
from luxar.models.schemas import DocumentParseResult, DriverMetadata, KnowledgeChunk


class AssetReuseAdvisorTests(unittest.TestCase):
    def test_build_context_collects_driver_knowledge_and_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            driver_root = root / "driver_library"
            skill_root = root / "skill_library"
            protocol_dir = skill_root / "protocols" / "spi"
            protocol_dir.mkdir(parents=True, exist_ok=True)
            (protocol_dir / "SKILL.md").write_text("# SPI Protocol Skill\nUse callback injection.\n", encoding="utf-8")

            source = root / "bmi270.c"
            header = root / "bmi270.h"
            source.write_text("int bmi270_init(void){return 0;}\n", encoding="utf-8")
            header.write_text("int bmi270_init(void);\n", encoding="utf-8")
            DriverLibrary(driver_root).store_driver(
                DriverMetadata(
                    name="bmi270",
                    protocol="SPI",
                    chip="BMI270",
                    vendor="bosch",
                    device="bmi270",
                    path=str(source),
                    header_path=str(header),
                    source_path=str(source),
                    review_passed=True,
                    source_doc="bmi270 spi summary",
                )
            )

            kb = KnowledgeBase(driver_root / "knowledge_base")
            kb.store_document(
                DocumentParseResult(
                    success=True,
                    source_path=str(root / "bmi270.txt"),
                    document_id="bmi270-doc",
                    title="bmi270",
                    extracted_text="",
                    chunk_count=1,
                    chunks=[
                        KnowledgeChunk(
                            doc_id="bmi270-doc",
                            chunk_id="bmi270-doc-chunk-0000",
                            source_path=str(root / "bmi270.txt"),
                            title="bmi270",
                            content="interrupt status register clears on read",
                            keywords=["interrupt", "status"],
                        )
                    ],
                    summary="interrupt status register clears on read",
                )
            )

            advisor = AssetReuseAdvisor(
                project_root=root,
                driver_library_root=driver_root,
                skill_library_root=skill_root,
            )
            context = advisor.build_context(
                chip="BMI270",
                interface="SPI",
                vendor="bosch",
                device="bmi270",
                register_summary="interrupt status",
            )

            self.assertIn("可复用驱动库", context["summary"])
            self.assertIn("知识库片段", context["summary"])
            self.assertIn("协议技能摘要", context["summary"])
            self.assertTrue(context["sources"])
            self.assertIsNotNone(context["reuse_candidate"])

    def test_select_reuse_candidate_requires_reviewed_exact_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            driver_root = root / "driver_library"
            source = root / "bmi270.c"
            header = root / "bmi270.h"
            source.write_text("int bmi270_init(void){return 0;}\n", encoding="utf-8")
            header.write_text("int bmi270_init(void);\n", encoding="utf-8")
            library = DriverLibrary(driver_root)
            library.store_driver(
                DriverMetadata(
                    name="bmi270",
                    protocol="SPI",
                    chip="BMI270",
                    vendor="bosch",
                    device="bmi270",
                    path=str(source),
                    header_path=str(header),
                    source_path=str(source),
                    review_passed=True,
                )
            )
            advisor = AssetReuseAdvisor(
                project_root=root,
                driver_library_root=driver_root,
                skill_library_root=root / "skill_library",
            )
            candidate = advisor.select_reuse_candidate(
                chip="BMI270",
                interface="SPI",
                vendor="bosch",
                device="bmi270",
            )
            self.assertIsNotNone(candidate)
            self.assertEqual("bmi270", candidate.name)

    def test_select_reuse_candidate_rejects_low_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            driver_root = root / "driver_library"
            source = root / "other.c"
            header = root / "other.h"
            source.write_text("int other_init(void){return 0;}\n", encoding="utf-8")
            header.write_text("int other_init(void);\n", encoding="utf-8")
            library = DriverLibrary(driver_root)
            library.store_driver(
                DriverMetadata(
                    name="other",
                    protocol="SPI",
                    chip="OTHER",
                    vendor="vendor_x",
                    device="other",
                    path=str(source),
                    header_path=str(header),
                    source_path=str(source),
                    review_passed=True,
                )
            )
            advisor = AssetReuseAdvisor(
                project_root=root,
                driver_library_root=driver_root,
                skill_library_root=root / "skill_library",
            )
            candidate = advisor.select_reuse_candidate(
                chip="BMI270",
                interface="I2C",
                vendor="bosch",
                device="bmi270",
            )
            self.assertIsNone(candidate)

    def test_build_context_returns_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            driver_root = root / "driver_library"
            skill_root = root / "skill_library"
            (skill_root / "protocols" / "spi").mkdir(parents=True, exist_ok=True)
            (skill_root / "protocols" / "spi" / "SKILL.md").write_text("# SPI", encoding="utf-8")
            source = root / "bmi270.c"
            header = root / "bmi270.h"
            source.write_text("int bmi270_init(void){return 0;}\n", encoding="utf-8")
            header.write_text("int bmi270_init(void);\n", encoding="utf-8")
            library = DriverLibrary(driver_root)
            library.store_driver(
                DriverMetadata(
                    name="bmi270", protocol="SPI", chip="BMI270",
                    vendor="bosch", device="bmi270",
                    path=str(source), header_path=str(header),
                    source_path=str(source), review_passed=True,
                )
            )
            advisor = AssetReuseAdvisor(project_root=root, driver_library_root=driver_root, skill_library_root=skill_root)
            context = advisor.build_context(chip="BMI270", interface="SPI", vendor="bosch", device="bmi270")
            self.assertIn("confidence", context)
            self.assertGreater(context["confidence"], 0.0)

    def test_select_reuse_candidate_uses_reuse_count_bonus(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            driver_root = root / "driver_library"
            source = root / "pop.c"
            header = root / "pop.h"
            source.write_text("int pop_init(void){return 0;}\n", encoding="utf-8")
            header.write_text("int pop_init(void);\n", encoding="utf-8")
            library = DriverLibrary(driver_root)
            library.store_driver(
                DriverMetadata(
                    name="pop", protocol="SPI", chip="POP",
                    vendor="v", device="pop",
                    path=str(source), header_path=str(header),
                    source_path=str(source), review_passed=True,
                    reuse_count=5, kb_score=0.8,
                )
            )
            advisor = AssetReuseAdvisor(project_root=root, driver_library_root=driver_root, skill_library_root=root / "skill_library")
            candidate = advisor.select_reuse_candidate(chip="POP", interface="SPI", vendor="v", device="pop")
            self.assertIsNotNone(candidate)


if __name__ == "__main__":
    unittest.main()

