"""Bounded interactive serial sessions for the local Web UI.

Port discovery and allow-list validation stay in ``luxar.web`` so this module
never decides which host device may be opened.  It only owns an already
validated port for a short-lived, process-local terminal session.
"""

from __future__ import annotations

import re
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Protocol

import serial


class SerialConnection(Protocol):
    @property
    def in_waiting(self) -> int: ...

    @property
    def is_open(self) -> bool: ...

    def read(self, size: int = 1) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def close(self) -> None: ...


SerialFactory = Callable[..., SerialConnection]


class SerialTerminalError(RuntimeError):
    """A safe, user-displayable serial terminal failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class _Session:
    session_id: str
    port: str
    baud_rate: int
    data_bits: int
    parity: str
    stop_bits: float
    connection: SerialConnection
    events: deque[dict[str, object]]
    stop: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)
    sequence: int = 0
    rx_bytes: int = 0
    tx_bytes: int = 0
    reader_error: str | None = None
    reader: threading.Thread | None = None


class SerialTerminalService:
    """Own interactive pyserial handles without exposing them to HTTP code."""

    _PARITY = {
        "none": serial.PARITY_NONE,
        "even": serial.PARITY_EVEN,
        "odd": serial.PARITY_ODD,
        "mark": serial.PARITY_MARK,
        "space": serial.PARITY_SPACE,
    }
    _DATA_BITS = {
        5: serial.FIVEBITS,
        6: serial.SIXBITS,
        7: serial.SEVENBITS,
        8: serial.EIGHTBITS,
    }
    _STOP_BITS = {
        1.0: serial.STOPBITS_ONE,
        1.5: serial.STOPBITS_ONE_POINT_FIVE,
        2.0: serial.STOPBITS_TWO,
    }

    def __init__(
        self,
        *,
        serial_factory: SerialFactory = serial.Serial,
        max_sessions: int = 8,
        max_events_per_session: int = 1_000,
        max_read_bytes: int = 4_096,
    ) -> None:
        if max_sessions <= 0 or max_events_per_session <= 0 or max_read_bytes <= 0:
            raise ValueError("serial terminal limits must be positive")
        self._serial_factory = serial_factory
        self._max_sessions = max_sessions
        self._max_events_per_session = max_events_per_session
        self._max_read_bytes = max_read_bytes
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.RLock()

    def open(
        self,
        *,
        port: str,
        baud_rate: int,
        data_bits: int,
        parity: str,
        stop_bits: float,
    ) -> dict[str, object]:
        with self._lock:
            if len(self._sessions) >= self._max_sessions:
                raise SerialTerminalError("limit", "串口会话数量已达上限")
            if any(item.port == port for item in self._sessions.values()):
                raise SerialTerminalError("busy", "该串口已被 Web 串口工具占用")

            try:
                connection = self._serial_factory(
                    port=port,
                    baudrate=baud_rate,
                    bytesize=self._DATA_BITS[data_bits],
                    parity=self._PARITY[parity],
                    stopbits=self._STOP_BITS[stop_bits],
                    timeout=0.1,
                    write_timeout=1.0,
                )
            except (OSError, ValueError, serial.SerialException) as error:
                raise SerialTerminalError("open_failed", "串口打开失败，请检查设备是否被占用") from error

            session = _Session(
                session_id=uuid.uuid4().hex,
                port=port,
                baud_rate=baud_rate,
                data_bits=data_bits,
                parity=parity,
                stop_bits=stop_bits,
                connection=connection,
                events=deque(maxlen=self._max_events_per_session),
            )
            self._sessions[session.session_id] = session
            reader = threading.Thread(
                target=self._read_loop,
                args=(session,),
                name=f"luxar-serial-{session.session_id[:8]}",
                daemon=True,
            )
            session.reader = reader
            reader.start()
            return self.snapshot(session.session_id, after_sequence=0)

    def snapshot(
        self,
        session_id: str,
        *,
        after_sequence: int,
    ) -> dict[str, object]:
        session = self._get(session_id)
        with session.lock:
            return {
                "session_id": session.session_id,
                "connected": bool(session.connection.is_open and not session.stop.is_set()),
                "port": session.port,
                "baud_rate": session.baud_rate,
                "data_bits": session.data_bits,
                "parity": session.parity,
                "stop_bits": session.stop_bits,
                "rx_bytes": session.rx_bytes,
                "tx_bytes": session.tx_bytes,
                "last_sequence": session.sequence,
                "reader_error": session.reader_error,
                "events": [
                    dict(event)
                    for event in session.events
                    if int(event["sequence"]) > after_sequence
                ],
            }

    def write(
        self,
        session_id: str,
        *,
        mode: str,
        payload: str,
        line_ending: str,
    ) -> dict[str, object]:
        session = self._get(session_id)
        data = self._encode_payload(mode, payload, line_ending)
        try:
            with session.lock:
                if session.stop.is_set() or not session.connection.is_open:
                    raise SerialTerminalError("closed", "串口会话已断开")
                written = session.connection.write(data)
                if written != len(data):
                    raise SerialTerminalError("write_failed", "串口数据未完整发送")
                session.tx_bytes += written
                event = self._append_event(session, "tx", data)
        except SerialTerminalError:
            raise
        except (OSError, serial.SerialException) as error:
            raise SerialTerminalError("write_failed", "串口发送失败") from error
        return {
            "bytes_written": written,
            "event": event,
            "tx_bytes": session.tx_bytes,
        }

    def close(self, session_id: str) -> dict[str, object]:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            raise SerialTerminalError("not_found", "串口会话不存在或已结束")
        self._close_session(session)
        return {"status": "closed", "session_id": session_id, "port": session.port}

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            self._close_session(session)

    def _get(self, session_id: str) -> _Session:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise SerialTerminalError("not_found", "串口会话不存在或已结束")
        return session

    def _read_loop(self, session: _Session) -> None:
        try:
            while not session.stop.is_set():
                waiting = max(1, min(int(session.connection.in_waiting or 0), self._max_read_bytes))
                data = session.connection.read(waiting)
                if not data:
                    continue
                with session.lock:
                    session.rx_bytes += len(data)
                    self._append_event(session, "rx", data)
        except (OSError, serial.SerialException) as error:
            with session.lock:
                if not session.stop.is_set():
                    session.reader_error = "串口读取中断"
                    self._append_event(session, "error", str(error).encode("utf-8", errors="replace"))
                    session.stop.set()

    @staticmethod
    def _append_event(
        session: _Session,
        direction: str,
        data: bytes,
    ) -> dict[str, object]:
        session.sequence += 1
        event: dict[str, object] = {
            "sequence": session.sequence,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "direction": direction,
            "text": data.decode("utf-8", errors="replace"),
            "hex": data.hex(" ").upper(),
            "size": len(data),
        }
        session.events.append(event)
        return dict(event)

    @staticmethod
    def _encode_payload(mode: str, payload: str, line_ending: str) -> bytes:
        if mode == "hex":
            compact = re.sub(r"\s+", "", payload)
            if not compact or len(compact) % 2 or not re.fullmatch(r"[0-9A-Fa-f]+", compact):
                raise SerialTerminalError("invalid_payload", "HEX 数据必须由完整的两位十六进制字节组成")
            return bytes.fromhex(compact)

        suffix = {"none": "", "lf": "\n", "crlf": "\r\n"}[line_ending]
        data = (payload + suffix).encode("utf-8")
        if not data:
            raise SerialTerminalError("invalid_payload", "发送内容不能为空")
        return data

    @staticmethod
    def _close_session(session: _Session) -> None:
        session.stop.set()
        try:
            session.connection.close()
        except (OSError, serial.SerialException):
            pass
        if session.reader is not None and session.reader is not threading.current_thread():
            session.reader.join(timeout=1.0)


__all__ = ["SerialTerminalError", "SerialTerminalService"]
