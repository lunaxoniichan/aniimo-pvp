"""Tournament lifecycle: create (step 1), players (step 2), start, rounds, advancement, finish."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select

from ..db.models import Entry, Game, Match, Player, Tournament
from ..domain import elo, formats
from ..domain.bracket import bracket_size, build_bracket
from ..domain.players_csv import CsvIssue, parse_players_csv
from ..domain.states import ACTIVE_TOURNAMENT, EntryStatus, GameStatus, MatchState, TournamentStatus
from .common import (
    GAME_JOBS,
    JOB_NOSHOW_ESCALATE,
    JOB_NOSHOW_REMINDER,
    MATCH_JOBS,
    audit,
    entry_for,
    get_tournament,
)
from .errors import UserError
from .notifier import Outbox

if TYPE_CHECKING:
    from .context import AppContext

MemberLookup = Callable[[int], Awaitable[str | None]]  # returns the member's display name, or None


@dataclass
class PreviewRow:
    line: int
    discord_id: int
    aniimo_uid: str
    display_name: str
    fake: bool = False


@dataclass
class PlayersPreview:
    rows: list[PreviewRow] = field(default_factory=list)
    issues: list[CsvIssue] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)  # reasons Confirm is disabled
    cap: int = 0
    bracket_size: int | None = None
    byes: int = 0
    rounds: list[formats.RoundPlan] = field(default_factory=list)

    @property
    def can_confirm(self) -> bool:
        return not self.blocking and len(self.rows) >= 2


class TournamentService:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx

    # ----- queries -----
    async def active(self, guild_id: int) -> Tournament | None:
        async with self.ctx.db.session() as s:
            return await s.scalar(
                select(Tournament)
                .where(Tournament.guild_id == guild_id, Tournament.status.in_([x.value for x in ACTIVE_TOURNAMENT]))
                .order_by(Tournament.id.desc())
                .limit(1)
            )

    async def latest(self, guild_id: int) -> Tournament | None:
        async with self.ctx.db.session() as s:
            return await s.scalar(
                select(Tournament).where(Tournament.guild_id == guild_id).order_by(Tournament.id.desc()).limit(1)
            )

    async def require_active(self, guild_id: int, *statuses: TournamentStatus) -> Tournament:
        t = await self.active(guild_id)
        if t is None:
            raise UserError("There's no active tournament. Start one with /tournament create.")
        if statuses and t.status not in {x.value for x in statuses}:
            raise UserError(f"{t.name} is in the {t.status} stage, so that isn't possible now")
        return t

    async def get(self, tournament_id: int) -> Tournament:
        async with self.ctx.db.session() as s:
            return await get_tournament(s, tournament_id)

    async def entries(self, tournament_id: int) -> list[Entry]:
        async with self.ctx.db.session() as s:
            rows = await s.scalars(select(Entry).where(Entry.tournament_id == tournament_id).order_by(Entry.id))
            return list(rows)

    async def matches(self, tournament_id: int, round_no: int | None = None) -> list[Match]:
        async with self.ctx.db.session() as s:
            q = select(Match).where(Match.tournament_id == tournament_id)
            if round_no is not None:
                q = q.where(Match.round == round_no)
            return list(await s.scalars(q.order_by(Match.round, Match.slot)))

    # ----- step 1: create + options -----
    async def create(self, guild_id: int, name: str, actor_id: int) -> Tournament:
        name = name.strip()[:100]
        if not name:
            raise UserError("Give the tournament a name")
        out = Outbox()
        async with self.ctx.db.transaction() as s:
            existing = await s.scalar(
                select(Tournament).where(
                    Tournament.guild_id == guild_id, Tournament.status.in_([x.value for x in ACTIVE_TOURNAMENT])
                )
            )
            if existing:
                raise UserError(f"{existing.name} is still {existing.status}. Finish or /tournament cancel it first.")
            t = Tournament(guild_id=guild_id, name=name, status=TournamentStatus.SETUP.value,
                           player_cap=formats.DEFAULT_CAP, format=formats.DEFAULT_FORMAT, fearless=False,
                           created_by=actor_id, created_at=self.ctx.clock.now())
            s.add(t)
            await s.flush()
            await audit(s, t.id, None, actor_id, "tournament_create", name=name)
            out.tournament(t.id, "setup_created")
        await out.flush(self.ctx.notifier)
        return t

    async def set_options(self, tournament_id: int, actor_id: int, *, cap: int | None = None,
                          fmt: str | None = None, fearless: bool | None = None, name: str | None = None) -> Tournament:
        out = Outbox()
        async with self.ctx.locks.tournament(tournament_id), self.ctx.db.transaction() as s:
            t = await get_tournament(s, tournament_id)
            if t.status not in (TournamentStatus.SETUP, TournamentStatus.REGISTRATION):
                raise UserError("Format and player cap are locked once the tournament has started")
            if cap is not None:
                if cap not in formats.CAPS:
                    raise UserError(f"Player cap must be one of {', '.join(map(str, formats.CAPS))}")
                count = await s.scalar(select(func.count()).select_from(Entry).where(Entry.tournament_id == t.id))
                if count and count > cap:
                    raise UserError(f"{count} players are already registered, more than a cap of {cap}")
                t.player_cap = cap
            if fmt is not None:
                if fmt not in formats.FORMATS:
                    raise UserError("Unknown format")
                t.format = fmt
            if fearless is not None:
                t.fearless = fearless
            if name is not None and name.strip():
                t.name = name.strip()[:100]
            await audit(s, t.id, None, actor_id, "tournament_options", cap=cap, fmt=fmt, fearless=fearless, name=name)
            out.tournament(t.id, "setup_changed")
        await out.flush(self.ctx.notifier)
        return t

    async def to_registration(self, tournament_id: int, actor_id: int) -> Tournament:
        out = Outbox()
        async with self.ctx.locks.tournament(tournament_id), self.ctx.db.transaction() as s:
            t = await get_tournament(s, tournament_id)
            if t.status == TournamentStatus.SETUP:
                t.status = TournamentStatus.REGISTRATION.value
                await audit(s, t.id, None, actor_id, "registration_open")
                out.tournament(t.id, "registration_opened")
            elif t.status != TournamentStatus.REGISTRATION:
                raise UserError("Registration is closed")
        await out.flush(self.ctx.notifier)
        return t

    # ----- step 2: players -----
    async def preview_players(self, tournament_id: int, actor_id: int, text: str,
                              lookup: MemberLookup | None = None) -> PlayersPreview:
        dev = self.ctx.settings.dev_mode
        out = Outbox()
        async with self.ctx.locks.tournament(tournament_id):
            async with self.ctx.db.session() as s:
                t = await get_tournament(s, tournament_id)
                if t.status not in (TournamentStatus.SETUP, TournamentStatus.REGISTRATION):
                    raise UserError("The player list is locked once the tournament has started")
                cap, fmt = t.player_cap, t.format
            parsed, issues = parse_players_csv(text, strict_ids=not dev)
            preview = PlayersPreview(issues=list(issues), cap=cap)
            for row in parsed:
                member_name = await lookup(row.discord_id) if lookup else None
                fake = False
                if lookup is not None and member_name is None:
                    if dev:
                        fake = True
                    else:
                        preview.issues.append(CsvIssue(row.line, "not a member of this server", row.discord_id))
                        continue
                name = row.display_name or member_name or f"Player {str(row.discord_id)[-4:]}"
                preview.rows.append(PreviewRow(row.line, row.discord_id, row.aniimo_uid, name, fake))
            n = len(preview.rows)
            if n > cap:
                preview.blocking.append(f"{n} players is more than the cap of {cap}. Raise the cap or trim the list.")
            if n > formats.MAX_PLAYERS:
                preview.blocking.append(f"At most {formats.MAX_PLAYERS} players are supported")
            if n < 2:
                preview.blocking.append("At least 2 valid players are needed")
            if 2 <= n <= formats.MAX_PLAYERS:
                preview.bracket_size = bracket_size(n)
                preview.byes = preview.bracket_size - n
                preview.rounds = formats.plan(fmt, preview.bracket_size)
            async with self.ctx.db.transaction() as s:
                t = await get_tournament(s, tournament_id)
                if t.status == TournamentStatus.SETUP:
                    t.status = TournamentStatus.REGISTRATION.value
                t.pending_players = [
                    {"line": r.line, "discord_id": r.discord_id, "aniimo_uid": r.aniimo_uid,
                     "display_name": r.display_name, "fake": r.fake}
                    for r in preview.rows
                ] if preview.can_confirm else None
                await audit(s, t.id, None, actor_id, "players_preview", valid=n, issues=len(preview.issues))
            out.tournament(tournament_id, "preview", preview=preview)
        await out.flush(self.ctx.notifier)
        return preview

    async def confirm_players(self, tournament_id: int, actor_id: int) -> int:
        out = Outbox()
        async with self.ctx.locks.tournament(tournament_id), self.ctx.db.transaction() as s:
            t = await get_tournament(s, tournament_id)
            if t.status != TournamentStatus.REGISTRATION:
                raise UserError("The player list can't be changed now")
            rows = t.pending_players or []
            if len(rows) < 2:
                raise UserError("Paste a valid player list first")
            await s.execute(delete(Entry).where(Entry.tournament_id == t.id))
            for r in rows:
                p = await s.get(Player, r["discord_id"])
                if p is None:
                    p = Player(discord_id=r["discord_id"], aniimo_uid=r["aniimo_uid"],
                               display_name=r["display_name"], rating=elo.DEFAULT_RATING, wins=0, losses=0,
                               is_fake=bool(r.get("fake")))
                    s.add(p)
                else:
                    p.aniimo_uid, p.display_name, p.is_fake = r["aniimo_uid"], r["display_name"], bool(r.get("fake"))
            await s.flush()  # players must exist before entries reference them
            for r in rows:
                s.add(Entry(tournament_id=t.id, discord_id=r["discord_id"], display_name=r["display_name"],
                            aniimo_uid=r["aniimo_uid"], status=EntryStatus.ACTIVE.value))
            t.pending_players = None
            await audit(s, t.id, None, actor_id, "players_confirm", count=len(rows))
            out.tournament(t.id, "players_confirmed", count=len(rows))
        await out.flush(self.ctx.notifier)
        return len(rows)

    # ----- start + rounds -----
    async def start(self, tournament_id: int, actor_id: int, seed: str | None = None) -> Tournament:
        out = Outbox()
        async with self.ctx.locks.tournament(tournament_id):
            async with self.ctx.db.transaction() as s:
                t = await get_tournament(s, tournament_id)
                if t.status != TournamentStatus.REGISTRATION:
                    raise UserError("Confirm the player list before starting")
                entries = list(await s.scalars(select(Entry).where(Entry.tournament_id == t.id).order_by(Entry.id)))
                if len(entries) < 2:
                    raise UserError("At least 2 confirmed players are needed")
                if len(entries) > t.player_cap:
                    raise UserError(f"{len(entries)} players is more than the cap of {t.player_cap}")
                t.rng_seed = seed or secrets.token_hex(4).upper()
                plan = build_bracket([e.discord_id for e in entries], t.rng_seed)
                t.bracket_size = plan.size
                for e in entries:
                    e.seed = plan.seeds[e.discord_id]
                rows: list[list[Match]] = []
                for r_idx, planned_round in enumerate(plan.rounds, start=1):
                    bo = formats.bo_for_round(t.format, plan.size, r_idx)
                    round_rows = []
                    for pm in planned_round:
                        m = Match(tournament_id=t.id, round=r_idx, slot=pm.slot, bo=bo, p1_id=pm.p1, p2_id=pm.p2,
                                  state=MatchState.PENDING.value, is_bye=pm.is_bye, next_slot=pm.next_position)
                        if pm.is_bye:
                            m.state = MatchState.DONE.value
                            m.winner_id = pm.winner
                            m.walkover = True
                            m.advanced = True
                            m.finished_at = self.ctx.clock.now()
                        s.add(m)
                        round_rows.append(m)
                    rows.append(round_rows)
                await s.flush()
                for r_idx, round_rows in enumerate(rows[:-1]):
                    for m in round_rows:
                        m.next_match_id = rows[r_idx + 1][m.slot // 2].id
                t.status = TournamentStatus.RUNNING.value
                t.current_round = 1
                t.started_at = self.ctx.clock.now()
                await audit(s, t.id, None, actor_id, "tournament_start", seed=t.rng_seed, size=plan.size)
                out.tournament(t.id, "started", seed=t.rng_seed)
            await out.flush(self.ctx.notifier)
            await self._open_round(tournament_id, 1)
        return t

    async def _open_round(self, tournament_id: int, round_no: int) -> list[int]:
        """Open every playable match of a round (caller holds the tournament lock)."""
        out = Outbox()
        opened: list[int] = []
        tm = self.ctx.settings.timers
        async with self.ctx.db.transaction() as s:
            t = await get_tournament(s, tournament_id)
            matches = list(await s.scalars(
                select(Match).where(Match.tournament_id == tournament_id, Match.round == round_no).order_by(Match.slot)
            ))
            for m in matches:
                if m.state != MatchState.PENDING or m.p1_id is None or m.p2_id is None:
                    continue
                m.state = MatchState.WAITING.value
                m.opened_at = self.ctx.clock.now()
                await self.ctx.scheduler.schedule(s, JOB_NOSHOW_REMINDER, m.id, tm.noshow_reminder_minutes * 60)
                await self.ctx.scheduler.schedule(s, JOB_NOSHOW_ESCALATE, m.id, tm.noshow_escalate_minutes * 60)
                opened.append(m.id)
            t.current_round = round_no
            out.tournament(tournament_id, "round_opened", round=round_no, match_ids=opened)
            for mid in opened:
                out.match(mid, "opened")
        async with self.ctx.db.transaction() as s:
            # every match of the round may already be decided (forfeits): finish the round right away
            await self._check_round_complete(s, tournament_id, out)
        await out.flush(self.ctx.notifier)
        return opened

    async def start_next_round(self, tournament_id: int, actor_id: int) -> int:
        async with self.ctx.locks.tournament(tournament_id):
            async with self.ctx.db.transaction() as s:
                t = await get_tournament(s, tournament_id)
                if t.status != TournamentStatus.RUNNING:
                    raise UserError("The tournament isn't running")
                pending = await s.scalar(select(func.count()).select_from(Match).where(
                    Match.tournament_id == t.id, Match.round == t.current_round, Match.state != MatchState.DONE))
                if pending:
                    raise UserError(f"Round {t.current_round} still has {pending} unfinished match(es)")
                if t.current_round >= formats.round_count(t.bracket_size or 2):
                    raise UserError("There is no next round")
                nxt = t.current_round + 1
                await audit(s, t.id, None, actor_id, "round_start", round=nxt)
            await self._open_round(tournament_id, nxt)
            return nxt

    # ----- advancement (called after a match is DONE, holding that match's lock) -----
    async def after_match_done(self, match_id: int) -> None:
        async with self.ctx.db.session() as s:
            m = await s.get(Match, match_id)
            if m is None:
                return
            tid = m.tournament_id
        out = Outbox()
        async with self.ctx.locks.tournament(tid), self.ctx.db.transaction() as s:
            await self._advance(s, match_id, out)
            await self._check_round_complete(s, tid, out)
        await out.flush(self.ctx.notifier)

    async def _advance(self, s, match_id: int, out: Outbox) -> None:
        m = await s.get(Match, match_id)
        if m is None or m.state != MatchState.DONE or m.advanced:
            return
        m.advanced = True
        t = await get_tournament(s, m.tournament_id)
        loser = None
        if m.winner_id is not None:
            loser = m.p2_id if m.winner_id == m.p1_id else m.p1_id
        if loser is not None:
            e = await entry_for(s, t.id, loser)
            if e and e.status == EntryStatus.ACTIVE:
                e.status = EntryStatus.ELIMINATED.value
        await self._apply_elo(s, m)
        if m.next_match_id is None:
            return
        nxt = await s.get(Match, m.next_match_id)
        if m.winner_id is None:
            nxt.forfeit_slot = m.next_slot
        elif m.next_slot == 1:
            nxt.p1_id = m.winner_id
        else:
            nxt.p2_id = m.winner_id
        # the other side was forfeited: the arriving player (if any) walks through
        if nxt.forfeit_slot is not None and nxt.state == MatchState.PENDING:
            other_slot = 2 if nxt.forfeit_slot == 1 else 1
            other_feeder_done = await self._feeder_done(s, nxt, other_slot)
            arriving = nxt.p1_id if other_slot == 1 else nxt.p2_id
            if other_feeder_done:
                nxt.state = MatchState.DONE.value
                nxt.walkover = True
                nxt.winner_id = arriving
                nxt.finished_at = self.ctx.clock.now()
                out.match(nxt.id, "done", winner_id=arriving, walkover=True, reason="forfeit")
                await s.flush()
                await self._advance(s, nxt.id, out)

    async def _feeder_done(self, s, match: Match, slot: int) -> bool:
        feeder = await s.scalar(select(Match).where(Match.next_match_id == match.id, Match.next_slot == slot))
        return feeder is None or feeder.state == MatchState.DONE

    async def _apply_elo(self, s, m: Match) -> None:
        if m.walkover or m.is_bye or m.winner_id is None or m.p1_id is None or m.p2_id is None:
            return
        played = await s.scalar(select(func.count()).select_from(Game).where(
            Game.match_id == m.id, Game.status == GameStatus.CONFIRMED))
        if not played:
            return
        winner = await s.get(Player, m.winner_id)
        loser = await s.get(Player, m.p2_id if m.winner_id == m.p1_id else m.p1_id)
        if winner is None or loser is None:
            return
        dw, dl = elo.update(winner.rating, loser.rating)
        winner.rating += dw
        loser.rating += dl
        winner.wins += 1
        loser.losses += 1
        if m.winner_id == m.p1_id:
            m.p1_rating_delta, m.p2_rating_delta = dw, dl
        else:
            m.p1_rating_delta, m.p2_rating_delta = dl, dw

    async def _check_round_complete(self, s, tournament_id: int, out: Outbox) -> None:
        t = await get_tournament(s, tournament_id)
        if t.status != TournamentStatus.RUNNING or t.completed_round >= t.current_round:
            return
        pending = await s.scalar(select(func.count()).select_from(Match).where(
            Match.tournament_id == t.id, Match.round == t.current_round, Match.state != MatchState.DONE))
        if pending:
            return
        t.completed_round = t.current_round
        last_round = formats.round_count(t.bracket_size or 2)
        if t.current_round >= last_round:
            final = await s.scalar(select(Match).where(Match.tournament_id == t.id, Match.round == last_round))
            t.status = TournamentStatus.FINISHED.value
            t.finished_at = self.ctx.clock.now()
            t.champion_id = final.winner_id if final else None
            if t.champion_id:
                e = await entry_for(s, t.id, t.champion_id)
                if e:
                    e.status = EntryStatus.CHAMPION.value
            out.tournament(t.id, "finished", champion_id=t.champion_id)
        else:
            out.tournament(t.id, "round_complete", round=t.current_round, next_round=t.current_round + 1)

    async def cancel(self, tournament_id: int, actor_id: int) -> None:
        out = Outbox()
        async with self.ctx.locks.tournament(tournament_id), self.ctx.db.transaction() as s:
            t = await get_tournament(s, tournament_id)
            if t.status in (TournamentStatus.FINISHED, TournamentStatus.CANCELLED):
                raise UserError("That tournament is already over")
            t.status = TournamentStatus.CANCELLED.value
            t.finished_at = self.ctx.clock.now()
            matches = list(await s.scalars(select(Match).where(Match.tournament_id == t.id)))
            for m in matches:
                await self.ctx.scheduler.cancel(s, MATCH_JOBS, m.id)
                game_ids = await s.scalars(select(Game.id).where(Game.match_id == m.id))
                for gid in game_ids:
                    await self.ctx.scheduler.cancel(s, GAME_JOBS, gid)
            await audit(s, t.id, None, actor_id, "tournament_cancel")
            out.tournament(t.id, "cancelled")
        await out.flush(self.ctx.notifier)
