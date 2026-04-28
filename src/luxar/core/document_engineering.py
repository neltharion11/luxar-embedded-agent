from __future__ import annotations

import re
from pathlib import Path

from luxar.core.knowledge_base import KnowledgeBase
from luxar.core.pdf_parser import PDFParser
from luxar.models.schemas import (
    BringupStep,
    BusRequirement,
    EngineeringContext,
    PinRequirement,
    ProtocolFrameHint,
)


class DocumentEngineeringAnalyzer:
    def __init__(self, knowledge_root: str | Path):
        self.knowledge_root = Path(knowledge_root).resolve()
        self.kb = KnowledgeBase(self.knowledge_root)
        self.parser = PDFParser()

    def analyze(
        self,
        *,
        docs: list[str],
        query: str = "",
        store: bool = True,
        limit: int = 8,
    ) -> EngineeringContext:
        summaries: list[str] = []
        sources: list[str] = []
        parse_errors: list[str] = []
        for doc in docs:
            result = self.parser.parse(source_path=doc)
            sources.append(str(Path(doc).resolve()))
            if result.success:
                summaries.append(result.summary)
                if store:
                    self.kb.store_document(result)
            else:
                parse_errors.append(f"{doc}: {result.error}")

        effective_query = query.strip()
        matches = self.kb.search(effective_query, limit=limit) if effective_query else []
        joined_text = "\n".join(
            [text for text in summaries if text] + [item.content for item in matches]
        ).strip()
        if not joined_text:
            joined_text = "\n".join(summaries).strip()

        pin_requirements = self._extract_pin_requirements(joined_text)
        bus_requirements = self._extract_bus_requirements(joined_text)
        protocol_frames = self._extract_protocol_frames(joined_text)
        register_hints = self._extract_register_hints(joined_text)
        bringup_sequence = self._extract_bringup_steps(joined_text)
        timing_constraints = self._extract_timing_constraints(joined_text)
        integration_notes = self._extract_integration_notes(joined_text, pin_requirements, bus_requirements)
        risk_notes = self._extract_risk_notes(joined_text, pin_requirements, bus_requirements)

        return EngineeringContext(
            source_documents=sources,
            document_summary=self._summarize(joined_text),
            pin_requirements=pin_requirements,
            bus_requirements=bus_requirements,
            protocol_frames=protocol_frames,
            register_hints=register_hints,
            bringup_sequence=bringup_sequence,
            timing_constraints=timing_constraints,
            integration_notes=integration_notes,
            risk_notes=risk_notes,
            raw_matches=matches,
            parse_errors=parse_errors,
        )

    def _extract_pin_requirements(self, text: str) -> list[PinRequirement]:
        roles = [
            ("CS", r"\b(cs|chip select|ncs)\b"),
            ("INT", r"\b(int|irq|interrupt)\b"),
            ("RST", r"\b(rst|reset)\b"),
            ("SCL", r"\b(scl)\b"),
            ("SDA", r"\b(sda)\b"),
            ("MOSI", r"\b(mosi|sdi)\b"),
            ("MISO", r"\b(miso|sdo)\b"),
            ("SCK", r"\b(sck|clk|sclk)\b"),
            ("TX", r"\b(tx)\b"),
            ("RX", r"\b(rx)\b"),
        ]
        pins: list[PinRequirement] = []
        lowered = text.lower()
        for name, pattern in roles:
            if re.search(pattern, lowered):
                pins.append(PinRequirement(name=name, role=name, required=True, notes=f"Detected {name} signal requirement from documentation context."))
        return pins

    def _extract_bus_requirements(self, text: str) -> list[BusRequirement]:
        buses: list[BusRequirement] = []
        lowered = text.lower()
        if "spi" in lowered:
            mode_match = re.search(r"\bmode\s*([0-3])\b", lowered)
            speed_match = re.search(r"(\d+(?:\.\d+)?\s*(?:mhz|khz))", lowered)
            buses.append(
                BusRequirement(
                    interface="SPI",
                    mode=f"mode {mode_match.group(1)}" if mode_match else "",
                    speed_hint=speed_match.group(1) if speed_match else "",
                    direction="full-duplex" if "full duplex" in lowered else "",
                    notes="Detected SPI bus requirement from documentation context.",
                )
            )
        if "i2c" in lowered or "iic" in lowered:
            speed_match = re.search(r"(\d+\s*(?:khz))", lowered)
            buses.append(
                BusRequirement(
                    interface="I2C",
                    speed_hint=speed_match.group(1) if speed_match else "",
                    notes="Detected I2C bus requirement from documentation context.",
                )
            )
        if "uart" in lowered or "serial" in lowered:
            baud_match = re.search(r"(\d{4,7}\s*baud)", lowered)
            buses.append(
                BusRequirement(
                    interface="UART",
                    speed_hint=baud_match.group(1) if baud_match else "",
                    direction="txrx" if ("tx" in lowered and "rx" in lowered) else "",
                    notes="Detected UART bus requirement from documentation context.",
                )
            )
        return buses

    def _extract_protocol_frames(self, text: str) -> list[ProtocolFrameHint]:
        frames: list[ProtocolFrameHint] = []
        lowered = text.lower()
        if re.search(r"\bread\b.*\bwrite\b|\bwrite\b.*\bread\b", lowered):
            frames.append(
                ProtocolFrameHint(
                    direction="txrx",
                    summary="Documentation mentions paired read/write transaction flow.",
                    notes="Review command/address and payload ordering before implementing the transport.",
                )
            )
        if "command" in lowered:
            frames.append(
                ProtocolFrameHint(
                    direction="command",
                    summary="Documentation mentions command-based framing.",
                    notes="Expect a command byte or command field before payload transfer.",
                )
            )
        if "response" in lowered or "status" in lowered:
            frames.append(
                ProtocolFrameHint(
                    direction="response",
                    summary="Documentation mentions response or status frame handling.",
                    notes="Validate response length and status bits in the driver.",
                )
            )
        return frames

    def _extract_register_hints(self, text: str) -> list[str]:
        hints = re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", text)
        output: list[str] = []
        for item in hints:
            if item in {"SPI", "I2C", "UART", "GPIO", "INT", "RST", "TX", "RX"}:
                continue
            if item not in output:
                output.append(item)
            if len(output) >= 12:
                break
        return output

    def _extract_bringup_steps(self, text: str) -> list[BringupStep]:
        lowered = text.lower()
        steps: list[BringupStep] = []
        if "reset" in lowered:
            steps.append(BringupStep(step="Reset the device or assert its reset sequence before first access."))
        if "chip id" in lowered or "device id" in lowered or "who_am_i" in lowered:
            steps.append(BringupStep(step="Read and verify the device identification register during bring-up."))
        if "init" in lowered or "configure" in lowered:
            steps.append(BringupStep(step="Apply the documented initialization/configuration sequence before normal operation."))
        return steps

    def _extract_timing_constraints(self, text: str) -> list[str]:
        constraints: list[str] = []
        for match in re.findall(r"\b\d+(?:\.\d+)?\s*(?:ms|us|ns|mhz|khz)\b", text.lower()):
            if match not in constraints:
                constraints.append(match)
        return constraints[:10]

    def _extract_integration_notes(
        self,
        text: str,
        pins: list[PinRequirement],
        buses: list[BusRequirement],
    ) -> list[str]:
        notes: list[str] = []
        for bus in buses:
            notes.append(f"Configure one {bus.interface} peripheral and keep its HAL integration point available to generated code.")
        for pin in pins:
            if pin.name in {"CS", "INT", "RST"}:
                notes.append(f"Expose a dedicated {pin.name} integration hook instead of hardcoding board-specific pin numbers.")
        if not notes and text.strip():
            notes.append("Review the provided documentation and expose any required peripheral integration hooks to App code.")
        return self._dedupe(notes)

    def _extract_risk_notes(
        self,
        text: str,
        pins: list[PinRequirement],
        buses: list[BusRequirement],
    ) -> list[str]:
        risks: list[str] = []
        if pins:
            risks.append("Documentation-derived pin roles do not guarantee physical MCU pin numbers; board-specific mapping still needs confirmation.")
        if buses:
            risks.append("Detected bus requirements may still need manual confirmation for instance selection, speed, and CubeMX/firmware routing.")
        if "timing" in text.lower() or "delay" in text.lower():
            risks.append("Initialization and transaction timing must be validated against the datasheet before flashing hardware.")
        return self._dedupe(risks)

    def _summarize(self, text: str) -> str:
        normalized = " ".join(text.split())
        return normalized[:1200]

    def _dedupe(self, items: list[str]) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for item in items:
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            output.append(normalized)
        return output
