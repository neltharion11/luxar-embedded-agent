from __future__ import annotations

import unittest
from pathlib import Path


class UiShellTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = Path("ui/public/index.html").read_text(encoding="utf-8")

    def test_chat_status_i18n_keys_exist(self) -> None:
        for key in [
            "chat.thinking",
            "chat.streaming",
            "chat.stalled",
            "chat.recovering",
            "chat.lastResponseAgo",
            "chat.noEventSince",
        ]:
            self.assertIn(key, self.html)

    def test_chat_status_controller_uses_stall_threshold(self) -> None:
        self.assertIn("let CHAT_STALL_THRESHOLD_MS = 45000;", self.html)
        self.assertIn("function createChatStatusController(el)", self.html)
        self.assertIn("statusController.setState('tool_running'", self.html)

    def test_stream_parser_handles_warning_and_tool_running_events(self) -> None:
        self.assertIn("currentEvent === 'tool_running'", self.html)
        self.assertIn("currentEvent === 'warning'", self.html)


if __name__ == "__main__":
    unittest.main()
