from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luxar.core.memory_store import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def test_reasoning_content_round_trips_through_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "memory.db"
            store = MemoryStore(db_path)
            try:
                store.ensure_session("project:test", source="web", project="test")
                store.append_messages_batch(
                    "project:test",
                    [
                        {"role": "user", "content": "你好"},
                        {"role": "assistant", "content": "你好，我在。", "reasoning_content": "thinking-state"},
                    ],
                )
                messages = store.get_messages("project:test")
            finally:
                store.close()

        self.assertEqual(2, len(messages))
        self.assertEqual("assistant", messages[-1]["role"])
        self.assertEqual("thinking-state", messages[-1]["reasoning_content"])


if __name__ == "__main__":
    unittest.main()
