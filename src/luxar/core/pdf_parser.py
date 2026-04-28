from __future__ import annotations

import hashlib
import re
from pathlib import Path

from luxar.models.schemas import DocumentParseResult, KnowledgeChunk


_REGISTER_HEADERS = {"address", "register", "name", "offset", "description", "bit", "bits", "field", "type", "reset", "access", "rw"}
_PIN_HEADERS = {"pin", "pin#", "pin number", "signal", "function", "alternate", "af", "default", "position"}
_TABLE_CANDIDATE_MIN_ROWS = 3
_TABLE_CANDIDATE_MIN_COLS = 3


class PDFParser:
    def parse(self, source_path: str, chunk_size: int = 1200, overlap: int = 120) -> DocumentParseResult:
        path = Path(source_path).resolve()
        if not path.exists():
            return DocumentParseResult(
                success=False,
                source_path=str(path),
                error=f"Document not found: {path}",
            )

        suffix = path.suffix.lower()
        try:
            if suffix == ".pdf":
                text = self._extract_pdf_text(path)
            elif suffix in {".txt", ".md"}:
                text = path.read_text(encoding="utf-8")
            elif suffix in {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"}:
                text = self._extract_image_text(path)
            else:
                return DocumentParseResult(
                    success=False,
                    source_path=str(path),
                    error=f"Unsupported document format: {path.suffix or '<none>'}",
                )
        except Exception as exc:
            return DocumentParseResult(
                success=False,
                source_path=str(path),
                error=str(exc),
            )

        normalized = self._normalize_text(text)
        if not normalized.strip():
            return DocumentParseResult(
                success=False,
                source_path=str(path),
                error="No text could be extracted from the document.",
            )

        tables = self._extract_tables(path)

        document_id = self._build_document_id(path)
        title = path.stem
        chunks = self._chunk_text(
            document_id=document_id,
            source_path=str(path),
            title=title,
            text=normalized,
            chunk_size=chunk_size,
            overlap=overlap,
            tables=tables,
        )
        summary = self._summarize_text(normalized)
        return DocumentParseResult(
            success=True,
            source_path=str(path),
            document_id=document_id,
            title=title,
            extracted_text=normalized,
            chunk_count=len(chunks),
            chunks=chunks,
            summary=summary,
        )

    def _extract_pdf_text(self, path: Path) -> str:
        extractor_errors: list[str] = []
        min_text_len = 50

        # Tier 0: Docling — AI layout-aware, produces structured Markdown
        text = self._extract_pdf_text_docling(path)
        if text and len(text.strip()) >= min_text_len:
            return text
        if text:
            extractor_errors.append("docling: produced insufficient text")

        # Tier 1: pymupdf embedded text + render-to-OCR cascade
        try:
            import fitz  # type: ignore (pymupdf)
            import io
            from PIL import Image  # type: ignore

            doc = fitz.open(str(path))
            text = "\n".join(page.get_text() for page in doc)
            if len(text.strip()) >= min_text_len:
                return text

            # Tier 2: scanned PDF — render pages and OCR with RapidOCR
            pages_text: list[str] = []
            for page in doc:
                pix = page.get_pixmap(dpi=200)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                page_text = self._ocr_image_pillow(img)
                if page_text:
                    pages_text.append(page_text)
            if pages_text and sum(len(t) for t in pages_text) >= min_text_len:
                return "\n".join(pages_text)
            extractor_errors.append("pymupdf+OCR: produced insufficient text")
        except Exception as exc:
            extractor_errors.append(f"pymupdf+OCR: {exc}")

        joined = "; ".join(extractor_errors) if extractor_errors else "No PDF extractor available."
        raise RuntimeError(
            "Unable to extract PDF text — the document may be a scanned image or have an unsupported format. "
            "Install `docling` or `pymupdf`+`rapidocr-onnxruntime` for PDF support. "
            f"Details: {joined}"
        )

    def _extract_pdf_text_docling(self, path: Path) -> str:
        """Convert PDF to Markdown using Docling's layout-aware AI models.

        Docling uses DocLayNet for layout analysis and TableFormer for table
        structure recognition, producing semantically ordered Markdown output.
        Falls back silently on any failure — callers should try the next extractor.
        """
        try:
            import os
            os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

            from docling.document_converter import DocumentConverter

            converter = DocumentConverter()
            result = converter.convert(str(path))
            markdown = result.document.export_to_markdown()
            if markdown and markdown.strip():
                return markdown.strip()
            return ""
        except ImportError:
            return ""
        except Exception:
            return ""

    def _extract_image_text(self, path: Path) -> str:
        """Extract text from image files using RapidOCR."""
        text = self._ocr_with_rapidocr(path)
        if text:
            return text
        raise RuntimeError(
            "Unable to OCR image — no text produced. "
            "Ensure `rapidocr-onnxruntime` is installed."
        )

    def _ocr_with_rapidocr(self, path: Path) -> str:
        """OCR an image with RapidOCR (PaddleOCR ONNX runtime). Returns empty str on failure."""
        try:
            from rapidocr_onnxruntime import RapidOCR

            engine = RapidOCR()
            result, _elapse = engine(str(path), use_det=True, use_cls=True, use_rec=True)
            if not result:
                return ""

            return self._format_rapidocr_result(result)
        except ImportError:
            return ""
        except Exception:
            return ""

    def _ocr_image_pillow(self, img: object) -> str:
        """OCR a PIL Image with RapidOCR. Returns empty str on failure."""
        try:
            from rapidocr_onnxruntime import RapidOCR
            import numpy as np

            engine = RapidOCR()
            img_array = np.array(img)
            result, _elapse = engine(img_array, use_det=True, use_cls=True, use_rec=True)
            if not result:
                return ""

            return self._format_rapidocr_result(result)
        except ImportError:
            return ""
        except Exception:
            return ""

    def _format_rapidocr_result(self, result: list) -> str:
        """Group RapidOCR detections into ordered lines by Y-coordinate."""
        lines: dict[int, list[tuple[float, str]]] = {}
        for box, text, score in result:
            try:
                s = float(score)
            except (ValueError, TypeError):
                s = 0.0
            if s < 0.5:
                continue
            if not text or not text.strip():
                continue
            y_center = (box[0][1] + box[2][1]) / 2
            y_key = round(y_center / 30) * 30
            if y_key not in lines:
                lines[y_key] = []
            lines[y_key].append((box[0][0], text.strip()))

        ordered: list[str] = []
        for yk in sorted(lines.keys()):
            line_parts = sorted(lines[yk], key=lambda x: x[0])
            ordered.append(" ".join(t for _, t in line_parts))

        return "\n".join(ordered)

    def _extract_tables(self, path: Path) -> list[list[list[str]]]:
        suffix = path.suffix.lower()
        if suffix == ".txt":
            return self._extract_tables_from_text(path)
        if suffix not in (".pdf",):
            return []  # Images/OCR output has no structured tables
        return self._extract_tables_from_pdf(path)

    def _extract_tables_from_text(self, path: Path) -> list[list[list[str]]]:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return []
        lines = text.splitlines()
        tables: list[list[list[str]]] = []
        i = 0
        while i < len(lines):
            row = self._parse_text_table_row(lines[i])
            if row and len(row) >= _TABLE_CANDIDATE_MIN_COLS:
                table: list[list[str]] = [row]
                i += 1
                while i < len(lines):
                    next_row = self._parse_text_table_row(lines[i])
                    if next_row and len(next_row) == len(row):
                        table.append(next_row)
                        i += 1
                    else:
                        break
                if len(table) >= _TABLE_CANDIDATE_MIN_ROWS:
                    tables.append(table)
            else:
                i += 1
        return tables

    def _parse_text_table_row(self, line: str) -> list[str] | None:
        stripped = line.strip()
        if not stripped or stripped.startswith("---") or stripped.startswith("==="):
            return None
        if "|" in stripped:
            cells = [c.strip() for c in stripped.split("|")]
            cells = [c for c in cells if c]
        elif re.match(r"^[A-Za-z0-9_\-\.]+\s{2,}[A-Za-z]", stripped):
            cells = re.split(r"\s{2,}", stripped)
            cells = [c.strip() for c in cells if c.strip()]
        else:
            return None
        if len(cells) < _TABLE_CANDIDATE_MIN_COLS:
            return None
        return cells

    def _extract_tables_from_pdf(self, path: Path) -> list[list[list[str]]]:
        """Extract tables from PDF using pdfplumber (optional).
        When Docling is available, tables are already embedded as Markdown in the text."""
        tables: list[list[list[str]]] = []
        try:
            import pdfplumber  # type: ignore

            with pdfplumber.open(str(path)) as pdf:
                for page in pdf.pages:
                    page_tables = page.extract_tables()
                    for table in page_tables:
                        cleaned = self._clean_pdf_table(table)
                        if len(cleaned) >= _TABLE_CANDIDATE_MIN_ROWS and len(cleaned[0]) >= _TABLE_CANDIDATE_MIN_COLS:
                            tables.append(cleaned)
        except ImportError:
            pass
        except Exception:
            pass
        return tables

    def _clean_pdf_table(self, table: list[list[str | None]]) -> list[list[str]]:
        cleaned: list[list[str]] = []
        for row in table:
            cells = [str(c).strip() if c else "" for c in row]
            if any(cells):
                cleaned.append(cells)
        return cleaned

    def _structure_register_table(self, table: list[list[str]]) -> list[dict[str, str]]:
        if not table:
            return []
        header_row = [c.lower().strip() for c in table[0]]
        header_set = set(header_row)
        if not header_set & _REGISTER_HEADERS:
            return []

        addr_idx = self._find_header_index(header_row, {"address", "offset", "addr"})
        name_idx = self._find_header_index(header_row, {"register", "name", "register name"})
        desc_idx = self._find_header_index(header_row, {"description", "function", "desc"})
        bit_idx = self._find_header_index(header_row, {"bit", "bits", "field"})
        reset_idx = self._find_header_index(header_row, {"reset", "reset value", "default"})
        access_idx = self._find_header_index(header_row, {"access", "rw", "type"})

        records: list[dict[str, str]] = []
        for row in table[1:]:
            if len(row) < 2:
                continue
            record: dict[str, str] = {}
            if addr_idx is not None and addr_idx < len(row):
                record["address_offset"] = row[addr_idx].strip()
            if name_idx is not None and name_idx < len(row):
                record["register_name"] = row[name_idx].strip()
            if desc_idx is not None and desc_idx < len(row):
                record["description"] = row[desc_idx].strip()
            if bit_idx is not None and bit_idx < len(row):
                record["bits"] = row[bit_idx].strip()
            if reset_idx is not None and reset_idx < len(row):
                record["reset_value"] = row[reset_idx].strip()
            if access_idx is not None and access_idx < len(row):
                record["access"] = row[access_idx].strip()
            if not record:
                continue
            records.append(record)
        return records

    def _structure_pin_table(self, table: list[list[str]]) -> list[dict[str, str]]:
        if not table:
            return []
        header_row = [c.lower().strip() for c in table[0]]
        header_set = set(header_row)
        if not header_set & _PIN_HEADERS:
            return []

        pin_idx = self._find_header_index(header_row, {"pin", "pin#", "pin number", "position", "pins"})
        signal_idx = self._find_header_index(header_row, {"signal", "function", "default", "name"})
        af_idx = self._find_header_index(header_row, {"alternate", "af", "alternate function"})

        records: list[dict[str, str]] = []
        for row in table[1:]:
            if len(row) < 2:
                continue
            record: dict[str, str] = {}
            if pin_idx is not None and pin_idx < len(row):
                record["pin"] = row[pin_idx].strip()
            if signal_idx is not None and signal_idx < len(row):
                record["signal"] = row[signal_idx].strip()
            if af_idx is not None and af_idx < len(row):
                record["alternate_function"] = row[af_idx].strip()
            if not record:
                continue
            records.append(record)
        return records

    def _find_header_index(self, header_row: list[str], candidates: set[str]) -> int | None:
        for i, h in enumerate(header_row):
            for c in candidates:
                if c in h:
                    return i
        return None

    def _render_register_table_text(self, records: list[dict[str, str]]) -> str:
        if not records:
            return ""
        header_keys = ["register_name", "address_offset", "description", "bits", "reset_value", "access"]
        header_labels = ["Register", "Address", "Description", "Bits", "Reset", "Access"]
        visible: list[tuple[str, ...]] = []
        keys_present: list[int] = []
        for i, key in enumerate(header_keys):
            if any(key in r for r in records):
                keys_present.append(i)
        label_cols = [header_labels[i] for i in keys_present]
        visible.append(tuple(label_cols))
        for rec in records:
            row = tuple(rec.get(header_keys[i], "") for i in keys_present)  # type: ignore[arg-type]
            visible.append(row)
        max_widths = [max(len(str(row[c])) for row in visible) for c in range(len(visible[0]))]
        lines: list[str] = []
        lines.append(" | ".join(str(visible[0][c]).ljust(max_widths[c]) for c in range(len(visible[0]))))
        lines.append("-|-".join("-" * max_widths[c] for c in range(len(visible[0]))))
        for row in visible[1:]:
            lines.append(" | ".join(str(row[c]).ljust(max_widths[c]) for c in range(len(row))))
        return "\n".join(lines)

    def _render_pin_table_text(self, records: list[dict[str, str]]) -> str:
        if not records:
            return ""
        header_keys = ["pin", "signal", "alternate_function"]
        header_labels = ["Pin", "Signal", "Alternate Function"]
        visible: list[tuple[str, ...]] = []
        keys_present: list[int] = []
        for i, key in enumerate(header_keys):
            if any(key in r for r in records):
                keys_present.append(i)
        label_cols = [header_labels[i] for i in keys_present]
        visible.append(tuple(label_cols))
        for rec in records:
            row = tuple(rec.get(header_keys[i], "") for i in keys_present)  # type: ignore[arg-type]
            visible.append(row)
        max_widths = [max(len(str(row[c])) for row in visible) for c in range(len(visible[0]))]
        lines: list[str] = []
        lines.append(" | ".join(str(visible[0][c]).ljust(max_widths[c]) for c in range(len(visible[0]))))
        lines.append("-|-".join("-" * max_widths[c] for c in range(len(visible[0]))))
        for row in visible[1:]:
            lines.append(" | ".join(str(row[c]).ljust(max_widths[c]) for c in range(len(row))))
        return "\n".join(lines)

    def _normalize_text(self, text: str) -> str:
        collapsed = text.replace("\r\n", "\n").replace("\r", "\n")
        collapsed = re.sub(r"[ \t]+", " ", collapsed)
        return collapsed.strip()

    def _build_document_id(self, path: Path) -> str:
        digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]
        return f"{path.stem.lower()}-{digest}"

    def _chunk_text(
        self,
        document_id: str,
        source_path: str,
        title: str,
        text: str,
        chunk_size: int,
        overlap: int,
        tables: list[list[list[str]]] | None = None,
    ) -> list[KnowledgeChunk]:
        paragraphs = self._split_into_paragraphs(text)
        if not paragraphs:
            return []

        table_blocks: list[list[str]] = []
        if tables:
            for table in tables:
                reg_records = self._structure_register_table(table)
                if reg_records:
                    table_text = self._render_register_table_text(reg_records)
                    table_blocks.append(table_text.split())
                    continue
                pin_records = self._structure_pin_table(table)
                if pin_records:
                    table_text = self._render_pin_table_text(pin_records)
                    table_blocks.append(table_text.split())
                    continue
                text_row = " ".join(" ".join(cell for cell in row) for row in table)
                table_blocks.append(text_row.split())

        all_blocks: list[list[str]] = []
        for para in paragraphs:
            all_blocks.append(para)
        for block in table_blocks:
            all_blocks.append(block)

        merged = self._merge_paragraphs_by_words(all_blocks, chunk_size)
        if not merged:
            return []

        chunks: list[KnowledgeChunk] = []
        index = 0
        for i, group in enumerate(merged):
            content = " ".join(group).strip()
            if not content:
                continue
            if i > 0 and overlap > 0:
                prev_words = merged[i - 1]
                overlap_words = prev_words[-overlap:] if len(prev_words) >= overlap else prev_words[:]
                content = " ".join(overlap_words) + " " + content if overlap_words else content
            chunks.append(
                KnowledgeChunk(
                    doc_id=document_id,
                    chunk_id=f"{document_id}-chunk-{index:04d}",
                    source_path=source_path,
                    title=title,
                    content=content.strip(),
                    keywords=self._extract_keywords(content),
                )
            )
            index += 1
        return chunks

    def _split_into_paragraphs(self, text: str) -> list[list[str]]:
        raw_paragraphs = re.split(r"\n\s*\n", text)
        result: list[list[str]] = []
        for para in raw_paragraphs:
            stripped = para.strip()
            if not stripped:
                continue
            words = stripped.split()
            if len(words) < 2:
                continue
            result.append(words)
        return result

    def _merge_paragraphs_by_words(self, paragraphs: list[list[str]], chunk_size: int) -> list[list[str]]:
        merged: list[list[str]] = []
        current: list[str] = []
        for para in paragraphs:
            if not current:
                current = list(para)
            elif len(current) + len(para) <= chunk_size:
                current.extend(para)
            else:
                merged.append(current)
                current = list(para)
        if current:
            merged.append(current)
        return merged

    def _extract_keywords(self, text: str, limit: int = 20) -> list[str]:
        candidates = re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}", text)
        hex_candidates = re.findall(r"0[xX][0-9a-fA-F]+", text)
        candidates.extend(hex_candidates)

        stop_words = {
            "the", "and", "for", "with", "that", "this", "from", "are", "was",
            "have", "has", "into", "when", "then", "than", "use", "using",
            "mode", "data", "read", "write", "page", "bit", "bits",
        }

        seen: list[str] = []
        for token in candidates:
            lowered = token.lower()
            if lowered in stop_words or lowered in seen:
                continue
            seen.append(lowered)
            if len(seen) >= limit:
                break
        return seen

    def _summarize_text(self, text: str, max_sentences: int = 3) -> str:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        selected = [segment.strip() for segment in sentences if segment.strip()][:max_sentences]
        return " ".join(selected)[:600]

