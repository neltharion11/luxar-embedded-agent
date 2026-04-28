from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luxar.core.pdf_parser import PDFParser


class PDFParserTests(unittest.TestCase):
    def test_parse_text_document_into_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sensor.txt"
            path.write_text(
                "BMI270 register map. SPI timing requires CS setup before clock. "
                "Interrupt status register reports motion events. "
                "Configure initialization sequence before normal mode.",
                encoding="utf-8",
            )
            parser = PDFParser()
            result = parser.parse(str(path), chunk_size=8, overlap=2)

            self.assertTrue(result.success)
            self.assertGreaterEqual(result.chunk_count, 1)
            self.assertIn("BMI270", result.extracted_text)
            self.assertTrue(result.chunks[0].keywords)

    def test_parse_missing_document_returns_error(self) -> None:
        parser = PDFParser()
        result = parser.parse("C:/does/not/exist.pdf")
        self.assertFalse(result.success)
        self.assertIn("Document not found", result.error)

    def test_paragraph_based_chunking_preserves_paragraph_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "multi_para.txt"
            path.write_text(
                "This is the first paragraph about SPI interface timing and configuration. "
                "It has multiple sentences for testing purposes.\n\n"
                "This is a completely separate paragraph about interrupt handling. "
                "Interrupt status register and clear on read are important.\n\n"
                "Third paragraph about initialization sequence. "
                "Reset and configuration writes before normal mode.",
                encoding="utf-8",
            )
            parser = PDFParser()
            result = parser.parse(str(path), chunk_size=10, overlap=0)

            self.assertTrue(result.success)
            self.assertGreaterEqual(result.chunk_count, 3)
            self.assertIn("first paragraph", result.chunks[0].content.lower())
            self.assertIn("separate paragraph", result.chunks[1].content.lower())
            self.assertIn("third paragraph", result.chunks[2].content.lower())

    def test_large_chunk_size_merges_paragraphs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "merge_para.txt"
            path.write_text(
                "Short paragraph one.\n\n"
                "Short paragraph two about SPI.\n\n"
                "Short paragraph three about I2C.",
                encoding="utf-8",
            )
            parser = PDFParser()
            result = parser.parse(str(path), chunk_size=50, overlap=0)

            self.assertTrue(result.success)
            self.assertLess(result.chunk_count, 3)

    def test_parse_empty_text_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empty.txt"
            path.write_text("   \n\n  ", encoding="utf-8")
            parser = PDFParser()
            result = parser.parse(str(path))
            self.assertFalse(result.success)
            self.assertIn("No text", result.error)

    def test_split_into_paragraphs_skips_too_short_paragraphs(self) -> None:
        parser = PDFParser()
        text = "Hello world.\n\nA\n\nThird paragraph has enough words here."
        result = parser._split_into_paragraphs(text)
        self.assertEqual(2, len(result))

    def test_unsupported_format_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.doc"
            path.write_text("some content", encoding="utf-8")
            parser = PDFParser()
            result = parser.parse(str(path))
            self.assertFalse(result.success)
            self.assertIn("Unsupported document format", result.error)

    def test_overlap_content_included_in_subsequent_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "overlap_test.txt"
            para = "word " * 50
            path.write_text(f"{para}\n\n{para}\n\n{para}", encoding="utf-8")
            parser = PDFParser()
            result = parser.parse(str(path), chunk_size=30, overlap=10)
            self.assertTrue(result.success)
            if result.chunk_count > 1:
                second = result.chunks[1].content.lower()
                first_end = result.chunks[0].content.lower().split()[-5:]
                overlap_found = any(w in second for w in first_end)
                self.assertTrue(overlap_found, "Overlap words should appear in the next chunk")

    def test_structure_register_table(self) -> None:
        parser = PDFParser()
        table = [
            ["Address", "Register", "Description"],
            ["0x00", "REG_CTRL", "Control register for SPI mode"],
            ["0x04", "REG_STATUS", "Status register with interrupt flags"],
            ["0x08", "REG_DATA", "Data register for read/write"],
        ]
        records = parser._structure_register_table(table)
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]["register_name"], "REG_CTRL")
        self.assertEqual(records[0]["address_offset"], "0x00")
        self.assertEqual(records[1]["description"], "Status register with interrupt flags")

    def test_structure_register_table_non_register_headers_returns_empty(self) -> None:
        parser = PDFParser()
        table = [
            ["Product", "Version", "Date"],
            ["SensorA", "1.0", "2024-01"],
        ]
        records = parser._structure_register_table(table)
        self.assertEqual(records, [])

    def test_structure_pin_table(self) -> None:
        parser = PDFParser()
        table = [
            ["Pin", "Signal", "Alternate Function"],
            ["PA0", "SPI1_SCK", "AF5"],
            ["PA1", "SPI1_MISO", "AF5"],
            ["PA2", "SPI1_MOSI", "AF5"],
        ]
        records = parser._structure_pin_table(table)
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]["pin"], "PA0")
        self.assertEqual(records[0]["signal"], "SPI1_SCK")
        self.assertEqual(records[0]["alternate_function"], "AF5")

    def test_structure_pin_table_non_pin_headers_returns_empty(self) -> None:
        parser = PDFParser()
        table = [
            ["Part", "Spec", "Value"],
            ["VCC", "Voltage", "3.3V"],
        ]
        records = parser._structure_pin_table(table)
        self.assertEqual(records, [])

    def test_extract_tables_from_text_pipe_delimited(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "registers.txt"
            path.write_text(
                "Some text before.\n\n"
                "| Address | Register     | Description          |\n"
                "| 0x00    | REG_CTRL     | Control register     |\n"
                "| 0x04    | REG_STATUS   | Status register      |\n"
                "| 0x08    | REG_DATA     | Data register        |\n\n"
                "Some text after.\n",
                encoding="utf-8",
            )
            parser = PDFParser()
            tables = parser._extract_tables(path)
            self.assertEqual(len(tables), 1)
            self.assertEqual(len(tables[0]), 4)
            self.assertIn("Address", tables[0][0])
            self.assertIn("REG_CTRL", tables[0][1])

    def test_extract_tables_from_text_space_aligned(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pins.txt"
            path.write_text(
                "Pin    Signal      AF\n"
                "PA0    SPI1_SCK    AF5\n"
                "PA1    SPI1_MISO   AF5\n"
                "PA2    SPI1_MOSI   AF5\n",
                encoding="utf-8",
            )
            parser = PDFParser()
            tables = parser._extract_tables(path)
            self.assertEqual(len(tables), 1)
            self.assertEqual(len(tables[0]), 4)

    def test_parse_with_register_table_in_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bmi270_regs.txt"
            content = (
                "BMI270 Register Map\n\n"
                "| Address | Register     | Description                      |\n"
                "| 0x00    | REG_CTRL     | Main control register            |\n"
                "| 0x04    | REG_STATUS   | Interrupt and status flags       |\n"
                "| 0x08    | REG_DATA     | Accelerometer data output        |\n"
                "| 0x0C    | REG_TEMP     | Temperature sensor data          |\n\n"
                "The initialization sequence requires reset and configuration writes."
            )
            path.write_text(content, encoding="utf-8")
            parser = PDFParser()
            result = parser.parse(str(path), chunk_size=200, overlap=0)
            self.assertTrue(result.success)
            combined = " ".join(c.content for c in result.chunks).lower()
            self.assertIn("0x00", combined)
            self.assertIn("reg_ctrl", combined)
            self.assertIn("bmi270", combined)

    def test_register_address_in_keywords(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "regs_keywords.txt"
            path.write_text(
                "| Address | Register | Description |\n"
                "| 0x00    | REG_A    | First reg   |\n"
                "| 0x04    | REG_B    | Second reg  |\n"
                "| 0x08    | REG_C    | Third reg   |\n",
                encoding="utf-8",
            )
            parser = PDFParser()
            result = parser.parse(str(path), chunk_size=100, overlap=0)
            self.assertTrue(result.success)
            all_keywords = set()
            for c in result.chunks:
                all_keywords.update(k.lower() for k in c.keywords)
            self.assertIn("0x00", all_keywords, "Hex address should appear in keywords")
            self.assertIn("0x04", all_keywords, "Hex address should appear in keywords")

    def test_parse_with_pin_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pins.txt"
            content = (
                "Pin Configuration\n\n"
                "| Pin | Signal     | Alternate Function |\n"
                "| PA0 | SPI1_SCK   | AF5                |\n"
                "| PA1 | SPI1_MISO  | AF5                |\n"
                "| PA2 | SPI1_MOSI  | AF5                |\n\n"
                "These pins are used for SPI communication."
            )
            path.write_text(content, encoding="utf-8")
            parser = PDFParser()
            result = parser.parse(str(path), chunk_size=200, overlap=0)
            self.assertTrue(result.success)
            combined = " ".join(c.content for c in result.chunks)
            self.assertIn("PA0", combined)
            self.assertIn("SPI1_SCK", combined)

    def test_render_register_table_text(self) -> None:
        parser = PDFParser()
        records = [
            {"register_name": "REG_CTRL", "address_offset": "0x00", "description": "Control register"},
            {"register_name": "REG_STATUS", "address_offset": "0x04", "description": "Status flags"},
        ]
        rendered = parser._render_register_table_text(records)
        self.assertIn("REG_CTRL", rendered)
        self.assertIn("0x00", rendered)
        self.assertIn("0x04", rendered)
        lines = rendered.splitlines()
        self.assertGreaterEqual(len(lines), 4)

    def test_render_pin_table_text(self) -> None:
        parser = PDFParser()
        records = [
            {"pin": "PA0", "signal": "SPI1_SCK", "alternate_function": "AF5"},
            {"pin": "PA1", "signal": "SPI1_MISO", "alternate_function": "AF5"},
        ]
        rendered = parser._render_pin_table_text(records)
        self.assertIn("PA0", rendered)
        self.assertIn("SPI1_SCK", rendered)
        lines = rendered.splitlines()
        self.assertGreaterEqual(len(lines), 4)


if __name__ == "__main__":
    unittest.main()

