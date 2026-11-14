"""Single-elimination bracket: shuffle, seeding, byes and advancement links."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .formats import MAX_PLAYERS


def bracket_size(n_players: int) -> int:
    if n_players < 2:
        raise ValueError("At least 2 players are needed")
    if n_players > MAX_PLAYERS:
        raise ValueError(f"At most {MAX_PLAYERS} players are supported")
    size = 2
    while size < n_players:
        size *= 2
    return size


def seed_order(size: int) -> list[int]:
    """Standard bracket order, e.g. 8 -> [1, 8, 4, 5, 2, 7, 3, 6].

    Adjacent pairs meet in round 1; top seeds are spread so they meet as late as possible,
    and seeds above the player count (byes) always face a real player.
    """
    seeds = [1]
    while len(seeds) < size:
        total = len(seeds) * 2 + 1
        seeds = [s for x in seeds for s in (x, total - x)]
    return seeds


@dataclass
class PlannedMatch:
    round_no: int
    slot: int  # 0-based within the round
    p1: int | None = None
    p2: int | None = None
    is_bye: bool = False
    winner: int | None = None
    next_slot_index: int | None = None  # slot in round_no + 1
    next_position: int | None = None  # 1 or 2


@dataclass
class PlannedBracket:
    size: int
    seeds: dict[int, int]  # player id -> seed
    rounds: list[list[PlannedMatch]] = field(default_factory=list)


def shuffle_players(player_ids: list[int], seed: str) -> list[int]:
    order = list(player_ids)
    random.Random(seed).shuffle(order)
    return order


def build_bracket(player_ids: list[int], seed: str) -> PlannedBracket:
    """Shuffle players with ``seed`` and build every round. Round-1 byes are resolved."""
    if len(set(player_ids)) != len(player_ids):
        raise ValueError("Duplicate players")
    size = bracket_size(len(player_ids))
    shuffled = shuffle_players(player_ids, seed)
    seeds = {pid: i + 1 for i, pid in enumerate(shuffled)}
    by_seed = {i + 1: pid for i, pid in enumerate(shuffled)}
    order = seed_order(size)

    bracket = PlannedBracket(size=size, seeds=seeds)
    n_rounds = size.bit_length() - 1
    for r in range(1, n_rounds + 1):
        count = size >> r
        matches = []
        for slot in range(count):
            m = PlannedMatch(round_no=r, slot=slot)
            if r < n_rounds:
                m.next_slot_index = slot // 2
                m.next_position = 1 if slot % 2 == 0 else 2
            matches.append(m)
        bracket.rounds.append(matches)

    for slot, m in enumerate(bracket.rounds[0]):
        m.p1 = by_seed.get(order[2 * slot])
        m.p2 = by_seed.get(order[2 * slot + 1])
        if m.p1 is None or m.p2 is None:
            m.is_bye = True
            m.winner = m.p1 if m.p1 is not None else m.p2
            if m.winner is None:  # cannot happen with standard seeding, guard anyway
                raise AssertionError("bye vs bye")
            if len(bracket.rounds) > 1:
                nxt = bracket.rounds[1][m.next_slot_index]
                if m.next_position == 1:
                    nxt.p1 = m.winner
                else:
                    nxt.p2 = m.winner
    return bracket
