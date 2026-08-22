"""Small, transactional SQL migration runner with a PostgreSQL advisory lock."""

from __future__ import annotations

from importlib.resources import files
from typing import Any


_MIGRATION_LOCK_ID = 4_858_821_923


class MigrationRunner:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def apply(self, *, include_vector: bool = True) -> list[str]:
        applied: list[str] = []
        resources = files("luxar.database.migrations")
        migrations = sorted(
            (item for item in resources.iterdir() if item.name.endswith(".sql")),
            key=lambda item: item.name,
        )

        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    (_MIGRATION_LOCK_ID,),
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS luxar_schema_migrations (
                        version text PRIMARY KEY,
                        applied_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
                rows = connection.execute(
                    "SELECT version FROM luxar_schema_migrations"
                ).fetchall()
                existing = {str(row[0]) for row in rows}

                for migration in migrations:
                    if migration.name in existing:
                        continue
                    if not include_vector and migration.name.startswith("002_"):
                        continue
                    connection.execute(migration.read_text(encoding="utf-8"))
                    connection.execute(
                        "INSERT INTO luxar_schema_migrations(version) VALUES (%s)",
                        (migration.name,),
                    )
                    applied.append(migration.name)
        return applied
