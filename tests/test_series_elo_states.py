import pytest

from aniimo_pvp.domain import elo
from aniimo_pvp.domain.series import (
    GameResult,
    can_next_game,
    next_side_chooser,
    score,
    series_winner,
    wins_needed,
)
from aniimo_pvp.domain.states import InvalidTransition, check_transition

P1, P2 = 1, 2


def g(n, a, status, w=None):
    return GameResult(n, a, status, w)


def test_wins_needed():
    assert [wins_needed(b) for b in (1, 3, 5)] == [1, 2, 3]
    with pytest.raises(ValueError):
        wins_needed(2)


@pytest.mark.parametrize("bo,results,winner", [
    (1, [P1], P1),
    (3, [P1, P1], P1),
    (3, [P1, P2, P2], P2),
    (5, [P1, P2, P1, P2, P1], P1),
    (3, [P1], None),
])
def test_series_detection(bo, results, winner):
    games = [g(i + 1, 1, "confirmed", w) for i, w in enumerate(results)]
    assert series_winner(score(games, P1, P2), bo, P1, P2) == winner


def test_design_example_rematch_vs_next_game():
    # design §4.1: Bo3 with one same-teams rematch -> 2-1
    games = [
        g(1, 1, "confirmed", P1),
        g(2, 1, "voided"),
        g(2, 2, "confirmed", P2),
        g(3, 1, "confirmed", P1),
    ]
    sc = score(games, P1, P2)
    assert (sc.p1, sc.p2) == (2, 1)
    assert series_winner(sc, 3, P1, P2) == P1


def test_voided_never_counts():
    games = [g(1, 1, "voided", P1), g(1, 2, "confirmed", P2)]
    assert score(games, P1, P2).p1 == 0


def test_can_next_game_rules():
    assert not can_next_game([g(1, 1, "confirmed", P1)], 1, P1, P2)  # never in Bo1
    assert can_next_game([g(1, 1, "confirmed", P1)], 3, P1, P2)
    assert not can_next_game([g(1, 1, "playing")], 3, P1, P2)  # latest not confirmed
    assert not can_next_game([g(1, 1, "confirmed", P1), g(2, 1, "confirmed", P1)], 3, P1, P2)  # decided
    # a voided attempt with no replacement yet is not "latest"; game 1 confirmed -> next allowed
    assert can_next_game([g(1, 1, "confirmed", P1), g(2, 1, "voided")], 3, P1, P2)
    assert not can_next_game([g(1, 1, "confirmed", P1), g(2, 1, "voided"), g(2, 2, "playing")], 3, P1, P2)


def test_loser_chooses_side():
    assert next_side_chooser([g(1, 1, "confirmed", P1)], P1, P2) == P2
    assert next_side_chooser([g(1, 1, "confirmed", P1), g(2, 1, "confirmed", P2)], P1, P2) == P1
    assert next_side_chooser([], P1, P2) is None


def test_elo():
    w, lo = elo.update(1000, 1000)
    assert (w, lo) == (16, -16)
    w, lo = elo.update(1200, 1000)
    assert 0 < w < 16 and w + lo == 0
    w, lo = elo.update(1000, 1400)
    assert w > 16


def test_transitions():
    check_transition("waiting", "side_choice")
    check_transition("playing", "playing")  # same-teams rematch
    with pytest.raises(InvalidTransition):
        check_transition("done", "waiting")
    with pytest.raises(InvalidTransition):
        check_transition("waiting", "playing")
