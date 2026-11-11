"""Match lifecycle: ready check, sides, results, next game, rematch, admin overrides."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from ..db.models import DraftActionRow, Entry, Game, Match, Player, Tournament
from ..domain.series import can_next_game, next_side_chooser, score, series_winner
from ..domain.states import EntryStatus, GameStatus, MatchState, TournamentStatus, VoidReason
from .common import (
    GAME_JOBS,
    JOB_ARCHIVE,
    JOB_AUTO_CONFIRM,
    JOB_DRAFT_STEP,
    JOB_NOSHOW_ESCALATE,
    JOB_NOSHOW_REMINDER,
    JOB_REMATCH_OFFER,
    JOB_SIDE_CHOICE,
    MATCH_JOBS,
    audit,
    current_game,
    draft_rows,
    entry_for,
    games_of,
    get_match,
    get_tournament,
    opponent,
    require_player,
    require_state,
    results,
    transition,
)
from .errors import NotAllowed, UserError
from .notifier import Outbox

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from .context import AppContext

S = MatchState
REMATCH_MODES = ("same_teams", "new_draft")


class MatchService:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx

    # ----- plumbing -----
    async def _run(self, match_id: int, fn, *args, **kwargs):
        """Run fn(session, match, outbox, ...) under the match lock in one transaction, then notify."""
        out = Outbox()
        async with self.ctx.locks.match(match_id):
            async with self.ctx.db.transaction() as s:
                m = await get_match(s, match_id)
                t = await get_tournament(s, m.tournament_id)
                if t.status != TournamentStatus.RUNNING:
                    raise UserError("The tournament isn't running")
                result = await fn(s, m, out, *args, **kwargs)
            await out.flush(self.ctx.notifier)
            for mid in out.finished_matches:
                await self.ctx.tournaments.after_match_done(mid)
        return result

    async def match_for_game(self, game_id: int) -> int | None:
        async with self.ctx.db.session() as s:
            g = await s.get(Game, game_id)
            return g.match_id if g else None

    async def find_by_thread(self, thread_id: int) -> Match | None:
        async with self.ctx.db.session() as s:
            return await s.scalar(select(Match).where(Match.thread_id == thread_id))

    async def get(self, match_id: int) -> Match:
        async with self.ctx.db.session() as s:
            return await get_match(s, match_id)

    async def set_message_ids(self, match_id: int, **ids: int | None) -> None:
        async with self.ctx.db.transaction() as s:
            m = await get_match(s, match_id)
            for k, v in ids.items():
                setattr(m, k, v)

    # ----- ready check -----
    async def ready(self, match_id: int, actor_id: int) -> bool:
        return await self._run(match_id, self._ready, actor_id)

    async def _ready(self, s: AsyncSession, m: Match, out: Outbox, actor_id: int) -> bool:
        require_state(m, S.WAITING, S.NOSHOW, message="The ready check is over")
        require_player(m, actor_id)
        if actor_id == m.p1_id:
            m.p1_ready = True
        else:
            m.p2_ready = True
        out.match(m.id, "ready", player_id=actor_id)
        if not (m.p1_ready and m.p2_ready):
            return False
        await self.ctx.scheduler.cancel(s, (JOB_NOSHOW_REMINDER, JOB_NOSHOW_ESCALATE), m.id)
        if m.state == S.NOSHOW:
            transition(m, S.WAITING)
        game = Game(
            match_id=m.id, number=1, attempt=1, status=GameStatus.SIDE_CHOICE.value, created_at=self.ctx.clock.now()
        )
        s.add(game)
        await s.flush()
        coin = self.ctx.rng.choice([m.p1_id, m.p2_id])
        m.coin_winner_id = coin
        m.side_chooser_id = coin
        transition(m, S.SIDE_CHOICE)
        await self.ctx.scheduler.schedule(s, JOB_SIDE_CHOICE, game.id, self.ctx.settings.timers.side_choice_seconds)
        out.match(m.id, "side_choice", game_id=game.id, chooser_id=coin, coin=True)
        return True

    async def job_noshow_reminder(self, job) -> None:
        async def fn(s, m: Match, out: Outbox):
            if m.state == S.WAITING and not (m.p1_ready and m.p2_ready):
                missing = [p for p, r in ((m.p1_id, m.p1_ready), (m.p2_id, m.p2_ready)) if not r]
                out.match(m.id, "noshow_reminder", missing=missing)

        await self._safe_job(job.ref_id, fn)

    async def job_noshow_escalate(self, job) -> None:
        async def fn(s, m: Match, out: Outbox):
            if m.state == S.WAITING and not (m.p1_ready and m.p2_ready):
                transition(m, S.NOSHOW)
                missing = [p for p, r in ((m.p1_id, m.p1_ready), (m.p2_id, m.p2_ready)) if not r]
                out.match(m.id, "noshow", missing=missing)

        await self._safe_job(job.ref_id, fn)

    async def _safe_job(self, match_id: int, fn) -> None:
        try:
            await self._run(match_id, fn)
        except UserError:
            pass  # tournament finished/cancelled meanwhile

    # ----- sides -----
    async def choose_side(self, match_id: int, actor_id: int, side: str) -> None:
        await self._run(match_id, self._choose_side, actor_id, side, False)

    async def _choose_side(self, s: AsyncSession, m: Match, out: Outbox, actor_id: int, side: str, auto: bool):
        require_state(m, S.SIDE_CHOICE, message="Sides are already chosen")
        if actor_id != m.side_chooser_id:
            raise NotAllowed("The other player chooses the side")
        side = side.upper()
        if side not in ("A", "B"):
            raise UserError("Side must be A or B")
        game = await current_game(s, m.id)
        if game is None or game.status != GameStatus.SIDE_CHOICE:
            raise UserError("There's no game waiting for a side")
        other = opponent(m, actor_id)
        game.side_a_id, game.side_b_id = (actor_id, other) if side == "A" else (other, actor_id)
        game.status = GameStatus.DRAFTING.value
        await self.ctx.scheduler.cancel(s, JOB_SIDE_CHOICE, game.id)
        transition(m, S.DRAFTING)
        out.match(m.id, "side_chosen", game_id=game.id, chooser_id=actor_id, side=side, auto=auto)
        await self.ctx.drafts.begin(s, m, game, out)

    async def job_side_choice(self, job) -> None:
        mid = await self.match_for_game(job.ref_id)
        if mid is None:
            return

        async def fn(s, m: Match, out: Outbox):
            game = await current_game(s, m.id)
            if m.state == S.SIDE_CHOICE and game and game.id == job.ref_id and m.side_chooser_id:
                await self._choose_side(s, m, out, m.side_chooser_id, "A", True)

        await self._safe_job(mid, fn)

    # ----- results -----
    async def report(self, match_id: int, actor_id: int, winner_id: int) -> None:
        await self._run(match_id, self._report, actor_id, winner_id)

    async def _report(self, s: AsyncSession, m: Match, out: Outbox, actor_id: int, winner_id: int) -> None:
        require_state(m, S.PLAYING, message="There's no game in progress to report")
        require_player(m, actor_id)
        if winner_id not in (m.p1_id, m.p2_id):
            raise UserError("The winner must be one of the two players")
        game = await current_game(s, m.id)
        game.status = GameStatus.REPORTED.value
        game.reported_by = actor_id
        game.reported_winner_id = winner_id
        if m.rematch_by:
            m.rematch_by = m.rematch_mode = None
            await self.ctx.scheduler.cancel(s, JOB_REMATCH_OFFER, m.id)
        transition(m, S.CONFIRMING)
        tm = self.ctx.settings.timers
        await self.ctx.scheduler.schedule(s, JOB_AUTO_CONFIRM, game.id, tm.auto_confirm_minutes * 60)
        out.match(m.id, "reported", game_id=game.id, reporter_id=actor_id, winner_id=winner_id)

    async def attach_screenshot(self, match_id: int, actor_id: int, url: str) -> bool:
        async def fn(s, m: Match, out: Outbox) -> bool:
            if actor_id not in (m.p1_id, m.p2_id):
                return False
            game = await current_game(s, m.id)
            if game is None or game.status not in (GameStatus.PLAYING, GameStatus.REPORTED, GameStatus.DISPUTED):
                return False
            game.screenshot_url = url[:500]
            out.match(m.id, "screenshot", game_id=game.id, player_id=actor_id, url=url)
            return True

        try:
            return await self._run(match_id, fn)
        except UserError:
            return False

    async def confirm(self, match_id: int, actor_id: int) -> None:
        async def fn(s, m: Match, out: Outbox):
            require_state(m, S.CONFIRMING, message="There's no result waiting for confirmation")
            game = await current_game(s, m.id)
            if actor_id != opponent(m, game.reported_by):
                raise NotAllowed("Only the other player can confirm this result")
            await self._decide(s, m, game, game.reported_winner_id, out, "confirmed", actor_id)

        await self._run(match_id, fn)

    async def dispute(self, match_id: int, actor_id: int) -> None:
        async def fn(s, m: Match, out: Outbox):
            require_state(m, S.CONFIRMING, message="There's no result to dispute")
            game = await current_game(s, m.id)
            if actor_id != opponent(m, game.reported_by):
                raise NotAllowed("Only the other player can dispute this result")
            game.status = GameStatus.DISPUTED.value
            await self.ctx.scheduler.cancel(s, JOB_AUTO_CONFIRM, game.id)
            transition(m, S.DISPUTED)
            await audit(s, m.tournament_id, m.id, actor_id, "dispute", game_id=game.id)
            out.match(m.id, "disputed", game_id=game.id, by=actor_id)

        await self._run(match_id, fn)

    async def job_auto_confirm(self, job) -> None:
        mid = await self.match_for_game(job.ref_id)
        if mid is None:
            return

        async def fn(s, m: Match, out: Outbox):
            game = await current_game(s, m.id)
            if m.state == S.CONFIRMING and game and game.id == job.ref_id and game.status == GameStatus.REPORTED:
                await self._decide(s, m, game, game.reported_winner_id, out, "auto", None)

        await self._safe_job(mid, fn)

    async def _decide(
        self, s: AsyncSession, m: Match, game: Game, winner_id: int, out: Outbox, reason: str, actor_id: int | None
    ) -> None:
        game.status = GameStatus.CONFIRMED.value
        game.winner_id = winner_id
        game.confirmed_at = self.ctx.clock.now()
        await self.ctx.scheduler.cancel(s, (JOB_AUTO_CONFIRM, JOB_DRAFT_STEP, JOB_SIDE_CHOICE), game.id)
        if m.rematch_by:
            m.rematch_by = m.rematch_mode = None
            await self.ctx.scheduler.cancel(s, JOB_REMATCH_OFFER, m.id)
        await s.flush()
        games = await games_of(s, m.id)
        sc = score(results(games), m.p1_id, m.p2_id)
        out.match(
            m.id,
            "game_confirmed",
            game_id=game.id,
            winner_id=winner_id,
            reason=reason,
            score=(sc.p1, sc.p2),
            by=actor_id,
        )
        winner = series_winner(sc, m.bo, m.p1_id, m.p2_id)
        if winner is not None:
            await self._finish(s, m, winner, out, walkover=False)
        else:
            transition(m, S.BETWEEN_GAMES)
            out.match(
                m.id,
                "between_games",
                score=(sc.p1, sc.p2),
                chooser_id=next_side_chooser(results(games), m.p1_id, m.p2_id),
            )

    async def _finish(
        self, s: AsyncSession, m: Match, winner_id: int | None, out: Outbox, *, walkover: bool, reason: str = "won"
    ) -> None:
        if m.state == S.DONE:
            return
        transition(m, S.DONE)
        m.winner_id = winner_id
        m.walkover = walkover
        m.finished_at = self.ctx.clock.now()
        m.rematch_by = m.rematch_mode = None
        await self.ctx.scheduler.cancel(s, MATCH_JOBS, m.id)
        for g in await games_of(s, m.id):
            await self.ctx.scheduler.cancel(s, GAME_JOBS, g.id)
            g.step_deadline = None
        await self.ctx.scheduler.schedule(s, JOB_ARCHIVE, m.id, self.ctx.settings.timers.thread_archive_minutes * 60)
        out.match(m.id, "done", winner_id=winner_id, walkover=walkover, reason=reason)
        out.finished_matches.append(m.id)

    # ----- next game -----
    async def next_game(self, match_id: int, actor_id: int) -> int:
        async def fn(s, m: Match, out: Outbox) -> int:
            require_state(m, S.BETWEEN_GAMES, message="The next game can't start now")
            require_player(m, actor_id)
            games = await games_of(s, m.id)
            res = results(games)
            if not can_next_game(res, m.bo, m.p1_id, m.p2_id):
                raise UserError("This series doesn't have another game")
            number = max(g.number for g in games if g.status == GameStatus.CONFIRMED) + 1
            chooser = next_side_chooser(res, m.p1_id, m.p2_id)
            game = Game(
                match_id=m.id,
                number=number,
                attempt=1,
                status=GameStatus.SIDE_CHOICE.value,
                created_at=self.ctx.clock.now(),
            )
            s.add(game)
            await s.flush()
            m.side_chooser_id = chooser
            m.coin_winner_id = None
            transition(m, S.SIDE_CHOICE)
            await self.ctx.scheduler.schedule(s, JOB_SIDE_CHOICE, game.id, self.ctx.settings.timers.side_choice_seconds)
            out.match(m.id, "side_choice", game_id=game.id, chooser_id=chooser, coin=False, number=number)
            return game.id

        return await self._run(match_id, fn)

    # ----- rematch -----
    async def request_rematch(self, match_id: int, actor_id: int, mode: str) -> None:
        if mode not in REMATCH_MODES:
            raise UserError("Unknown rematch type")

        async def fn(s, m: Match, out: Outbox):
            require_state(m, S.PLAYING, message="A rematch can only be requested while the game is being played")
            require_player(m, actor_id)
            if m.rematch_by and m.rematch_by != actor_id:
                raise UserError("The other player already asked for a rematch. Answer that offer instead.")
            m.rematch_by, m.rematch_mode = actor_id, mode
            await self.ctx.scheduler.cancel(s, JOB_REMATCH_OFFER, m.id)
            secs = self.ctx.settings.timers.rematch_offer_seconds
            await self.ctx.scheduler.schedule(s, JOB_REMATCH_OFFER, m.id, secs)
            game = await current_game(s, m.id)
            out.match(m.id, "rematch_offered", by=actor_id, mode=mode, game_id=game.id, number=game.number)

        await self._run(match_id, fn)

    async def answer_rematch(self, match_id: int, actor_id: int, accept: bool) -> None:
        async def fn(s, m: Match, out: Outbox):
            require_state(m, S.PLAYING, message="That rematch offer is no longer open")
            if not m.rematch_by:
                raise UserError("That rematch offer is no longer open")
            if actor_id != opponent(m, m.rematch_by):
                raise NotAllowed("Only the other player can answer this rematch offer")
            mode, by = m.rematch_mode, m.rematch_by
            m.rematch_by = m.rematch_mode = None
            await self.ctx.scheduler.cancel(s, JOB_REMATCH_OFFER, m.id)
            if not accept:
                out.match(m.id, "rematch_declined", by=by, answered_by=actor_id)
                return
            reason = VoidReason.REMATCH_SAME_TEAMS if mode == "same_teams" else VoidReason.REMATCH_NEW_DRAFT
            await self._rematch(s, m, out, mode, reason, actor_id)

        await self._run(match_id, fn)

    async def job_rematch_offer(self, job) -> None:
        async def fn(s, m: Match, out: Outbox):
            if m.rematch_by:
                by = m.rematch_by
                m.rematch_by = m.rematch_mode = None
                out.match(m.id, "rematch_expired", by=by)

        await self._safe_job(job.ref_id, fn)

    async def _rematch(
        self, s: AsyncSession, m: Match, out: Outbox, mode: str, reason: VoidReason, actor_id: int | None
    ) -> int:
        old = await current_game(s, m.id)
        if old is None:
            raise UserError("There's no game to replay")
        old.status = GameStatus.VOIDED.value
        old.void_reason = reason.value
        old.step_deadline = None
        await self.ctx.scheduler.cancel(s, GAME_JOBS, old.id)
        await s.flush()
        new = Game(
            match_id=m.id,
            number=old.number,
            attempt=old.attempt + 1,
            side_a_id=old.side_a_id,
            side_b_id=old.side_b_id,
            created_at=self.ctx.clock.now(),
            status=(GameStatus.PLAYING if mode == "same_teams" else GameStatus.DRAFTING).value,
        )
        if mode == "same_teams":
            new.draft_source_id = old.draft_source_id or old.id
        s.add(new)
        await s.flush()
        await audit(
            s,
            m.tournament_id,
            m.id,
            actor_id,
            "rematch",
            mode=mode,
            reason=reason.value,
            voided_game=old.id,
            new_game=new.id,
        )
        out.match(
            m.id,
            "rematch_accepted",
            mode=mode,
            reason=reason.value,
            game_id=new.id,
            number=new.number,
            attempt=new.attempt,
            voided_game_id=old.id,
        )
        if mode == "same_teams":
            for r in await draft_rows(s, old.id):
                s.add(
                    DraftActionRow(
                        game_id=new.id,
                        step=r.step,
                        side=r.side,
                        player_id=r.player_id,
                        kind=r.kind,
                        aniimo_slug=r.aniimo_slug,
                        auto=r.auto,
                        created_at=self.ctx.clock.now(),
                    )
                )
            transition(m, S.PLAYING)
            out.match(m.id, "playing", game_id=new.id, replay=True)
        else:
            transition(m, S.DRAFTING)
            await self.ctx.drafts.begin(s, m, new, out)
        return new.id

    async def call_admin(self, match_id: int, actor_id: int, note: str | None = None) -> None:
        async def fn(s, m: Match, out: Outbox):
            if actor_id not in (m.p1_id, m.p2_id):
                raise NotAllowed("Only the players in this match can call an admin here")
            await audit(s, m.tournament_id, m.id, actor_id, "call_admin", note=note)
            out.match(m.id, "call_admin", by=actor_id, note=note)

        await self._run(match_id, fn)

    # ----- admin -----
    async def admin_extend(self, match_id: int, actor_id: int, minutes: float | None = None) -> None:
        minutes = minutes or self.ctx.settings.timers.noshow_extend_minutes

        async def fn(s, m: Match, out: Outbox):
            require_state(m, S.WAITING, S.NOSHOW, message="Only a ready check can be extended")
            if m.state == S.NOSHOW:
                transition(m, S.WAITING)
            await self.ctx.scheduler.cancel(s, JOB_NOSHOW_ESCALATE, m.id)
            await self.ctx.scheduler.schedule(s, JOB_NOSHOW_ESCALATE, m.id, minutes * 60)
            await audit(s, m.tournament_id, m.id, actor_id, "admin_extend", minutes=minutes)
            out.match(m.id, "extended", minutes=minutes, by=actor_id)

        await self._run(match_id, fn)

    async def admin_award(self, match_id: int, actor_id: int, winner_id: int, reason: str = "admin") -> None:
        async def fn(s, m: Match, out: Outbox):
            if m.state == S.DONE:
                raise UserError("That match is already finished")
            if winner_id not in (m.p1_id, m.p2_id) or winner_id is None:
                raise UserError("The winner must be one of the two players")
            await audit(s, m.tournament_id, m.id, actor_id, "admin_award", winner=winner_id, reason=reason)
            out.match(m.id, "admin_action", action="award", winner_id=winner_id, by=actor_id)
            await self._finish(s, m, winner_id, out, walkover=True, reason=reason)

        await self._run(match_id, fn)

    async def admin_dq_both(self, match_id: int, actor_id: int) -> None:
        async def fn(s, m: Match, out: Outbox):
            if m.state == S.DONE:
                raise UserError("That match is already finished")
            for pid in (m.p1_id, m.p2_id):
                e = await entry_for(s, m.tournament_id, pid) if pid else None
                if e:
                    e.status = EntryStatus.DQ.value
            await audit(s, m.tournament_id, m.id, actor_id, "admin_dq_both")
            out.match(m.id, "admin_action", action="dq_both", by=actor_id)
            await self._finish(s, m, None, out, walkover=True, reason="dq_both")

        await self._run(match_id, fn)

    async def admin_set_result(self, match_id: int, actor_id: int, winner_id: int) -> None:
        async def fn(s, m: Match, out: Outbox):
            require_state(
                m,
                S.PLAYING,
                S.CONFIRMING,
                S.DISPUTED,
                message="There's no game in progress. Use /admin award to decide the whole match.",
            )
            if winner_id not in (m.p1_id, m.p2_id):
                raise UserError("The winner must be one of the two players")
            game = await current_game(s, m.id)
            await audit(s, m.tournament_id, m.id, actor_id, "admin_set_result", game_id=game.id, winner=winner_id)
            out.match(m.id, "admin_action", action="set_result", winner_id=winner_id, by=actor_id, game_id=game.id)
            await self._decide(s, m, game, winner_id, out, "admin", actor_id)

        await self._run(match_id, fn)

    async def admin_replay(self, match_id: int, actor_id: int, mode: str) -> int:
        if mode not in REMATCH_MODES:
            raise UserError("Mode must be same_teams or new_draft")

        async def fn(s, m: Match, out: Outbox) -> int:
            require_state(m, S.PLAYING, S.CONFIRMING, S.DISPUTED, message="There's no game to replay right now")
            m.rematch_by = m.rematch_mode = None
            await self.ctx.scheduler.cancel(s, JOB_REMATCH_OFFER, m.id)
            out.match(m.id, "admin_action", action="replay", mode=mode, by=actor_id)
            return await self._rematch(s, m, out, mode, VoidReason.ADMIN_REPLAY, actor_id)

        return await self._run(match_id, fn)

    async def admin_pause(self, match_id: int, actor_id: int) -> None:
        async def fn(s, m: Match, out: Outbox):
            require_state(m, S.DRAFTING, message="Only a running draft can be paused")
            game = await current_game(s, m.id)
            await self.ctx.scheduler.cancel(s, JOB_DRAFT_STEP, game.id)
            game.step_deadline = None
            transition(m, S.PAUSED)
            await audit(s, m.tournament_id, m.id, actor_id, "admin_pause")
            out.match(m.id, "paused", by=actor_id, game_id=game.id)

        await self._run(match_id, fn)

    async def admin_resume(self, match_id: int, actor_id: int) -> None:
        async def fn(s, m: Match, out: Outbox):
            require_state(m, S.PAUSED, message="The draft isn't paused")
            game = await current_game(s, m.id)
            transition(m, S.DRAFTING)
            await audit(s, m.tournament_id, m.id, actor_id, "admin_resume")
            out.match(m.id, "resumed", by=actor_id, game_id=game.id)
            await self.ctx.drafts.begin(s, m, game, out)

        await self._run(match_id, fn)

    async def admin_dq_player(self, tournament_id: int, actor_id: int, player_id: int) -> int | None:
        """Disqualify a player: their open match goes to the opponent, or their slot is forfeited."""
        async with self.ctx.db.session() as s:
            e = await entry_for(s, tournament_id, player_id)
            if e is None:
                raise UserError("That player isn't in this tournament")
            if e.status != EntryStatus.ACTIVE:
                raise UserError(f"That player is already {e.status}")
            m = await s.scalar(
                select(Match)
                .where(
                    Match.tournament_id == tournament_id,
                    Match.state != S.DONE,
                    (Match.p1_id == player_id) | (Match.p2_id == player_id),
                )
                .order_by(Match.round)
                .limit(1)
            )
        if m is None:
            raise UserError("That player has no unfinished match")

        async def fn(s, m: Match, out: Outbox):
            e = await entry_for(s, m.tournament_id, player_id)
            e.status = EntryStatus.DQ.value
            await audit(s, m.tournament_id, m.id, actor_id, "admin_dq", player=player_id)
            other = opponent(m, player_id)
            out.match(m.id, "admin_action", action="dq", player_id=player_id, by=actor_id)
            if other is not None:
                await self._finish(s, m, other, out, walkover=True, reason="dq")
            else:
                slot = 1 if m.p1_id == player_id else 2
                if slot == 1:
                    m.p1_id = None
                else:
                    m.p2_id = None
                m.forfeit_slot = slot

        await self._run(m.id, fn)
        return m.id

    async def admin_swap(
        self,
        tournament_id: int,
        actor_id: int,
        old_id: int,
        new_id: int,
        aniimo_uid: str,
        display_name: str,
        is_fake: bool = False,
    ) -> list[int]:
        changed: list[int] = []
        out = Outbox()
        async with self.ctx.locks.tournament(tournament_id), self.ctx.db.transaction() as s:
            t = await s.get(Tournament, tournament_id)
            if t is None or t.status != TournamentStatus.RUNNING:
                raise UserError("Swaps are for running tournaments (before the start, paste a new CSV)")
            e = await entry_for(s, tournament_id, old_id)
            if e is None:
                raise UserError("The old player isn't in this tournament")
            if await entry_for(s, tournament_id, new_id):
                raise UserError("The new player is already in this tournament")
            uid_taken = await s.scalar(
                select(Entry).where(Entry.tournament_id == tournament_id, Entry.aniimo_uid == aniimo_uid)
            )
            if uid_taken:
                raise UserError("That Aniimo UID is already registered")
            played = await s.scalar(
                select(Game.id)
                .join(Match, Match.id == Game.match_id)
                .where(
                    Match.tournament_id == tournament_id,
                    Game.status == GameStatus.CONFIRMED,
                    (Match.p1_id == old_id) | (Match.p2_id == old_id),
                )
                .limit(1)
            )
            won = await s.scalar(
                select(Match.id)
                .where(Match.tournament_id == tournament_id, Match.winner_id == old_id, Match.is_bye.is_(False))
                .limit(1)
            )
            if played or won:
                raise UserError("That player has already played, so they can't be swapped. Use /admin dq instead.")
            matches = list(
                await s.scalars(
                    select(Match).where(
                        Match.tournament_id == tournament_id,
                        Match.state != S.DONE,
                        (Match.p1_id == old_id) | (Match.p2_id == old_id),
                    )
                )
            )
            for m in matches:
                if m.state not in (S.PENDING, S.WAITING, S.NOSHOW):
                    raise UserError("The old player's match has already started")
            p = await s.get(Player, new_id)
            if p is None:
                s.add(
                    Player(
                        discord_id=new_id,
                        aniimo_uid=aniimo_uid,
                        display_name=display_name,
                        rating=1000,
                        wins=0,
                        losses=0,
                        is_fake=is_fake,
                    )
                )
            else:
                p.aniimo_uid, p.display_name = aniimo_uid, display_name
            await s.flush()
            e.discord_id, e.aniimo_uid, e.display_name = new_id, aniimo_uid, display_name
            for m in matches:
                if m.p1_id == old_id:
                    m.p1_id, m.p1_ready = new_id, False
                else:
                    m.p2_id, m.p2_ready = new_id, False
                changed.append(m.id)
                out.match(m.id, "player_swapped", old_id=old_id, new_id=new_id, by=actor_id)
            # bye wins already pushed into a later match
            for m in await s.scalars(
                select(Match).where(Match.tournament_id == tournament_id, Match.winner_id == old_id)
            ):
                m.winner_id = new_id
            await audit(s, tournament_id, None, actor_id, "admin_swap", old=old_id, new=new_id)
        await out.flush(self.ctx.notifier)
        return changed
