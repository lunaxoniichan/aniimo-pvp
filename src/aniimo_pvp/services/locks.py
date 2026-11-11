import asyncio
from collections import defaultdict


class LockRegistry:
    """One asyncio lock per match and per tournament. Never take a match lock while holding a tournament lock."""

    def __init__(self) -> None:
        self._match: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tournament: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def match(self, match_id: int) -> asyncio.Lock:
        return self._match[match_id]

    def tournament(self, tournament_id: int) -> asyncio.Lock:
        return self._tournament[tournament_id]
