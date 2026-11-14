"""Async SQLite engine + session factory."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from .models import Base

# Columns added after the first release. create_all() makes new tables but never changes existing ones,
# so older databases get these with ALTER TABLE (SQLite can add columns with a default in place).
ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("tournaments", "kind", "VARCHAR(16) NOT NULL DEFAULT 'tournament'"),
]
ADDED_INDEXES = ["CREATE INDEX IF NOT EXISTS ix_tournaments_kind ON tournaments (kind)"]


def _upgrade(sync_conn) -> list[str]:
    added = []
    for table, column, ddl in ADDED_COLUMNS:
        cols = {row[1] for row in sync_conn.exec_driver_sql(f"PRAGMA table_info({table})")}
        if cols and column not in cols:
            sync_conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            added.append(f"{table}.{column}")
    if added:
        for ddl in ADDED_INDEXES:
            sync_conn.exec_driver_sql(ddl)
    return added


class Database:
    def __init__(self, path: str):
        self.path = path
        if path == ":memory:":
            self.engine = create_async_engine(
                "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
            )
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

        file_db = path != ":memory:"

        @event.listens_for(self.engine.sync_engine, "connect")
        def _pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            if file_db:
                dbapi_conn.isolation_level = None  # we emit BEGIN ourselves (see _begin)
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if file_db:
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=10000")
            cur.close()

        if file_db:

            @event.listens_for(self.engine.sync_engine, "begin")
            def _begin(conn):  # pragma: no cover - trivial
                # take the write lock up front: concurrent writers wait instead of failing on a stale snapshot
                conn.exec_driver_sql("BEGIN IMMEDIATE")

        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_all(self) -> list[str]:
        """Create missing tables and add columns that older databases don't have yet."""
        async with self.engine.begin() as conn:
            added = await conn.run_sync(_upgrade)
            await conn.run_sync(Base.metadata.create_all)
        return added

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as s:
            yield s

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as s, s.begin():
            yield s

    async def dispose(self) -> None:
        await self.engine.dispose()


def backup_database(db_path: str, backups_dir: str, keep: int = 10) -> Path | None:
    src = Path(db_path)
    if not src.exists() or src.stat().st_size == 0:
        return None
    dest_dir = Path(backups_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"aniimo-{datetime.now():%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(src) as source, sqlite3.connect(dest) as target:
        source.backup(target)  # consistent copy even with a WAL file
    source.close()
    target.close()
    backups = sorted(dest_dir.glob("aniimo-*.db"))
    for old in backups[:-keep]:
        old.unlink(missing_ok=True)
    return dest
