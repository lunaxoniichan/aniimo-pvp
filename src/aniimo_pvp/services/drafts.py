"""Draft turns: begin, submit, timeout (auto action), undo, options for the picker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete

from ..db.models import DraftActionRow, Game, Match
from ..domain.draft import DraftState, IllegalAction
from ..domain.roster import Aniimo
from ..domain.states import GameStatus, MatchState
from .common import (
    JOB_DRAFT_STEP,
    audit,
    current_game,
    draft_rows,
    fearless_locked,
    get_match,
    to_state,
    transition,
)
from .errors import NotAllowed, UserError
from .notifier import Outbox

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from .context import AppContext

S = MatchState


@dataclass
class TurnOptions:
    match_id: int
    game_id: int
    step: int
    side: str
    kind: str
    actor_id: int
    available: list[Aniimo]
    locked: set[str]
    state: DraftState
    deadline: datetime | None = None


class DraftService:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx

    def _player_for(self, game: Game, side: str) -> int | None:
        return game.side_a_id if side == "A" else game.side_b_id

    async def begin(self, s: AsyncSession, m: Match, game: Game, out: Outbox) -> None:
        """Start (or restart) the timer for the current step and announce the turn."""
        rows = await draft_rows(s, game.id)
        state = to_state(self.ctx.order, rows)
        await self.ctx.scheduler.cancel(s, JOB_DRAFT_STEP, game.id)
        if state.complete:
            game.status = GameStatus.PLAYING.value
            game.step_deadline = None
            transition(m, S.PLAYING)
            out.match(m.id, "draft_complete", game_id=game.id)
            return
        secs = self.ctx.settings.timers.draft_step_seconds
        game.step_deadline = self.ctx.clock.now() + timedelta(seconds=secs)
        await self.ctx.scheduler.schedule(s, JOB_DRAFT_STEP, game.id, secs, {"step": state.step})
        side, kind = state.current
        out.match(
            m.id, "turn", game_id=game.id, step=state.step, side=side, kind=kind, player_id=self._player_for(game, side)
        )

    async def options(self, match_id: int, game_id: int, step: int, actor_id: int) -> TurnOptions:
        """Legal choices for the actor's turn; raises if it's not their turn."""
        async with self.ctx.db.session() as s:
            m = await get_match(s, match_id)
            if m.state == S.PAUSED:
                raise UserError("The draft is paused by an admin")
            if m.state != S.DRAFTING:
                raise UserError("The draft isn't running")
            game = await current_game(s, m.id)
            if game is None or game.id != game_id:
                raise UserError("This draft is no longer active")
            state = to_state(self.ctx.order, await draft_rows(s, game.id))
            if state.step != step:
                raise UserError("This turn is over")
            side, kind = state.current
            player = self._player_for(game, side)
            if actor_id != player:
                raise NotAllowed("It's not your turn")
            locked = await fearless_locked(s, m, game, player)
            slugs = set(state.available(self.ctx.roster.slugs, locked))
            available = [a for a in self.ctx.roster.items if a.slug in slugs]
            return TurnOptions(m.id, game.id, step, side, kind, player, available, locked, state, game.step_deadline)

    async def submit(self, match_id: int, game_id: int, step: int, actor_id: int, slug: str) -> None:
        async def fn(s: AsyncSession, m: Match, out: Outbox):
            if m.state == S.PAUSED:
                raise UserError("The draft is paused by an admin")
            if m.state != S.DRAFTING:
                raise UserError("The draft isn't running")
            game = await current_game(s, m.id)
            if game is None or game.id != game_id:
                raise UserError("This draft is no longer active")
            rows = await draft_rows(s, game.id)
            state = to_state(self.ctx.order, rows)
            if state.step != step:
                raise UserError("This turn is over")
            side, kind = state.current
            player = self._player_for(game, side)
            if actor_id != player:
                raise NotAllowed("It's not your turn")
            locked = await fearless_locked(s, m, game, player)
            try:
                action = state.apply(slug, self.ctx.roster.slugs, locked)
            except IllegalAction as e:
                raise UserError(str(e)) from e
            await self._record(s, m, game, state, action, player, out)

        await self.ctx.matches._run(match_id, fn)

    async def _record(
        self, s: AsyncSession, m: Match, game: Game, state: DraftState, action, player_id: int | None, out: Outbox
    ) -> None:
        s.add(
            DraftActionRow(
                game_id=game.id,
                step=action.step,
                side=action.side,
                player_id=player_id,
                kind=action.kind,
                aniimo_slug=action.slug,
                auto=action.auto,
                created_at=self.ctx.clock.now(),
            )
        )
        await s.flush()
        out.match(
            m.id,
            "action",
            game_id=game.id,
            step=action.step,
            side=action.side,
            kind=action.kind,
            slug=action.slug,
            auto=action.auto,
            player_id=player_id,
        )
        await self.begin(s, m, game, out)

    async def job_step_timeout(self, job) -> None:
        mid = await self.ctx.matches.match_for_game(job.ref_id)
        if mid is None:
            return
        step = (job.payload or {}).get("step")

        async def fn(s: AsyncSession, m: Match, out: Outbox):
            if m.state != S.DRAFTING:
                return
            game = await current_game(s, m.id)
            if game is None or game.id != job.ref_id:
                return
            state = to_state(self.ctx.order, await draft_rows(s, game.id))
            if state.step != step or state.complete:
                return
            side, _ = state.current
            player = self._player_for(game, side)
            locked = await fearless_locked(s, m, game, player)
            action = state.timeout_action(self.ctx.rng, self.ctx.roster.slugs, locked)
            await self._record(s, m, game, state, action, player, out)

        try:
            await self.ctx.matches._run(mid, fn)
        except UserError:
            pass

    async def admin_undo(self, match_id: int, actor_id: int) -> None:
        async def fn(s: AsyncSession, m: Match, out: Outbox):
            if m.state not in (S.DRAFTING, S.PAUSED, S.PLAYING):
                raise UserError("Undo works during a draft (or right after it, before a result is reported)")
            game = await current_game(s, m.id)
            rows = await draft_rows(s, game.id)
            if not rows:
                raise UserError("Nothing to undo yet")
            if m.state == S.PLAYING and game.draft_source_id is not None:
                raise UserError("This replay reuses an earlier draft; use /admin replay new_draft instead")
            last = rows[-1]
            await s.execute(delete(DraftActionRow).where(DraftActionRow.id == last.id))
            await audit(
                s, m.tournament_id, m.id, actor_id, "admin_undo", game_id=game.id, step=last.step, slug=last.aniimo_slug
            )
            out.match(m.id, "undo", game_id=game.id, step=last.step, slug=last.aniimo_slug, by=actor_id)
            if m.state == S.PLAYING:
                game.status = GameStatus.DRAFTING.value
                transition(m, S.DRAFTING)
            if m.state == S.DRAFTING:
                await self.begin(s, m, game, out)
            else:  # paused: stay paused, just refresh the board
                out.match(m.id, "paused", by=actor_id, game_id=game.id)

        await self.ctx.matches._run(match_id, fn)
