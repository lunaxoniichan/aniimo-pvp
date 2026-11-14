"""The seam between services and Discord. Services emit events after committing."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class Notifier:
    async def tournament_event(self, tournament_id: int, event: str, **info: Any) -> None:
        return None

    async def match_event(self, match_id: int, event: str, **info: Any) -> None:
        return None


class RecordingNotifier(Notifier):
    """Keeps every event; used by tests and dry runs."""

    def __init__(self) -> None:
        self.events: list[tuple[str, int, str, dict[str, Any]]] = []

    async def tournament_event(self, tournament_id: int, event: str, **info: Any) -> None:
        self.events.append(("tournament", tournament_id, event, info))

    async def match_event(self, match_id: int, event: str, **info: Any) -> None:
        self.events.append(("match", match_id, event, info))

    def names(self, scope: str | None = None, ref: int | None = None) -> list[str]:
        return [e for s, r, e, _ in self.events if (scope is None or s == scope) and (ref is None or r == ref)]

    def last(self, event: str) -> dict[str, Any]:
        for _, _, e, info in reversed(self.events):
            if e == event:
                return info
        raise KeyError(event)

    def clear(self) -> None:
        self.events.clear()


class Outbox:
    """Collects events during a transaction; flushed after commit."""

    def __init__(self) -> None:
        self.match_events: list[tuple[int, str, dict[str, Any]]] = []
        self.tournament_events: list[tuple[int, str, dict[str, Any]]] = []
        self.finished_matches: list[int] = []

    def match(self, match_id: int, event: str, **info: Any) -> None:
        self.match_events.append((match_id, event, info))

    def tournament(self, tournament_id: int, event: str, **info: Any) -> None:
        self.tournament_events.append((tournament_id, event, info))

    async def flush(self, notifier: Notifier) -> None:
        tev, mev = self.tournament_events, self.match_events
        self.tournament_events, self.match_events = [], []
        for tid, event, info in tev:
            try:
                await notifier.tournament_event(tid, event, **info)
            except Exception:  # never let Discord problems undo game state
                log.exception("tournament_event %s failed", event)
        for mid, event, info in mev:
            try:
                await notifier.match_event(mid, event, **info)
            except Exception:
                log.exception("match_event %s failed", event)
