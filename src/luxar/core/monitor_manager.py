from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Optional

_logger = logging.getLogger("luxar.monitor")


class MonitorState:
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


class MonitorManager:
    """Server-level singleton that manages background serial port monitoring.

    Features:
    - Background thread continuously reads from serial port
    - Line buffer (deque) with configurable max size
    - pause/resume: flash tool pauses before flashing, resumes after
    - Callback for SSE streaming
    """

    _instance: Optional[MonitorManager] = None

    def __init__(self):
        self._port: str = ""
        self._baudrate: int = 115200
        self._state: str = MonitorState.STOPPED
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._lock = threading.Lock()
        self._buffer: deque[str] = deque(maxlen=1000)
        self._serial = None
        self._on_line_callback = None

    @classmethod
    def instance(cls) -> MonitorManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def state(self) -> str:
        return self._state

    @property
    def port(self) -> str:
        return self._port

    @property
    def baudrate(self) -> int:
        return self._baudrate

    @property
    def buffer(self) -> list[str]:
        with self._lock:
            return list(self._buffer)

    def set_on_line(self, callback):
        """Set callback for each line read: callback(line: str)"""
        self._on_line_callback = callback

    def start(self, port: str, baudrate: int = 115200) -> bool:
        """Start background serial monitoring. Returns True on success."""
        with self._lock:
            if self._state == MonitorState.RUNNING:
                _logger.warning("Monitor already running on %s", self._port)
                return True

            try:
                import serial
            except ImportError:
                _logger.error("pyserial not installed")
                return False

            self._port = port
            self._baudrate = baudrate
            self._stop_event.clear()
            self._pause_event.set()  # start unpaused

            try:
                self._serial = serial.Serial(
                    port=port,
                    baudrate=baudrate,
                    timeout=0.5,
                )
            except Exception as exc:
                _logger.error("Failed to open serial port %s: %s", port, exc)
                self._state = MonitorState.STOPPED
                return False

            self._state = MonitorState.RUNNING
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
            _logger.info("Monitor started on %s @ %d baud", port, baudrate)
            return True

    def stop(self) -> bool:
        """Stop background serial monitoring. Returns True if was running."""
        with self._lock:
            if self._state == MonitorState.STOPPED:
                return False
            was_running = self._state in (MonitorState.RUNNING, MonitorState.PAUSED)
            self._state = MonitorState.STOPPED
            self._stop_event.set()
            self._pause_event.set()  # unblock reader

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

        with self._lock:
            if self._serial and getattr(self._serial, "is_open", False):
                try:
                    self._serial.close()
                except Exception:
                    pass
            self._serial = None

        _logger.info("Monitor stopped on %s", self._port)
        return was_running

    def pause(self) -> bool:
        """Pause monitoring (release serial port for flash). Returns True if was running."""
        with self._lock:
            if self._state != MonitorState.RUNNING:
                return False
            self._state = MonitorState.PAUSED
            self._pause_event.clear()

        # Wait for reader to pause
        time.sleep(0.3)

        with self._lock:
            if self._serial and getattr(self._serial, "is_open", False):
                try:
                    self._serial.close()
                except Exception:
                    pass
            self._serial = None

        _logger.info("Monitor paused on %s", self._port)
        return True

    def resume(self) -> bool:
        """Resume monitoring after pause. Returns True if was paused."""
        with self._lock:
            if self._state != MonitorState.PAUSED:
                return False

            try:
                import serial
                self._serial = serial.Serial(
                    port=self._port,
                    baudrate=self._baudrate,
                    timeout=0.5,
                )
            except Exception as exc:
                _logger.error("Failed to reopen serial port %s: %s", self._port, exc)
                self._state = MonitorState.STOPPED
                return False

            self._state = MonitorState.RUNNING
            self._pause_event.set()

        _logger.info("Monitor resumed on %s", self._port)
        return True

    def _read_loop(self):
        """Background thread: read serial lines, push to buffer + callback."""
        import serial as _serial_mod
        while not self._stop_event.is_set():
            self._pause_event.wait()  # block if paused
            if self._stop_event.is_set():
                break

            ser = self._serial
            if ser is None or not getattr(ser, "is_open", False):
                time.sleep(0.1)
                continue

            try:
                if ser.in_waiting > 0:
                    raw = ser.readline()
                    if raw:
                        try:
                            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                        except Exception:
                            line = raw.decode(errors="replace").rstrip("\r\n")
                        with self._lock:
                            self._buffer.append(line)
                        if self._on_line_callback:
                            try:
                                self._on_line_callback(line)
                            except Exception:
                                pass
                else:
                    time.sleep(0.05)
            except (_serial_mod.SerialException, OSError) as exc:
                _logger.debug("Serial read error (port may be disconnected): %s", exc)
                time.sleep(0.5)
            except Exception as exc:
                _logger.warning("Unexpected serial read error: %s", exc)
                time.sleep(0.5)

    def read_buffer(self, max_lines: int = 50) -> list[str]:
        """Read and drain the buffer, returning recent lines."""
        with self._lock:
            lines = list(self._buffer)
            self._buffer.clear()
            return lines[-max_lines:] if len(lines) > max_lines else lines

    def status_dict(self) -> dict:
        """Return status dict for API responses."""
        return {
            "state": self._state,
            "port": self._port,
            "baudrate": self._baudrate,
            "buffer_size": len(self._buffer) if self._buffer else 0,
        }
