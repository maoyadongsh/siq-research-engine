#!/usr/bin/env python3
"""Apply numbered PostgreSQL migrations exactly once with checksum audit."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import psycopg

MIGRATIONS_ROOT = Path(__file__).resolve().parents[1] / "migrations"
MIGRATION_LOCK_KEY = 1_393_202_610


def _database_url() -> str:
    value = (os.getenv("SIQ_APP_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
    value = value.replace("postgresql+psycopg://", "postgresql://", 1)
    if not value.startswith("postgresql://"):
        raise RuntimeError("PostgreSQL SIQ_APP_DATABASE_URL is required for application migrations")
    return value


def _migration_files() -> list[Path]:
    files = sorted(MIGRATIONS_ROOT.glob("[0-9][0-9][0-9]_*.sql"))
    if not files:
        raise RuntimeError(f"no numbered migrations found under {MIGRATIONS_ROOT}")
    return files


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_migrations() -> list[str]:
    applied: list[str] = []
    with psycopg.connect(_database_url(), autocommit=True) as connection:
        connection.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
        try:
            with connection.transaction():
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version VARCHAR(255) PRIMARY KEY,
                        checksum VARCHAR(64) NOT NULL,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
            for path in _migration_files():
                version = path.name
                checksum = _checksum(path)
                existing = connection.execute(
                    "SELECT checksum FROM schema_migrations WHERE version = %s",
                    (version,),
                ).fetchone()
                if existing is not None:
                    if existing[0] != checksum:
                        raise RuntimeError(
                            f"migration checksum changed after apply: {version}; "
                            "create a new forward migration instead"
                        )
                    continue
                with connection.transaction():
                    connection.execute(path.read_text(encoding="utf-8"))
                    connection.execute(
                        "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                        (version, checksum),
                    )
                applied.append(version)
        finally:
            connection.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
    return applied


def main() -> None:
    applied = run_migrations()
    if applied:
        print("Applied migrations: " + ", ".join(applied))
    else:
        print("Application database is already current.")


if __name__ == "__main__":
    main()
