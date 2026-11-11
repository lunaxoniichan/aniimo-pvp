"""Best-of-N series scoring. Only confirmed games count."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .states import GameStatus


def wins_needed(bo: int) -> int:
    if bo < 1 or bo % 2 == 0:
        raise ValueError("Bo must be an odd number >= 1")
    return bo // 2 + 1


@dataclass(frozen=True)
class GameResult:
    number: int
    attempt: int
    status: str
    winner_id: int | None


@dataclass(frozen=True)
class Score:
    p1: int
    p2: int

    def text(self) -> str:
        return f"{self.p1}–{self.p2}"


def score(games: Iterable[GameResult], p1: int, p2: int) -> Score:
    a = b = 0
    for g in games:
        if g.status != GameStatus.CONFIRMED:
            continue
        if g.winner_id == p1:
            a += 1
        elif g.winner_id == p2:
            b += 1
    return Score(a, b)


def series_winner(sc: Score, bo: int, p1: int, p2: int) -> int | None:
    need = wins_needed(bo)
    if sc.p1 >= need:
        return p1
    if sc.p2 >= need:
        return p2
    return None


def can_next_game(games: list[GameResult], bo: int, p1: int, p2: int) -> bool:
    """Next game exists only in Bo3/Bo5, after the latest game is confirmed, while undecided."""
    if bo == 1 or not games:
        return False
    live = [g for g in games if g.status != GameStatus.VOIDED]
    if not live:
        return False
    latest = max(live, key=lambda g: (g.number, g.attempt))
    if latest.status != GameStatus.CONFIRMED:
        return False
    return series_winner(score(games, p1, p2), bo, p1, p2) is None


def next_side_chooser(games: list[GameResult], p1: int, p2: int) -> int | None:
    """The loser of the latest confirmed game chooses the side for the next game."""
    confirmed = [g for g in games if g.status == GameStatus.CONFIRMED]
    if not confirmed:
        return None
    last = max(confirmed, key=lambda g: (g.number, g.attempt))
    return p2 if last.winner_id == p1 else p1
