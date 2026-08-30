from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from luxar.database.persistence import TransientPersistence
from luxar.domain.devices import SerialPortInfo
from luxar.serial_terminal import SerialTerminalError
from luxar.web import create_app


class FakeSerialTerminalService:
    def __init__(self) -> None:
        self.open_calls: list[dict[str, object]] = []
        self.write_calls: list[dict[str, object]] = []
        self.closed: list[str] = []
        self.close_all_calls = 0

    def open(self, **settings: object) -> dict[str, object]:
        self.open_calls.append(settings)
        return {
            "session_id": "serial-session-1",
            "connected": True,
            **settings,
            "rx_bytes": 0,
            "tx_bytes": 0,
            "last_sequence": 0,
            "reader_error": None,
            "events": [],
        }

    def snapshot(self, session_id: str, *, after_sequence: int) -> dict[str, object]:
        if session_id != "serial-session-1":
            raise SerialTerminalError("not_found", "串口会话不存在或已结束")
        return {
            "session_id": session_id,
            "connected": True,
            "port": "COM4",
            "baud_rate": 115_200,
            "data_bits": 8,
            "parity": "none",
            "stop_bits": 1.0,
            "rx_bytes": 5,
            "tx_bytes": 0,
            "last_sequence": 2,
            "reader_error": None,
            "events": [
                {
                    "sequence": 2,
                    "timestamp": "2026-08-28T00:00:00+00:00",
                    "direction": "rx",
                    "text": "ready",
                    "hex": "72 65 61 64 79",
                    "size": 5,
                }
            ] if after_sequence < 2 else [],
        }

    def write(self, session_id: str, **payload: object) -> dict[str, object]:
        self.write_calls.append({"session_id": session_id, **payload})
        return {"bytes_written": 2, "tx_bytes": 2, "event": {"sequence": 3}}

    def close(self, session_id: str) -> dict[str, object]:
        self.closed.append(session_id)
        return {"status": "closed", "session_id": session_id, "port": "COM4"}

    def close_all(self) -> None:
        self.close_all_calls += 1


def _app(tmp_path: Path, service: FakeSerialTerminalService):
    return create_app(
        projects_roots=[tmp_path],
        persistence=TransientPersistence(),
        port_discoverer=lambda: [SerialPortInfo(name="COM4", description="USB UART")],
        serial_terminal_service=service,  # type: ignore[arg-type]
    )


def test_serial_terminal_session_api_reuses_discovered_port_allowlist(tmp_path: Path) -> None:
    service = FakeSerialTerminalService()
    with TestClient(_app(tmp_path, service)) as client:
        rejected = client.post("/api/serial/sessions", json={"port": "COM5"})
        opened = client.post(
            "/api/serial/sessions",
            json={
                "port": "COM4",
                "baud_rate": 115200,
                "data_bits": 8,
                "parity": "none",
                "stop_bits": 1.0,
            },
        )
        polled = client.get(
            "/api/serial/sessions/serial-session-1?after_sequence=1"
        )

    assert rejected.status_code == 422
    assert service.open_calls == [{
        "port": "COM4",
        "baud_rate": 115200,
        "data_bits": 8,
        "parity": "none",
        "stop_bits": 1.0,
    }]
    assert opened.status_code == 201
    assert opened.json()["session_id"] == "serial-session-1"
    assert polled.status_code == 200
    assert polled.json()["events"][0]["text"] == "ready"
    assert service.close_all_calls == 1


def test_serial_terminal_write_and_disconnect_contract(tmp_path: Path) -> None:
    service = FakeSerialTerminalService()
    client = TestClient(_app(tmp_path, service))

    written = client.post(
        "/api/serial/sessions/serial-session-1/write",
        json={"mode": "hex", "payload": "AA 55", "line_ending": "none"},
    )
    closed = client.delete("/api/serial/sessions/serial-session-1")
    missing = client.get("/api/serial/sessions/missing")

    assert written.status_code == 200
    assert service.write_calls == [{
        "session_id": "serial-session-1",
        "mode": "hex",
        "payload": "AA 55",
        "line_ending": "none",
    }]
    assert closed.status_code == 200
    assert service.closed == ["serial-session-1"]
    assert missing.status_code == 404
