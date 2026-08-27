"""Lifecycle for embedded SQLite application and LangGraph checkpoint data."""

from __future__ import annotations

import sqlite3
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from luxar.checkpoint_serde import create_checkpoint_serializer
from luxar.database.local_settings import LocalStorageSettings
from luxar.database.sqlite_persistence import SQLitePersistence


class LocalStorageRuntime:
    """Own the long-lived SQLite checkpoint connection used by LangGraph."""

    def __init__(self, settings: LocalStorageSettings) -> None:
        self.settings = settings
        self._persistence: SQLitePersistence | None = None
        self._checkpoint_connection: sqlite3.Connection | None = None
        self._checkpointer: BaseCheckpointSaver | None = None

    @property
    def persistence(self) -> SQLitePersistence:
        if self._persistence is None:
            raise RuntimeError("本地存储尚未启动")
        return self._persistence

    def open(self) -> None:
        if self._persistence is not None:
            return
        self.settings.root.mkdir(parents=True, exist_ok=True)
        self._persistence = SQLitePersistence(self.settings.application_path)

    def close(self) -> None:
        connection, self._checkpoint_connection = (
            self._checkpoint_connection,
            None,
        )
        self._checkpointer = None
        self._persistence = None
        if connection is not None:
            connection.close()

    def health(self) -> bool:
        return self._persistence is not None and self._persistence.health()

    def checkpointer(self) -> BaseCheckpointSaver:
        if self._checkpointer is not None:
            return self._checkpointer
        self.open()
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as error:
            raise RuntimeError(
                "未安装 langgraph-checkpoint-sqlite"
            ) from error

        connection = sqlite3.connect(
            self.settings.checkpoint_path,
            timeout=10.0,
            check_same_thread=False,
        )
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 10000")
        checkpointer: Any = SqliteSaver(
            connection,
            serde=create_checkpoint_serializer(),
        )
        checkpointer.setup()
        self._checkpoint_connection = connection
        self._checkpointer = checkpointer
        return checkpointer

    def __enter__(self) -> "LocalStorageRuntime":
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
