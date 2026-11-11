"""Persistent timers. Every timer is a row in `jobs`, so it survives a restart."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..clock import Clock
from ..db.database import Database
from ..db.models import Job

log = logging.getLogger(__name__)
JobHandler = Callable[[Job], Awaitable[None]]


class Scheduler:
    def __init__(self, db: Database, clock: Clock):
        self.db = db
        self.clock = clock
        self.handlers: dict[str, JobHandler] = {}
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False

    def register(self, kind: str, handler: JobHandler) -> None:
        self.handlers[kind] = handler

    async def schedule(self, s: AsyncSession, kind: str, ref_id: int, seconds: float,
                       payload: dict[str, Any] | None = None) -> Job:
        job = Job(kind=kind, ref_id=ref_id, due_at=self.clock.now() + timedelta(seconds=seconds),
                  duration_seconds=float(seconds), payload=payload, status="pending")
        s.add(job)
        await s.flush()
        self._wake.set()
        return job

    async def cancel(self, s: AsyncSession, kinds: str | Iterable[str], ref_id: int) -> None:
        kinds = [kinds] if isinstance(kinds, str) else list(kinds)
        await s.execute(
            update(Job)
            .where(Job.kind.in_(kinds), Job.ref_id == ref_id, Job.status == "pending")
            .values(status="cancelled")
        )

    async def pending(self, kind: str, ref_id: int) -> list[Job]:
        async with self.db.session() as s:
            rows = await s.scalars(select(Job).where(Job.kind == kind, Job.ref_id == ref_id, Job.status == "pending"))
            return list(rows)

    async def run_due(self) -> int:
        now = self.clock.now()
        async with self.db.transaction() as s:
            jobs = list(await s.scalars(
                select(Job).where(Job.status == "pending", Job.due_at <= now).order_by(Job.due_at, Job.id)
            ))
            for job in jobs:
                job.status = "running"
        for job in jobs:
            handler = self.handlers.get(job.kind)
            try:
                if handler is None:
                    log.warning("No handler for job kind %s", job.kind)
                else:
                    await handler(job)
            except Exception:
                log.exception("Job %s #%s failed", job.kind, job.id)
            finally:
                async with self.db.transaction() as s:
                    await s.execute(update(Job).where(Job.id == job.id, Job.status == "running").values(status="done"))
        return len(jobs)

    async def recover(self) -> int:
        """After a restart, give overdue timers their full duration again (players couldn't click meanwhile)."""
        now = self.clock.now()
        count = 0
        async with self.db.transaction() as s:
            jobs = await s.scalars(
                select(Job).where(((Job.status == "pending") & (Job.due_at <= now)) | (Job.status == "running"))
            )
            for job in jobs:
                job.status = "pending"
                job.due_at = now + timedelta(seconds=job.duration_seconds)
                count += 1
        return count

    async def next_due_in(self) -> float | None:
        async with self.db.session() as s:
            due = await s.scalar(select(Job.due_at).where(Job.status == "pending").order_by(Job.due_at).limit(1))
        if due is None:
            return None
        return max(0.0, (due - self.clock.now()).total_seconds())

    async def run_forever(self, max_sleep: float = 1.0) -> None:
        while not self._stopping:
            try:
                await self.run_due()
                wait = await self.next_due_in()
                wait = max_sleep if wait is None else min(max_sleep, wait)
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=max(0.05, wait))
                except TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Scheduler loop error")
                await asyncio.sleep(1)

    def start(self) -> None:
        if self._task is None:
            self._stopping = False
            self._task = asyncio.create_task(self.run_forever(), name="scheduler")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
