import pytest

from aniimo_pvp.domain import formats
from aniimo_pvp.domain.bracket import bracket_size, build_bracket, seed_order

# design §3.4
MAX_GAMES = {
    8: {"quick": 9, "standard": 23, "championship": 27},
    16: {"quick": 17, "standard": 47, "championship": 51},
    32: {"quick": 33, "standard": 63, "championship": 99},
    64: {"quick": 65, "standard": 95, "championship": 195},
    128: {"quick": 129, "standard": 159, "championship": 387},
}


@pytest.mark.parametrize("cap", formats.CAPS)
@pytest.mark.parametrize("fmt", list(formats.FORMATS))
def test_max_games_match_design(cap, fmt):
    assert formats.max_games(fmt, cap) == MAX_GAMES[cap][fmt]


def test_series_per_stage_match_design():
    stages = [128, 64, 32, 16, 8, 4, 2]
    expect = {
        "quick": [1, 1, 1, 1, 1, 1, 3],
        "standard": [1, 1, 1, 3, 3, 3, 5],
        "championship": [3, 3, 3, 3, 3, 5, 5],
    }
    for fmt, bos in expect.items():
        assert [formats.FORMATS[fmt].bo_for(p) for p in stages] == bos


def test_plan_names_and_counts():
    plan = formats.plan("standard", 32)
    assert [r.name for r in plan] == ["Round of 32", "Round of 16", "Quarterfinal", "Semifinal", "Final"]
    assert [r.matches for r in plan] == [16, 8, 4, 2, 1]
    assert formats.round_count(128) == 7
    assert formats.bo_for_round("standard", 8, 1) == 3  # an 8 bracket starts at the quarterfinal


def test_seed_order():
    assert seed_order(2) == [1, 2]
    assert seed_order(4) == [1, 4, 2, 3]
    assert seed_order(8) == [1, 8, 4, 5, 2, 7, 3, 6]


@pytest.mark.parametrize("n", range(2, 129))
def test_bracket_properties(n):
    players = list(range(1000, 1000 + n))
    b = build_bracket(players, seed="abc")
    size = bracket_size(n)
    assert b.size == size and size >= n and size < 2 * n or n == 2
    r1 = b.rounds[0]
    assert len(r1) == size // 2
    byes = [m for m in r1 if m.is_bye]
    assert len(byes) == size - n
    placed = [p for m in r1 for p in (m.p1, m.p2) if p is not None]
    assert sorted(placed) == players  # everyone exactly once
    for m in r1:
        assert m.p1 is not None or m.p2 is not None  # never bye vs bye
    assert len(b.rounds) == size.bit_length() - 1
    assert len(b.rounds[-1]) == 1


def test_bye_winner_advances_to_correct_slot():
    b = build_bracket([1, 2, 3, 4, 5], seed="s")
    for m in b.rounds[0]:
        if m.is_bye:
            nxt = b.rounds[1][m.next_slot_index]
            assert (nxt.p1 if m.next_position == 1 else nxt.p2) == m.winner


def test_same_seed_same_bracket_and_different_seed_differs():
    players = list(range(1, 33))
    a = build_bracket(players, "seed-1")
    b = build_bracket(players, "seed-1")
    c = build_bracket(players, "seed-2")
    pairs = lambda br: [(m.p1, m.p2) for m in br.rounds[0]]  # noqa: E731
    assert pairs(a) == pairs(b)
    assert pairs(a) != pairs(c)


def test_bracket_limits():
    with pytest.raises(ValueError):
        bracket_size(1)
    with pytest.raises(ValueError):
        bracket_size(129)
    with pytest.raises(ValueError):
        build_bracket([1, 1, 2], "x")
