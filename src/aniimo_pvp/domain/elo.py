"""ELO rating update per match."""

from __future__ import annotations

DEFAULT_RATING = 1000
K_FACTOR = 32


def expected(ra: int, rb: int) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400))


def update(winner_rating: int, loser_rating: int, k: int = K_FACTOR) -> tuple[int, int]:
    """Return (winner_delta, loser_delta). The total is conserved."""
    delta = round(k * (1 - expected(winner_rating, loser_rating)))
    delta = max(1, delta)
    return delta, -delta
