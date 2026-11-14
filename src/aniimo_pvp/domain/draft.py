"""Ban/pick draft engine (pure, no I/O)."""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

DEFAULT_ORDER: tuple[tuple[str, str], ...] = (
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


class IllegalAction(ValueError):
    pass


def validate_order(order: Sequence[Sequence[str]]) -> tuple[tuple[str, str], ...]:
    out = []
    for i, entry in enumerate(order):
        if len(entry) != 2:
            raise ValueError(f"draft_order[{i}] must be [side, kind]")
        side, kind = str(entry[0]).upper(), str(entry[1]).lower()
        if side not in ("A", "B") or kind not in ("ban", "pick"):
            raise ValueError(f"draft_order[{i}] must be [A|B, ban|pick], got {entry!r}")
        out.append((side, kind))
    for kind in ("ban", "pick"):
        a = sum(1 for s, k in out if s == "A" and k == kind)
        b = sum(1 for s, k in out if s == "B" and k == kind)
        if a != b:
            raise ValueError(f"draft_order gives A {a} and B {b} {kind}s; they must be equal")
    if not any(k == "pick" for _, k in out):
        raise ValueError("draft_order needs at least one pick per side")
    return tuple(out)


def phase_label(order: Sequence[tuple[str, str]], step: int) -> str:
    """'Phase 2 · Ban' style label: a phase is a run of steps of the same kind."""
    if step >= len(order):
        return "Draft complete"
    kind = order[step][1]
    runs, prev = 0, None
    for i in range(step + 1):
        k = order[i][1]
        if k != prev:
            if k == kind:
                runs += 1
            prev = k
    return f"Phase {runs} · {kind.capitalize()}"


@dataclass(frozen=True)
class Action:
    step: int
    side: str
    kind: str  # ban | pick | skip
    slug: str | None
    auto: bool = False


@dataclass
class DraftState:
    order: tuple[tuple[str, str], ...] = DEFAULT_ORDER
    actions: list[Action] = field(default_factory=list)

    @property
    def step(self) -> int:
        return len(self.actions)

    @property
    def complete(self) -> bool:
        return self.step >= len(self.order)

    @property
    def current(self) -> tuple[str, str] | None:
        return None if self.complete else self.order[self.step]

    @property
    def banned(self) -> set[str]:
        return {a.slug for a in self.actions if a.kind == "ban" and a.slug}

    def picks(self, side: str) -> list[str]:
        return [a.slug for a in self.actions if a.kind == "pick" and a.side == side and a.slug]

    def bans(self, side: str) -> list[Action]:
        return [a for a in self.actions if self.order[a.step][1] == "ban" and a.side == side]

    @property
    def taken(self) -> set[str]:
        return self.banned | set(self.picks("A")) | set(self.picks("B"))

    def available(self, roster: Iterable[str], locked: Iterable[str] = ()) -> list[str]:
        """Legal choices for the current step. ``locked`` (Fearless) only restricts picks."""
        cur = self.current
        if cur is None:
            return []
        blocked = self.taken | (set(locked) if cur[1] == "pick" else set())
        return [s for s in roster if s not in blocked]

    def apply(
        self, slug: str | None, roster: Iterable[str], locked: Iterable[str] = (), *, auto: bool = False
    ) -> Action:
        cur = self.current
        if cur is None:
            raise IllegalAction("The draft is already complete")
        side, kind = cur
        if slug is None:
            if kind != "ban":
                raise IllegalAction("A pick can't be skipped")
            action = Action(self.step, side, "skip", None, auto)
        else:
            roster_set = set(roster)
            if slug not in roster_set:
                raise IllegalAction(f"Unknown Aniimo: {slug}")
            if slug in self.banned:
                raise IllegalAction("That Aniimo is banned")
            if slug in set(self.picks("A")) | set(self.picks("B")):
                raise IllegalAction("That Aniimo is already picked")
            if kind == "pick" and slug in set(locked):
                raise IllegalAction("Fearless: you already played that Aniimo in this series")
            action = Action(self.step, side, kind, slug, auto)
        self.actions.append(action)
        return action

    def timeout_action(self, rng: random.Random, roster: Sequence[str], locked: Iterable[str] = ()) -> Action:
        cur = self.current
        if cur is None:
            raise IllegalAction("The draft is already complete")
        if cur[1] == "ban":
            return self.apply(None, roster, locked, auto=True)
        options = self.available(roster, locked)
        if not options:
            # Nothing legal left (tiny roster): record a skip so the draft can finish.
            action = Action(self.step, cur[0], "skip", None, True)
            self.actions.append(action)
            return action
        return self.apply(rng.choice(options), roster, locked, auto=True)

    def undo(self) -> Action:
        if not self.actions:
            raise IllegalAction("Nothing to undo")
        return self.actions.pop()
