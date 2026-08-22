"""Connection-pool lifecycle, migrations, health checks, and checkpointer."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from luxar.database.migration_runner import MigrationRunner
from luxar.database.settings import DatabaseSettings


class DatabaseUnavailable(RuntimeError):
    """Database support is unconfigured, unavailable, or missing dependencies."""


class DatabaseRuntime:
    def __init__(self, settings: DatabaseSettings) -> None:
        self.settings = settings
        self._pool: Any | None = None
        self._checkpointer: BaseCheckpointSaver | None = None
        self._checkpoint_pool: Any | None = None

    @property
    def pool(self) -> Any:
        if self._pool is None:
            raise DatabaseUnavailable("数据库连接池尚未启动")
        return self._pool

    def open(self) -> None:
        if self._pool is not None:
            return
        if not self.settings.configured:
            raise DatabaseUnavailable("未配置 LUXAR_DATABASE_URL")
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as error:
            raise DatabaseUnavailable("未安装 PostgreSQL 驱动") from error

        try:
            self._pool = ConnectionPool(
                conninfo=self.settings.connection_string(),
                min_size=self.settings.min_pool_size,
                max_size=self.settings.max_pool_size,
                timeout=self.settings.timeout_seconds,
                open=False,
                kwargs={"autocommit": True},
            )
            self._pool.open(wait=True)
            if self.settings.auto_migrate:
                MigrationRunner(self._pool).apply(
                    include_vector=self.settings.require_vector
                )
        except Exception as error:
            self.close()
            raise DatabaseUnavailable("PostgreSQL 初始化失败") from error

    def close(self) -> None:
        checkpoint_pool, self._checkpoint_pool = self._checkpoint_pool, None
        if checkpoint_pool is not None:
            checkpoint_pool.close()
        pool, self._pool = self._pool, None
        self._checkpointer = None
        if pool is not None:
            pool.close()

    def health(self) -> bool:
        try:
            with self.pool.connection() as connection:
                row = connection.execute("SELECT 1").fetchone()
            return row is not None and row[0] == 1
        except Exception:
            return False

    def checkpointer(self) -> BaseCheckpointSaver:
        if self._checkpointer is not None:
            return self._checkpointer
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as error:
            raise DatabaseUnavailable(
                "未安装 langgraph-checkpoint-postgres"
            ) from error
        checkpoint_pool = ConnectionPool(
            conninfo=self.settings.connection_string(),
            min_size=self.settings.min_pool_size,
            max_size=self.settings.max_pool_size,
            timeout=self.settings.timeout_seconds,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        try:
            checkpoint_pool.open(wait=True)
            saver = PostgresSaver(checkpoint_pool)
            saver.setup()
        except Exception as error:
            checkpoint_pool.close()
            raise DatabaseUnavailable("PostgreSQL checkpoint 初始化失败") from error
        self._checkpoint_pool = checkpoint_pool
        self._checkpointer = saver
        return saver

    def __enter__(self) -> "DatabaseRuntime":
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
