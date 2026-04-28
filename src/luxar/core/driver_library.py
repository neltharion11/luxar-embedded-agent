from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from luxar.models.schemas import DriverMetadata


class DriverLibrary:
    def __init__(self, library_root: str | Path):
        self.library_root = Path(library_root).resolve()
        self.library_root.mkdir(parents=True, exist_ok=True)
        self.database_path = self.library_root / "index.db"
        self._ensure_schema()

    def store_driver(self, metadata: DriverMetadata) -> DriverMetadata:
        payload = metadata.model_copy(
            update={
                "path": str(Path(metadata.path).resolve()),
                "header_path": str(Path(metadata.header_path).resolve()) if metadata.header_path else "",
                "source_path": str(Path(metadata.source_path).resolve()) if metadata.source_path else "",
                "stored_at": metadata.stored_at or datetime.now(),
            }
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                """
                INSERT INTO drivers (
                    name, protocol, chip, vendor, device, path,
                    header_path, source_path, review_passed, source_doc,
                    review_issue_count, reuse_count, kb_score, stored_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    name=excluded.name,
                    protocol=excluded.protocol,
                    chip=excluded.chip,
                    vendor=excluded.vendor,
                    device=excluded.device,
                    header_path=excluded.header_path,
                    source_path=excluded.source_path,
                    review_passed=excluded.review_passed,
                    source_doc=excluded.source_doc,
                    review_issue_count=excluded.review_issue_count,
                    reuse_count=excluded.reuse_count,
                    kb_score=excluded.kb_score,
                    stored_at=excluded.stored_at,
                    updated_at=excluded.updated_at
                """,
                (
                    payload.name,
                    payload.protocol,
                    payload.chip,
                    payload.vendor,
                    payload.device,
                    payload.path,
                    payload.header_path,
                    payload.source_path,
                    int(payload.review_passed),
                    payload.source_doc,
                    payload.review_issue_count,
                    payload.reuse_count,
                    payload.kb_score,
                    payload.stored_at.isoformat(),
                    datetime.now().isoformat(),
                ),
            )
            connection.commit()
        return payload

    def search_drivers(
        self,
        keyword: str = "",
        protocol: str = "",
        vendor: str = "",
        limit: int = 20,
    ) -> list[DriverMetadata]:
        query = """
            SELECT
                name, protocol, chip, vendor, device, path,
                header_path, source_path, review_passed, source_doc,
                review_issue_count, reuse_count, kb_score, stored_at
            FROM drivers
            WHERE 1 = 1
        """
        params: list[object] = []
        if protocol.strip():
            query += " AND lower(protocol) = ?"
            params.append(protocol.strip().lower())
        if vendor.strip():
            query += " AND lower(vendor) = ?"
            params.append(vendor.strip().lower())
        if keyword.strip():
            like = f"%{keyword.strip().lower()}%"
            query += """
                AND (
                    lower(name) LIKE ?
                    OR lower(chip) LIKE ?
                    OR lower(vendor) LIKE ?
                    OR lower(device) LIKE ?
                    OR lower(protocol) LIKE ?
                    OR lower(path) LIKE ?
                )
            """
            params.extend([like, like, like, like, like, like])

        with closing(sqlite3.connect(self.database_path)) as connection:
            rows = connection.execute(query, params).fetchall()

        scored: list[tuple[float, tuple[object, ...]]] = []
        kw_lower = keyword.strip().lower() if keyword.strip() else ""
        for row in rows:
            score = self._compute_relevance_score(row, kw_lower)
            scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [self._row_to_metadata(row) for _, row in scored[:limit]]

    def resolve_driver(self, query: str) -> DriverMetadata | None:
        normalized = query.strip().lower()
        if not normalized:
            return None
        candidates = self.search_drivers(keyword=query, limit=20)
        for item in candidates:
            if item.name.lower() == normalized or item.device.lower() == normalized or item.chip.lower() == normalized:
                return item
        return candidates[0] if candidates else None

    def record_reuse(self, driver_path: str) -> None:
        resolved = str(Path(driver_path).resolve())
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                """
                UPDATE drivers SET
                    reuse_count = reuse_count + 1,
                    last_reused_at = ?,
                    updated_at = ?
                WHERE path = ?
                """,
                (datetime.now().isoformat(), datetime.now().isoformat(), resolved),
            )
            connection.commit()

    def update_kb_score(self, driver_path: str, score: float) -> None:
        resolved = str(Path(driver_path).resolve())
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "UPDATE drivers SET kb_score = ?, updated_at = ? WHERE path = ?",
                (score, datetime.now().isoformat(), resolved),
            )
            connection.commit()

    def stats(self) -> dict[str, int]:
        with closing(sqlite3.connect(self.database_path)) as connection:
            total = connection.execute("SELECT COUNT(*) FROM drivers").fetchone()[0]
            passed = connection.execute(
                "SELECT COUNT(*) FROM drivers WHERE review_passed = 1"
            ).fetchone()[0]
        return {
            "total_drivers": int(total),
            "review_passed_drivers": int(passed),
        }

    def _compute_relevance_score(self, row: tuple[object, ...], keyword: str) -> float:
        name = str(row[0] or "").lower()
        chip = str(row[2] or "").lower()
        device = str(row[4] or "").lower()
        review_passed = bool(row[8])
        review_issues = int(row[10] or 0)
        reuse_count = int(row[11] or 0)
        kb_score = float(row[12] or 0.0)

        score = 0.0
        if not keyword:
            return 1.0
        if keyword == name:
            score += 1.0
        elif keyword in name:
            score += 0.6
        if keyword == device:
            score += 0.8
        elif keyword in device:
            score += 0.5
        if keyword == chip:
            score += 0.6
        elif keyword in chip:
            score += 0.4
        if review_passed:
            score += 0.3 + (1.0 / max(review_issues + 1, 1)) * 0.2
        if reuse_count > 0:
            score += min(reuse_count / 10.0, 0.3)
        if kb_score > 0:
            score += kb_score * 0.3
        return score

    def _ensure_schema(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS drivers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    chip TEXT NOT NULL DEFAULT '',
                    vendor TEXT NOT NULL DEFAULT '',
                    device TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL UNIQUE,
                    header_path TEXT NOT NULL DEFAULT '',
                    source_path TEXT NOT NULL DEFAULT '',
                    review_passed INTEGER NOT NULL DEFAULT 0,
                    source_doc TEXT NOT NULL DEFAULT '',
                    review_issue_count INTEGER NOT NULL DEFAULT 0,
                    reuse_count INTEGER NOT NULL DEFAULT 0,
                    kb_score REAL NOT NULL DEFAULT 0.0,
                    last_reused_at TEXT,
                    stored_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(drivers)").fetchall()
            }
            for col, col_type in [("reuse_count", "INTEGER NOT NULL DEFAULT 0"),
                                   ("kb_score", "REAL NOT NULL DEFAULT 0.0"),
                                   ("last_reused_at", "TEXT")]:
                if col not in columns:
                    connection.execute(f"ALTER TABLE drivers ADD COLUMN {col} {col_type}")
            connection.commit()

    def _row_to_metadata(self, row: tuple[object, ...]) -> DriverMetadata:
        stored_at = datetime.fromisoformat(str(row[13])) if len(row) > 13 and row[13] else datetime.now()
        reuse_count = int(row[11]) if len(row) > 11 and row[11] else 0
        kb_score = float(row[12]) if len(row) > 12 and row[12] else 0.0
        return DriverMetadata(
            name=str(row[0]),
            protocol=str(row[1]),
            chip=str(row[2]),
            vendor=str(row[3]),
            device=str(row[4]),
            path=str(row[5]),
            header_path=str(row[6]),
            source_path=str(row[7]),
            review_passed=bool(row[8]),
            source_doc=str(row[9]),
            review_issue_count=int(row[10]),
            reuse_count=reuse_count,
            kb_score=kb_score,
            stored_at=stored_at,
        )

