from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luxar.core.document_engineering import DocumentEngineeringAnalyzer


class DocumentEngineeringAnalyzerTests(unittest.TestCase):
    def test_extracts_roles_buses_protocol_and_bringup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            doc = Path(tmpdir) / "bmi270.md"
            doc.write_text(
                "\n".join(
                    [
                        "BMI270 uses SPI mode 0 up to 10 MHz.",
                        "Required pins: CS, INT, RST, SCK, MOSI, MISO.",
                        "Write command byte then read response payload.",
                        "Read CHIP_ID after reset and delay 10 ms before init.",
                    ]
                ),
                encoding="utf-8",
            )
            analyzer = DocumentEngineeringAnalyzer(Path(tmpdir) / "kb")
            context = analyzer.analyze(docs=[str(doc)], query="BMI270 SPI")

        pin_names = {pin.name for pin in context.pin_requirements}
        bus_names = {bus.interface for bus in context.bus_requirements}
        self.assertIn("CS", pin_names)
        self.assertIn("INT", pin_names)
        self.assertIn("SPI", bus_names)
        self.assertTrue(any("command" in frame.summary.lower() or frame.direction == "command" for frame in context.protocol_frames))
        self.assertTrue(any("reset" in step.step.lower() for step in context.bringup_sequence))
        self.assertIn("10 ms", " ".join(context.timing_constraints))

    def test_incomplete_docs_only_produce_role_level_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            doc = Path(tmpdir) / "notes.txt"
            doc.write_text("Use SPI with CS and INT. Board pins are assigned elsewhere.", encoding="utf-8")
            analyzer = DocumentEngineeringAnalyzer(Path(tmpdir) / "kb")
            context = analyzer.analyze(docs=[str(doc)], query="wiring")

        self.assertTrue(any("physical MCU pin numbers" in note for note in context.risk_notes))
        self.assertFalse(any("PA" in note or "PB" in note for note in context.integration_notes))


if __name__ == "__main__":
    unittest.main()
