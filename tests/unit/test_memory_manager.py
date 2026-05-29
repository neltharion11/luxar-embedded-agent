from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luxar.memory.memory_manager import MemoryManager


class MemoryManagerTests(unittest.TestCase):
    def test_write_allows_stable_fact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MemoryManager(Path(tmpdir))
            result = manager.write("Board convention: I2C1 is reserved for sensors.", target="memory")
            payload = manager.read("memory")

        self.assertTrue(result["success"])
        self.assertIn("I2C1 is reserved for sensors", payload["content"])

    def test_write_blocks_task_progress_for_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MemoryManager(Path(tmpdir))
            result = manager.write("Current progress: build passed, next step is flash.", target="memory")
            payload = manager.read("memory")

        self.assertFalse(result["success"])
        self.assertTrue(result["blocked"])
        self.assertIn("transient task progress", result["error"])
        self.assertEqual("", payload["content"])

    def test_write_blocks_task_progress_for_user_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = MemoryManager(Path(tmpdir))
            result = manager.write("Status update: completed bring-up, remaining task is monitor.", target="user")
            payload = manager.read("user")

        self.assertFalse(result["success"])
        self.assertTrue(result["blocked"])
        self.assertEqual("", payload["content"])


if __name__ == "__main__":
    unittest.main()
