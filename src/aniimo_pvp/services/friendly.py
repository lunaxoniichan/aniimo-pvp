"""Friendly duels: a 1v1 between friends that is only about the draft.

A duel is stored as a one-match tournament of kind ``friendly`` so it reuses the whole match room (ready check,
coin flip, draft, board, timers, restart safety). Differences from a tournament match:

* it starts with a challenge that the friend accepts or declines (or that expires);
* there is no result: after each draft the players start a **new draft** (the other player picks the side) or
  **end the duel**;
* no ratings, no stats, no bracket and no announcements;
* an abandoned duel closes itself (no-show, or no new draft for a while).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, or_, select

from ..db.models import Game, Match, Player, Tournament
from ..domain.players_csv import _UID_RE  # same rule as the tournament CSV
from ..domain.states import GameStatus, MatchState, TournamentKind, TournamentStatus, VoidReason
from .common import (
    JOB_CHALLENGE_EXPIRE,
    JOB_FRIENDLY_IDLE,
    JOB_SIDE_CHOICE,
    audit,
    current_game,
    games_of,
    get_tournament,
    is_friendly,
    opponent,
    require_player,
    require_state,
    transition,
)
from .errors import NotAllowed, UserError
from .notifier import Outbox

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from .context import AppContext

S = MatchState
OPEN_DUEL = (TournamentStatus.REGISTRATION.value, TournamentStatus.RUNNING.value)


class FriendlyService:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx

    # ----- lookups -----
    async def get(self, tournament_id: int) -> tuple[Tournament, Match]:
        async with self.ctx.db.session() as s:
            return await self._load(s, tournament_id)

    async def _load(self, s: AsyncSession, tournament_id: int) -> tuple[Tournament, Match]:
        t = await get_tournament(s, tournament_id)
        if t.kind != TournamentKind.FRIENDLY:
            raise UserError("That isn't a friendly duel")
        m = await s.scalar(select(Match).where(Match.tournament_id == t.id))
        return t, m

    async def open_duel_of(self, player_id: int) -> Match | None:
        async with self.ctx.db.session() as s:
            return await self._open_duel_of(s, player_id)

    async def _open_duel_of(self, s: AsyncSession, player_id: int) -> Match | None:
        return await s.scalar(
            select(Match)
            .join(Tournament, Tournament.id == Match.tournament_id)
            .where(
                Tournament.kind == TournamentKind.FRIENDLY,
                Tournament.status.in_(OPEN_DUEL),
                or_(Match.p1_id == player_id, Match.p2_id == player_id),
            )
            .limit(1)
        )

    async def drafts_played(self, player_id: int) -> int:
        async with self.ctx.db.session() as s:
            n = await s.scalar(
                select(func.count())
                .select_from(Game)
                .join(Match, Match.id == Game.match_id)
                .join(Tournament, Tournament.id == Match.tournament_id)
                .where(
                    Tournament.kind == TournamentKind.FRIENDLY,
                    Game.status == GameStatus.FINISHED,
                    or_(Match.p1_id == player_id, Match.p2_id == player_id),
                )
            )
            return int(n or 0)

    # ----- Aniimo UID for the room card -----
    async def register_uid(self, discord_id: int, display_name: str, uid: str) -> None:
        uid = uid.strip()
        if not _UID_RE.match(uid):
            raise UserError("An Aniimo UID is letters and numbers only (up to 32), for example 100234567")
        async with self.ctx.db.transaction() as s:
            p = await s.get(Player, discord_id)
            if p is None:
                s.add(
                    Player(
                        discord_id=discord_id,
                        aniimo_uid=uid,
                        display_name=display_name[:64],
                        rating=1000,
                        wins=0,
                        losses=0,
                        is_fake=False,
                    )
                )
            else:
                p.aniimo_uid = uid

    async def _ensure_player(self, s: AsyncSession, discord_id: int, name: str, fake: bool) -> Player:
        p = await s.get(Player, discord_id)
        if p is None:
            p = Player(
                discord_id=discord_id,
                aniimo_uid="",
                display_name=name[:64],
                rating=1000,
                wins=0,
                losses=0,
                is_fake=fake,
            )
            s.add(p)
            await s.flush()
        return p

    # ----- challenge -----
    async def challenge(
        self,
        guild_id: int,
        channel_id: int,
        challenger: tuple[int, str],
        opponent_: tuple[int, str],
        *,
        fearless: bool = False,
        opponent_is_fake: bool = False,
    ) -> tuple[Tournament, Match]:
        (cid, cname), (oid, oname) = challenger, opponent_
        if cid == oid:
            raise UserError("You can't duel yourself. Pick a friend!")
        out = Outbox()
        async with self.ctx.db.transaction() as s:
            if await self._open_duel_of(s, cid):
                raise UserError("You already have an open duel. End it (or cancel the challenge) first.")
            if await self._open_duel_of(s, oid):
                raise UserError(f"{oname} already has an open duel. Try again when it's over.")
            await self._ensure_player(s, cid, cname, False)
            await self._ensure_player(s, oid, oname, opponent_is_fake)
            now = self.ctx.clock.now()
            t = Tournament(
                guild_id=guild_id,
                kind=TournamentKind.FRIENDLY.value,
                name=f"Friendly · {cname} vs {oname}"[:100],
                status=TournamentStatus.REGISTRATION.value,
                player_cap=2,
                format="quick",
                fearless=fearless,
                bracket_size=2,
                created_by=cid,
                created_at=now,
                setup_channel_id=channel_id,
            )
            s.add(t)
            await s.flush()
            m = Match(tournament_id=t.id, round=1, slot=0, bo=1, p1_id=cid, p2_id=oid, state=S.PENDING.value)
            s.add(m)
            await s.flush()
            await self.ctx.scheduler.schedule(
                s, JOB_CHALLENGE_EXPIRE, t.id, self.ctx.settings.timers.challenge_minutes * 60
            )
            await audit(s, t.id, m.id, cid, "duel_challenge", opponent=oid, fearless=fearless)
            out.tournament(t.id, "duel_challenged", match_id=m.id)
        await out.flush(self.ctx.notifier)
        return t, m

    async def set_card(self, tournament_id: int, channel_id: int, message_id: int) -> None:
        async with self.ctx.db.transaction() as s:
            t = await get_tournament(s, tournament_id)
            t.setup_channel_id, t.setup_message_id = channel_id, message_id

    async def accept(self, tournament_id: int, actor_id: int) -> Match:
        out = Outbox()
        async with self.ctx.locks.tournament(tournament_id):
            async with self.ctx.db.transaction() as s:
                t, m = await self._load(s, tournament_id)
                if t.status != TournamentStatus.REGISTRATION:
                    raise UserError("This challenge is no longer open")
                if actor_id != m.p2_id:
                    raise NotAllowed("Only the challenged player can accept")
                t.status = TournamentStatus.RUNNING.value
                t.started_at = self.ctx.clock.now()
                await self.ctx.scheduler.cancel(s, JOB_CHALLENGE_EXPIRE, t.id)
                await audit(s, t.id, m.id, actor_id, "duel_accept")
            await self.ctx.tournaments._open_round(tournament_id, 1)  # opens the thread + ready check
            out.tournament(tournament_id, "duel_accepted", match_id=m.id)
            await out.flush(self.ctx.notifier)
        return m

    async def _close_challenge(
        self, tournament_id: int, actor_id: int | None, event: str, *, who: str, allow_admin: bool = False
    ) -> None:
        out = Outbox()
        async with self.ctx.locks.tournament(tournament_id), self.ctx.db.transaction() as s:
            t, m = await self._load(s, tournament_id)
            if t.status != TournamentStatus.REGISTRATION:
                raise UserError("This challenge is no longer open")
            if who == "opponent" and actor_id != m.p2_id:
                raise NotAllowed("Only the challenged player can decline")
            if who == "challenger" and actor_id != m.p1_id and not allow_admin:
                raise NotAllowed("Only the player who sent the challenge can cancel it")
            t.status = TournamentStatus.CANCELLED.value
            t.finished_at = self.ctx.clock.now()
            m.state = S.DONE.value
            await self.ctx.scheduler.cancel(s, JOB_CHALLENGE_EXPIRE, t.id)
            await audit(s, t.id, m.id, actor_id, f"duel_{event}")
            out.tournament(t.id, f"duel_{event}", match_id=m.id, by=actor_id)
        await out.flush(self.ctx.notifier)

    async def decline(self, tournament_id: int, actor_id: int) -> None:
        await self._close_challenge(tournament_id, actor_id, "declined", who="opponent")

    async def cancel_challenge(self, tournament_id: int, actor_id: int, *, is_admin: bool = False) -> None:
        await self._close_challenge(tournament_id, actor_id, "cancelled", who="challenger", allow_admin=is_admin)

    async def job_challenge_expire(self, job) -> None:
        try:
            await self._close_challenge(job.ref_id, None, "expired", who="system")
        except UserError:
            pass  # accepted, declined or cancelled meanwhile

    # ----- drafts -----
    async def new_draft(self, match_id: int, actor_id: int) -> int:
        """After a finished draft: start the next one. The players take turns choosing the side."""

        async def fn(s: AsyncSession, m: Match, out: Outbox) -> int:
            if not await is_friendly(s, m):
                raise UserError("New draft is for friendly duels")
            require_state(m, S.PLAYING, message="Finish the current draft first")
            require_player(m, actor_id)
            game = await current_game(s, m.id)
            game.status = GameStatus.FINISHED.value
            chooser = opponent(m, m.side_chooser_id) or m.p1_id  # take turns
            new = Game(
                match_id=m.id,
                number=game.number + 1,
                attempt=1,
                status=GameStatus.SIDE_CHOICE.value,
                created_at=self.ctx.clock.now(),
            )
            s.add(new)
            await s.flush()
            m.side_chooser_id, m.coin_winner_id = chooser, None
            transition(m, S.BETWEEN_GAMES)
            transition(m, S.SIDE_CHOICE)
            await self.ctx.scheduler.cancel(s, JOB_FRIENDLY_IDLE, m.id)
            await self.ctx.scheduler.schedule(s, JOB_SIDE_CHOICE, new.id, self.ctx.settings.timers.side_choice_seconds)
            out.match(m.id, "side_choice", game_id=new.id, chooser_id=chooser, coin=False, number=new.number)
            return new.id

        return await self.ctx.matches._run(match_id, fn)

    # ----- ending -----
    async def end(self, match_id: int, actor_id: int, *, is_admin: bool = False) -> int:
        async def fn(s: AsyncSession, m: Match, out: Outbox) -> int:
            if not await is_friendly(s, m):
                raise UserError("End duel is for friendly duels")
            if m.state == S.DONE:
                raise UserError("This duel is already over")
            if actor_id not in (m.p1_id, m.p2_id) and not is_admin:
                raise NotAllowed("Only the two players can end this duel")
            return await self.end_in_tx(s, m, out, actor_id, "ended")

        return await self.ctx.matches._run(match_id, fn)

    async def end_in_tx(self, s: AsyncSession, m: Match, out: Outbox, by: int | None, reason: str) -> int:
        """End the duel inside the caller's transaction. Returns how many drafts were played."""
        drafts = 0
        for g in await games_of(s, m.id):
            if g.status == GameStatus.PLAYING:  # the draft was finished: it counts
                g.status = GameStatus.FINISHED.value
            elif g.status in (GameStatus.SIDE_CHOICE, GameStatus.DRAFTING):
                g.status, g.void_reason = GameStatus.VOIDED.value, VoidReason.DUEL_ENDED.value
            if g.status == GameStatus.FINISHED:
                drafts += 1
        await self.ctx.matches._finish(s, m, None, out, walkover=False, reason=f"duel_{reason}", advance=False)
        t = await s.get(Tournament, m.tournament_id)
        t.status = (TournamentStatus.FINISHED if drafts else TournamentStatus.CANCELLED).value
        t.finished_at = self.ctx.clock.now()
        await audit(s, t.id, m.id, by, "duel_end", reason=reason, drafts=drafts)
        out.tournament(t.id, "duel_ended", match_id=m.id, drafts=drafts, by=by, reason=reason)
        return drafts

    async def job_idle(self, job) -> None:
        async def fn(s: AsyncSession, m: Match, out: Outbox) -> None:
            if m.state == S.PLAYING and await is_friendly(s, m):
                await self.end_in_tx(s, m, out, None, "idle")

        await self.ctx.matches._safe_job(job.ref_id, fn)
