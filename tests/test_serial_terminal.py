from __future__ import annotations

import threading
import time
from collections import deque

import pytest

from luxar.serial_terminal import SerialTerminalError, SerialTerminalService


class FakeSerialConnection:
    def __init__(self, initial: bytes = b"") -> None:
        self._reads: deque[bytes] = deque([initial] if initial else [])
        self._lock = threading.Lock()
        self.writes: list[bytes] = []
        self.is_open = True

    @property
    def in_waiting(self) -> int:
        with self._lock:
            return len(self._reads[0]) if self._reads else 0

    def read(self, size: int = 1) -> bytes:
        time.sleep(0.005)
        with self._lock:
            if not self._reads or not self.is_open:
                return b""
            data = self._reads.popleft()
            if len(data) > size:
                self._reads.appendleft(data[size:])
            return data[:size]

    def write(self, data: bytes) -> int:
        if not self.is_open:
            raise OSError("closed")
        self.writes.append(data)
        return len(data)

    def close(self) -> None:
        self.is_open = False


def _wait_for_rx(
    service: SerialTerminalService,
    session_id: str,
) -> dict[str, object]:
    for _ in range(100):
        snapshot = service.snapshot(session_id, after_sequence=0)
        if snapshot["rx_bytes"]:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("reader thread did not capture fake serial bytes")


def test_serial_terminal_reads_writes_and_closes() -> None:
    connection = FakeSerialConnection(b"ready\n")
    service = SerialTerminalService(serial_factory=lambda **_: connection)

    opened = service.open(
        port="COM4",
        baud_rate=115_200,
        data_bits=8,
        parity="none",
        stop_bits=1.0,
    )
    session_id = str(opened["session_id"])
    snapshot = _wait_for_rx(service, session_id)

    assert snapshot["rx_bytes"] == 6
    assert snapshot["events"][0]["direction"] == "rx"  # type: ignore[index]
    assert snapshot["events"][0]["text"] == "ready\n"  # type: ignore[index]

    sent = service.write(
        session_id,
        mode="text",
        payload="status",
        line_ending="crlf",
    )
    assert sent["bytes_written"] == 8
    assert connection.writes == [b"status\r\n"]

    closed = service.close(session_id)
    assert closed["status"] == "closed"
    assert connection.is_open is False
    with pytest.raises(SerialTerminalError, match="不存在"):
        service.snapshot(session_id, after_sequence=0)


def test_serial_terminal_hex_payload_and_port_exclusivity() -> None:
    connections: list[FakeSerialConnection] = []

    def factory(**_: object) -> FakeSerialConnection:
        connection = FakeSerialConnection()
        connections.append(connection)
        return connection

    service = SerialTerminalService(serial_factory=factory)
    opened = service.open(
        port="COM4",
        baud_rate=9_600,
        data_bits=8,
        parity="none",
        stop_bits=1.0,
    )

    with pytest.raises(SerialTerminalError, match="占用"):
        service.open(
            port="COM4",
            baud_rate=9_600,
            data_bits=8,
            parity="none",
            stop_bits=1.0,
        )

    service.write(
        str(opened["session_id"]),
        mode="hex",
        payload="AA 55 0d",
        line_ending="none",
    )
    assert connections[0].writes == [b"\xaa\x55\x0d"]

    with pytest.raises(SerialTerminalError, match="十六进制"):
        service.write(
            str(opened["session_id"]),
            mode="hex",
            payload="AAG",
            line_ending="none",
        )

    service.close_all()
    assert connections[0].is_open is False
