"""Durable local storage plus optional PostgreSQL compatibility adapters."""

from luxar.database.local_runtime import LocalStorageRuntime
from luxar.database.local_settings import LocalStorageSettings

from luxar.database.persistence import (
    PendingApprovalRecord,
    PersistencePort,
    PostgresPersistence,
    TransientPersistence,
)
from luxar.database.runtime import DatabaseRuntime, DatabaseUnavailable
from luxar.database.settings import DatabaseSettings
from luxar.database.sqlite_persistence import SQLitePersistence

__all__ = [
    "DatabaseRuntime",
    "DatabaseSettings",
    "DatabaseUnavailable",
    "LocalStorageRuntime",
    "LocalStorageSettings",
    "PendingApprovalRecord",
    "PersistencePort",
    "PostgresPersistence",
    "SQLitePersistence",
    "TransientPersistence",
]
