"""Async SQLite engine + session factory."""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from .models import Base


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

        @event.listens_for(self.engine.sync_engine, "connect")
        def _pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if path != ":memory:":
                cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

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
    shutil.copy2(src, dest)
    backups = sorted(dest_dir.glob("aniimo-*.db"))
    for old in backups[:-keep]:
        old.unlink(missing_ok=True)
    return dest
