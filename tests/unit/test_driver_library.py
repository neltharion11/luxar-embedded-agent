from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luxar.core.driver_library import DriverLibrary
from luxar.models.schemas import DriverMetadata


class DriverLibraryTests(unittest.TestCase):
    def test_store_and_search_driver(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            library = DriverLibrary(Path(tmpdir) / "driver_library")
            source_path = Path(tmpdir) / "bmi270.c"
            header_path = Path(tmpdir) / "bmi270.h"
            source_path.write_text("int bmi270_init(void) { return 0; }\n", encoding="utf-8")
            header_path.write_text("int bmi270_init(void);\n", encoding="utf-8")

            stored = library.store_driver(
                DriverMetadata(
                    name="bmi270",
                    protocol="SPI",
                    chip="BMI270",
                    vendor="bosch",
                    device="bmi270",
                    path=str(source_path),
                    header_path=str(header_path),
                    source_path=str(source_path),
                    review_passed=True,
                    source_doc="BMI270 datasheet summary",
                    review_issue_count=0,
                )
            )

            self.assertTrue(Path(library.database_path).exists())
            results = library.search_drivers(keyword="bosch")
            self.assertEqual(1, len(results))
            self.assertEqual(stored.path, results[0].path)
            self.assertTrue(results[0].review_passed)

    def test_store_driver_upserts_by_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            library = DriverLibrary(Path(tmpdir) / "driver_library")
            source_path = Path(tmpdir) / "sensor.c"
            source_path.write_text("int sensor_init(void) { return 0; }\n", encoding="utf-8")

            library.store_driver(
                DriverMetadata(
                    name="sensor",
                    protocol="I2C",
                    chip="SENSOR",
                    vendor="vendor_a",
                    device="sensor",
                    path=str(source_path),
                    source_path=str(source_path),
                    review_passed=False,
                    review_issue_count=2,
                )
            )
            library.store_driver(
                DriverMetadata(
                    name="sensor",
                    protocol="I2C",
                    chip="SENSOR",
                    vendor="vendor_b",
                    device="sensor",
                    path=str(source_path),
                    source_path=str(source_path),
                    review_passed=True,
                    review_issue_count=0,
                )
            )

            results = library.search_drivers(keyword="vendor_b")
            self.assertEqual(1, len(results))
            self.assertEqual("vendor_b", results[0].vendor)
            self.assertTrue(results[0].review_passed)

    def test_search_drivers_returns_scored_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            library = DriverLibrary(Path(tmpdir) / "driver_library")
            for i, (name, kw, rev) in enumerate([
                ("exact_match", "bmi270", True),
                ("partial_chip", "partial_bmi", False),
                ("no_match", "other", False),
            ]):
                sp = Path(tmpdir) / f"{name}.c"
                hp = Path(tmpdir) / f"{name}.h"
                sp.write_text("int x(void){return 0;}\n", encoding="utf-8")
                hp.write_text("int x(void);\n", encoding="utf-8")
                library.store_driver(DriverMetadata(
                    name=name, protocol="SPI", chip=kw,
                    vendor="v", device=name,
                    path=str(sp), header_path=str(hp),
                    source_path=str(sp),
                    review_passed=rev, reuse_count=i,
                ))

            results = library.search_drivers(keyword="bmi270", limit=5)
            self.assertGreater(len(results), 0)
            self.assertEqual(results[0].name, "exact_match")

    def test_record_reuse_updates_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            library = DriverLibrary(Path(tmpdir) / "driver_library")
            sp = Path(tmpdir) / "reuse_me.c"
            hp = Path(tmpdir) / "reuse_me.h"
            sp.write_text("int x(void){return 0;}\n", encoding="utf-8")
            hp.write_text("int x(void);\n", encoding="utf-8")
            library.store_driver(DriverMetadata(
                name="reuse_me", protocol="SPI", chip="CHIP",
                vendor="v", device="reuse_me",
                path=str(sp), header_path=str(hp),
                source_path=str(sp), review_passed=True,
            ))

            library.record_reuse(str(sp))
            results = library.search_drivers(keyword="reuse_me")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].reuse_count, 1)

    def test_new_drivers_have_default_reuse_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            library = DriverLibrary(Path(tmpdir) / "driver_library")
            sp = Path(tmpdir) / "new.c"
            hp = Path(tmpdir) / "new.h"
            sp.write_text("int x(void){return 0;}\n", encoding="utf-8")
            hp.write_text("int x(void);\n", encoding="utf-8")
            stored = library.store_driver(DriverMetadata(
                name="new", protocol="SPI", chip="N",
                vendor="v", device="new",
                path=str(sp), header_path=str(hp),
                source_path=str(sp), review_passed=True,
            ))
            self.assertEqual(stored.reuse_count, 0)
            self.assertEqual(stored.kb_score, 0.0)

    def test_driver_metadata_serializes_new_fields(self) -> None:
        from datetime import datetime
        now = datetime.now()
        m = DriverMetadata(
            name="t", protocol="SPI", chip="C", path="/p",
            reuse_count=3, kb_score=0.75, last_reused_at=now,
        )
        d = m.model_dump(mode="json")
        self.assertEqual(d["reuse_count"], 3)
        self.assertEqual(d["kb_score"], 0.75)
        self.assertIsNotNone(d["last_reused_at"])


if __name__ == "__main__":
    unittest.main()

