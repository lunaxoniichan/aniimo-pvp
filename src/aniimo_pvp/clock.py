"""Clock abstraction so tests can control time. All datetimes are naive UTC."""

from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def unix(dt: datetime) -> int:
    return calendar.timegm(dt.timetuple())


class Clock:
    def now(self) -> datetime:
        return utcnow()


class FakeClock(Clock):
    def __init__(self, start: datetime | None = None):
        self.t = start or datetime(2026, 11, 11, 20, 0, 0)

    def now(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)
