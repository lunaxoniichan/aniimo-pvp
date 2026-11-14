import random

import pytest

from aniimo_pvp.domain.draft import DEFAULT_ORDER, DraftState, IllegalAction, phase_label, validate_order

ROSTER = [f"a{i:02d}" for i in range(30)]


def test_order_matches_spec():
    assert DEFAULT_ORDER == (
        ("A", "ban"),
        ("B", "ban"),
        ("A", "pick"),
        ("B", "pick"),
        ("B", "pick"),
        ("A", "pick"),
        ("B", "ban"),
        ("A", "ban"),
        ("B", "pick"),
        ("A", "pick"),
        ("A", "pick"),
        ("B", "pick"),
    )
    picks_a = [i + 1 for i, (s, k) in enumerate([x for x in DEFAULT_ORDER if x[1] == "pick"]) if s == "A"]
    assert picks_a == [1, 4, 6, 7]


def test_phase_labels():
    labels = [phase_label(DEFAULT_ORDER, i) for i in range(12)]
    assert labels[0] == labels[1] == "Phase 1 · Ban"
    assert labels[2] == labels[5] == "Phase 1 · Pick"
    assert labels[6] == labels[7] == "Phase 2 · Ban"
    assert labels[8] == labels[11] == "Phase 2 · Pick"
    assert phase_label(DEFAULT_ORDER, 12) == "Draft complete"


def test_full_draft_and_exclusivity():
    d = DraftState()
    for slug in ROSTER[:12]:
        d.apply(slug, ROSTER)
    assert d.complete
    assert d.picks("A") == ["a02", "a05", "a09", "a10"]
    assert d.picks("B") == ["a03", "a04", "a08", "a11"]
    assert d.banned == {"a00", "a01", "a06", "a07"}
    with pytest.raises(IllegalAction):
        d.apply("a20", ROSTER)


def test_illegal_choices():
    d = DraftState()
    d.apply("a00", ROSTER)  # A ban
    with pytest.raises(IllegalAction):
        d.apply("a00", ROSTER)  # banned
    d.apply("a01", ROSTER)  # B ban
    d.apply("a02", ROSTER)  # A pick
    with pytest.raises(IllegalAction):
        d.apply("a02", ROSTER)  # already picked by A: exclusive
    with pytest.raises(IllegalAction):
        d.apply("zzz", ROSTER)
    with pytest.raises(IllegalAction):
        d.apply(None, ROSTER)  # picks can't be skipped


def test_fearless_lock_only_blocks_picks():
    d = DraftState()
    d.apply("a05", ROSTER, locked={"a05"})  # bans ignore the lock
    d.apply("a06", ROSTER)
    with pytest.raises(IllegalAction):
        d.apply("a07", ROSTER, locked={"a07"})
    assert "a07" not in d.available(ROSTER, locked={"a07"})


def test_timeout_ban_skips_and_pick_is_random_legal():
    rng = random.Random(1)
    d = DraftState()
    a = d.timeout_action(rng, ROSTER)
    assert a.kind == "skip" and a.auto and a.slug is None
    d.apply("a01", ROSTER)
    a = d.timeout_action(rng, ROSTER)
    assert a.kind == "pick" and a.auto and a.slug in ROSTER and a.slug != "a01"


def test_random_drafts_never_illegal():
    rng = random.Random(42)
    for _ in range(1000):
        d = DraftState()
        locked_a = set(rng.sample(ROSTER, 3))
        while not d.complete:
            side = d.current[0]
            locked = locked_a if side == "A" else set()
            if rng.random() < 0.3:
                d.timeout_action(rng, ROSTER, locked)
            else:
                d.apply(rng.choice(d.available(ROSTER, locked)), ROSTER, locked)
        all_picks = d.picks("A") + d.picks("B")
        assert len(all_picks) == len(set(all_picks)) == 8
        assert not (set(all_picks) & d.banned)
        assert not (set(d.picks("A")) & locked_a)


def test_undo_restores_previous_state():
    d = DraftState()
    for slug in ROSTER[:5]:
        d.apply(slug, ROSTER)
    before = list(d.actions[:4])
    d.undo()
    assert d.actions == before and d.step == 4 and d.current == ("B", "pick")
    with pytest.raises(IllegalAction):
        DraftState().undo()


def test_tiny_roster_timeout_skips_when_nothing_left():
    d = DraftState()
    tiny = ["x", "y"]
    d.apply("x", tiny)
    d.apply("y", tiny)
    a = d.timeout_action(random.Random(0), tiny)
    assert a.kind == "skip"


def test_validate_order():
    assert validate_order([["a", "BAN"], ["B", "ban"], ["A", "pick"], ["B", "pick"]])[0] == ("A", "ban")
    with pytest.raises(ValueError):
        validate_order([["A", "ban"], ["A", "pick"], ["B", "pick"]])
    with pytest.raises(ValueError):
        validate_order([["C", "ban"]])
