from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luxar.core.knowledge_base import KnowledgeBase, _embedder
from luxar.models.schemas import DocumentParseResult, KnowledgeChunk
from luxar.core.pdf_parser import PDFParser


class KnowledgeBaseTests(unittest.TestCase):
    def test_store_and_search_document_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            doc_path = Path(tmpdir) / "bmi270.txt"
            doc_path.write_text(
                "BMI270 SPI interface timing. Register map includes interrupt status and configuration bits. "
                "The initialization sequence requires reset and configuration writes.",
                encoding="utf-8",
            )
            parser = PDFParser()
            parse_result = parser.parse(str(doc_path), chunk_size=10, overlap=2)

            kb = KnowledgeBase(Path(tmpdir) / "kb")
            kb.store_document(parse_result)

            matches = kb.search("interrupt status", limit=2)
            self.assertTrue(matches)
            self.assertIn("interrupt", matches[0].content.lower())
            self.assertGreater(matches[0].score, 0.0)

            summary = kb.summarize_query("configuration writes", limit=2)
            self.assertIn("bmi270", summary.lower())

    def test_search_prefers_semantically_closer_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            kb = KnowledgeBase(Path(tmpdir) / "kb")
            doc = DocumentParseResult(
                success=True,
                source_path=str(Path(tmpdir) / "sensor.txt"),
                document_id="doc-1",
                title="sensor",
                extracted_text="",
                chunk_count=2,
                chunks=[
                    KnowledgeChunk(
                        doc_id="doc-1",
                        chunk_id="chunk-a",
                        source_path=str(Path(tmpdir) / "sensor.txt"),
                        title="sensor",
                        content="interrupt status register clears on read after motion event detection gyroscope",
                        keywords=["interrupt", "status", "motion"],
                    ),
                    KnowledgeChunk(
                        doc_id="doc-1",
                        chunk_id="chunk-b",
                        source_path=str(Path(tmpdir) / "sensor.txt"),
                        title="sensor",
                        content="interrupt line requires reset timing before power mode transition voltage",
                        keywords=["interrupt", "reset", "timing"],
                    ),
                ],
                summary="",
            )
            kb.store_document(doc)

            matches = kb.search("interrupt status motion", limit=2)
            self.assertEqual("chunk-a", matches[0].chunk_id)
            self.assertGreater(matches[0].score, 0.0)
            if len(matches) > 1:
                self.assertGreater(matches[0].score, matches[1].score)

    def test_search_with_dense_vector_prefers_semantic_match(self) -> None:
        if not _embedder.available:
            self.skipTest("sentence-transformers not available")
        with tempfile.TemporaryDirectory() as tmpdir:
            kb = KnowledgeBase(Path(tmpdir) / "kb")
            doc = DocumentParseResult(
                success=True,
                source_path=str(Path(tmpdir) / "accel.txt"),
                document_id="doc-2",
                title="accelerometer",
                extracted_text="",
                chunk_count=2,
                chunks=[
                    KnowledgeChunk(
                        doc_id="doc-2",
                        chunk_id="gyro-chunk",
                        source_path=str(Path(tmpdir) / "accel.txt"),
                        title="accelerometer",
                        content="gyroscope measures angular velocity and rotation rate",
                        keywords=["gyroscope", "angular", "velocity"],
                    ),
                    KnowledgeChunk(
                        doc_id="doc-2",
                        chunk_id="accel-chunk",
                        source_path=str(Path(tmpdir) / "accel.txt"),
                        title="accelerometer",
                        content="accelerometer measures linear acceleration and tilt angle",
                        keywords=["accelerometer", "linear", "tilt"],
                    ),
                ],
                summary="",
            )
            kb.store_document(doc)

            matches = kb.search("linear acceleration", limit=2)
            self.assertEqual("accel-chunk", matches[0].chunk_id)

    def test_search_empty_query_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            kb = KnowledgeBase(Path(tmpdir) / "kb")
            self.assertEqual([], kb.search(""))
            self.assertEqual([], kb.search("   "))

    def test_stats_returns_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            doc_path = Path(tmpdir) / "test.txt"
            doc_path.write_text("word content for testing", encoding="utf-8")
            parser = PDFParser()
            parse_result = parser.parse(str(doc_path))

            kb = KnowledgeBase(Path(tmpdir) / "kb")
            kb.store_document(parse_result)

            stats = kb.stats()
            self.assertEqual(1, stats["documents"])
            self.assertGreater(stats["chunks"], 0)

    def test_store_same_document_twice_updates_instead_of_duplicating(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            doc_path = Path(tmpdir) / "dup.txt"
            doc_path.write_text("first version content", encoding="utf-8")
            parser = PDFParser()
            result1 = parser.parse(str(doc_path))

            kb = KnowledgeBase(Path(tmpdir) / "kb")
            kb.store_document(result1)
            stats1 = kb.stats()

            doc_path.write_text("second version content with more words here now", encoding="utf-8")
            result2 = parser.parse(str(doc_path))
            kb.store_document(result2)
            stats2 = kb.stats()

            self.assertEqual(stats1["documents"], stats2["documents"])

    def test_search_register_address_after_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            doc_path = Path(tmpdir) / "regmap.txt"
            doc_path.write_text(
                "| Address | Register | Description       |\n"
                "| 0x00    | REG_CTRL | Main control reg  |\n"
                "| 0x04    | REG_STATUS | Status flags    |\n"
                "| 0x08    | REG_DATA | Data output        |\n\n"
                "The initialization sequence requires reset and configuration writes.",
                encoding="utf-8",
            )
            parser = PDFParser()
            parse_result = parser.parse(str(doc_path), chunk_size=200, overlap=0)

            kb = KnowledgeBase(Path(tmpdir) / "kb")
            kb.store_document(parse_result)

            matches = kb.search("0x04 REG_STATUS", limit=2)
            self.assertTrue(matches)
            self.assertIn("0x04", matches[0].content)
            self.assertIn("REG_STATUS", matches[0].content)

    def test_search_pin_function_after_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            doc_path = Path(tmpdir) / "pins.txt"
            doc_path.write_text(
                "| Pin | Signal     | Alternate Function |\n"
                "| PA0 | SPI1_SCK   | AF5                |\n"
                "| PA1 | SPI1_MISO  | AF5                |\n"
                "| PA2 | SPI1_MOSI  | AF5                |\n\n"
                "SPI1 is used for sensor communication.",
                encoding="utf-8",
            )
            parser = PDFParser()
            parse_result = parser.parse(str(doc_path), chunk_size=200, overlap=0)

            kb = KnowledgeBase(Path(tmpdir) / "kb")
            kb.store_document(parse_result)

            matches = kb.search("PA1 SPI1_MISO", limit=2)
            self.assertTrue(matches)
            self.assertIn("PA1", matches[0].content)


if __name__ == "__main__":
    unittest.main()

