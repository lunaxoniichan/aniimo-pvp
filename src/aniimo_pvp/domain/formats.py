"""Standard tournament formats: series length per round, counted back from the final."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

CAPS = (8, 16, 32, 64, 128)
MAX_PLAYERS = 128


@dataclass(frozen=True)
class Format:
    key: str
    label: str
    description: str
    bo_for: Callable[[int], int]  # players remaining in the round -> Bo


FORMATS: dict[str, Format] = {
    "quick": Format("quick", "Quick", "Bo1 every round, Bo3 final. Fastest.", lambda p: 3 if p == 2 else 1),
    "standard": Format(
        "standard",
        "Standard",
        "Bo1 until the last 16, then Bo3, Bo5 final.",
        lambda p: 5 if p == 2 else 3 if p <= 16 else 1,
    ),
    "championship": Format(
        "championship",
        "Championship",
        "Bo3 every round, Bo5 semifinal and final.",
        lambda p: 5 if p <= 4 else 3,
    ),
}
DEFAULT_FORMAT = "standard"
DEFAULT_CAP = 32


def round_name(players_in_round: int) -> str:
    if players_in_round == 2:
        return "Final"
    if players_in_round == 4:
        return "Semifinal"
    if players_in_round == 8:
        return "Quarterfinal"
    return f"Round of {players_in_round}"


def short_round_name(players_in_round: int) -> str:
    return {2: "F", 4: "SF", 8: "QF"}.get(players_in_round, f"R{players_in_round}")


def players_in_round(bracket_size: int, round_no: int) -> int:
    """Players at the start of round ``round_no`` (1-based)."""
    return bracket_size >> (round_no - 1)


def round_count(bracket_size: int) -> int:
    return bracket_size.bit_length() - 1


def bo_for_round(format_key: str, bracket_size: int, round_no: int) -> int:
    return FORMATS[format_key].bo_for(players_in_round(bracket_size, round_no))


@dataclass(frozen=True)
class RoundPlan:
    round_no: int
    name: str
    players: int
    matches: int
    bo: int


def plan(format_key: str, bracket_size: int) -> list[RoundPlan]:
    fmt = FORMATS[format_key]
    rounds = []
    for r in range(1, round_count(bracket_size) + 1):
        p = players_in_round(bracket_size, r)
        rounds.append(RoundPlan(r, round_name(p), p, p // 2, fmt.bo_for(p)))
    return rounds


def max_games(format_key: str, bracket_size: int) -> int:
    return sum(r.matches * r.bo for r in plan(format_key, bracket_size))
