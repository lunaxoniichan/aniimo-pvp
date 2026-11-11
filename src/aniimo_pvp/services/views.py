"""Read-only view models used by the renderers and the Discord layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from ..db.models import Entry, Game, Job, Match, Player, Tournament
from ..domain import formats
from ..domain.draft import DraftState, phase_label
from ..domain.roster import Aniimo
from ..domain.series import Score, score, wins_needed
from ..domain.states import GameStatus
from .common import current_game, draft_rows, games_of, get_match, results, to_state

if TYPE_CHECKING:
    from .context import AppContext


@dataclass
class PlayerInfo:
    id: int
    name: str
    uid: str
    is_fake: bool = False
    rating: int = 1000


@dataclass
class SlotView:
    kind: str  # ban | pick
    index: int  # 1-based per side
    state: str  # done | active | empty
    aniimo: Aniimo | None = None
    auto: bool = False
    skipped: bool = False


@dataclass
class StepView:
    num: int
    side: str
    kind: str
    state: str
    aniimo: Aniimo | None = None
    auto: bool = False
    skipped: bool = False


@dataclass
class MatchView:
    tournament: Tournament
    match: Match
    p1: PlayerInfo | None
    p2: PlayerInfo | None
    games: list[Game]
    game: Game | None
    score: Score
    draft: DraftState | None
    order: tuple[tuple[str, str], ...]
    roster_lookup: dict[str, Aniimo] = field(default_factory=dict)
    due: dict[str, datetime] = field(default_factory=dict)  # pending timer kind -> due time

    @property
    def round_name(self) -> str:
        size = self.tournament.bracket_size or 2
        return formats.round_name(formats.players_in_round(size, self.match.round))

    @property
    def label(self) -> str:
        return f"R{self.match.round} · M{self.match.slot + 1}"

    @property
    def wins_needed(self) -> int:
        return wins_needed(self.match.bo)

    def player(self, pid: int | None) -> PlayerInfo | None:
        if self.p1 and pid == self.p1.id:
            return self.p1
        if self.p2 and pid == self.p2.id:
            return self.p2
        return None

    def name(self, pid: int | None) -> str:
        p = self.player(pid)
        return p.name if p else "TBD"

    def opponent(self, pid: int | None) -> PlayerInfo | None:
        if self.p1 and pid == self.p1.id:
            return self.p2
        if self.p2 and pid == self.p2.id:
            return self.p1
        return None

    def score_of(self, pid: int | None) -> int:
        if self.p1 and pid == self.p1.id:
            return self.score.p1
        if self.p2 and pid == self.p2.id:
            return self.score.p2
        return 0

    @property
    def side_a(self) -> PlayerInfo | None:
        return self.player(self.game.side_a_id) if self.game else None

    @property
    def side_b(self) -> PlayerInfo | None:
        return self.player(self.game.side_b_id) if self.game else None

    @property
    def turn(self) -> tuple[str, str] | None:
        return self.draft.current if self.draft else None

    @property
    def turn_player(self) -> PlayerInfo | None:
        t = self.turn
        if t is None or self.game is None:
            return None
        return self.side_a if t[0] == "A" else self.side_b

    @property
    def phase(self) -> str:
        return phase_label(self.order, self.draft.step if self.draft else 0)

    @property
    def step_deadline(self) -> datetime | None:
        return self.game.step_deadline if self.game else None

    @property
    def replay_of_number(self) -> bool:
        return bool(self.game and self.game.attempt > 1)

    def _slots(self, side: str, kind: str) -> list[SlotView]:
        out = []
        done = self.draft.actions if self.draft else []
        step = len(done)
        k = 0
        for i, (s, kd) in enumerate(self.order):
            if s != side or kd != kind:
                continue
            k += 1
            if i < step:
                a = done[i]
                out.append(SlotView(kind, k, "done", self.roster_lookup.get(a.slug or ""), a.auto, a.kind == "skip"))
            elif i == step and self.draft is not None and not self.draft.complete:
                out.append(SlotView(kind, k, "active"))
            else:
                out.append(SlotView(kind, k, "empty"))
        return out

    def bans(self, side: str) -> list[SlotView]:
        return self._slots(side, "ban")

    def picks(self, side: str) -> list[SlotView]:
        return self._slots(side, "pick")

    def steps(self) -> list[StepView]:
        out = []
        done = self.draft.actions if self.draft else []
        for i, (s, kd) in enumerate(self.order):
            if i < len(done):
                a = done[i]
                out.append(
                    StepView(i + 1, s, kd, "done", self.roster_lookup.get(a.slug or ""), a.auto, a.kind == "skip")
                )
            elif i == len(done) and self.draft is not None and not self.draft.complete:
                out.append(StepView(i + 1, s, kd, "active"))
            else:
                out.append(StepView(i + 1, s, kd, "empty"))
        return out

    def confirmed_games(self) -> list[Game]:
        return [g for g in self.games if g.status == GameStatus.CONFIRMED]


async def _player_info(s, tournament_id: int, pid: int | None) -> PlayerInfo | None:
    if pid is None:
        return None
    e = await s.scalar(select(Entry).where(Entry.tournament_id == tournament_id, Entry.discord_id == pid))
    p = await s.get(Player, pid)
    name = e.display_name if e else (p.display_name if p else str(pid))
    uid = e.aniimo_uid if e else (p.aniimo_uid if p else "")
    return PlayerInfo(pid, name, uid, bool(p and p.is_fake), p.rating if p else 1000)


async def load_match_view(ctx: AppContext, match_id: int, game_id: int | None = None) -> MatchView:
    async with ctx.db.session() as s:
        m = await get_match(s, match_id)
        t = await s.get(Tournament, m.tournament_id)
        games = await games_of(s, m.id)
        game = await s.get(Game, game_id) if game_id else await current_game(s, m.id)
        draft = to_state(ctx.order, await draft_rows(s, game.id)) if game else None
        p1 = await _player_info(s, t.id, m.p1_id)
        p2 = await _player_info(s, t.id, m.p2_id)
        sc = score(results(games), m.p1_id or -1, m.p2_id or -2)
        due: dict[str, datetime] = {}
        refs = [(Job.ref_id == m.id) & Job.kind.in_(["noshow_reminder", "noshow_escalate", "rematch_offer"])]
        if game:
            refs.append((Job.ref_id == game.id) & Job.kind.in_(["side_choice", "draft_step", "auto_confirm"]))
        for j in await s.scalars(select(Job).where(Job.status == "pending", refs[0] | refs[-1])):
            due[j.kind] = j.due_at
        return MatchView(t, m, p1, p2, games, game, sc, draft, ctx.order, ctx.roster.by_slug, due)


@dataclass
class BracketBox:
    match_id: int
    round_no: int
    slot: int
    p1: str | None
    p2: str | None
    seed1: int | None
    seed2: int | None
    winner: int | None  # 1 | 2
    is_bye: bool
    state: str
    score: str | None = None
    feeder1: str | None = None  # "Winner M3" when the player is unknown
    feeder2: str | None = None


async def load_bracket(ctx: AppContext, tournament_id: int) -> tuple[Tournament, list[list[BracketBox]]]:
    async with ctx.db.session() as s:
        t = await s.get(Tournament, tournament_id)
        entries = {e.discord_id: e for e in await s.scalars(select(Entry).where(Entry.tournament_id == tournament_id))}
        matches = list(
            await s.scalars(select(Match).where(Match.tournament_id == tournament_id).order_by(Match.round, Match.slot))
        )
        all_games = list(
            await s.scalars(
                select(Game).join(Match, Match.id == Game.match_id).where(Match.tournament_id == tournament_id)
            )
        )
    by_match: dict[int, list[Game]] = {}
    for g in all_games:
        by_match.setdefault(g.match_id, []).append(g)
    rounds: list[list[BracketBox]] = []
    for m in matches:
        while len(rounds) < m.round:
            rounds.append([])
        e1, e2 = entries.get(m.p1_id or -1), entries.get(m.p2_id or -1)
        sc = None
        if m.p1_id and m.p2_id and by_match.get(m.id):
            s_ = score(results(by_match[m.id]), m.p1_id, m.p2_id)
            if s_.p1 or s_.p2:
                sc = s_.text()
        winner = None
        if m.winner_id is not None:
            winner = 1 if m.winner_id == m.p1_id else 2 if m.winner_id == m.p2_id else None
        box = BracketBox(
            m.id,
            m.round,
            m.slot,
            e1.display_name if e1 else None,
            e2.display_name if e2 else None,
            e1.seed if e1 and m.round == 1 else None,
            e2.seed if e2 and m.round == 1 else None,
            winner,
            m.is_bye,
            m.state,
            sc,
        )
        if m.round > 1:
            prev = formats.short_round_name((t.bracket_size or 2) >> (m.round - 2))

            def feeder(n: int, prev: str = prev) -> str:
                return f"Winner {prev}{n}" if prev in ("QF", "SF") else f"Winner {prev} #{n}"

            box.feeder1 = feeder(m.slot * 2 + 1)
            box.feeder2 = feeder(m.slot * 2 + 2)
        rounds[m.round - 1].append(box)
    return t, rounds
