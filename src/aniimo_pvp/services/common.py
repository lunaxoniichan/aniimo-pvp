"""Helpers shared by the services."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AuditLog, DraftActionRow, Entry, Game, Match, Player, Tournament
from ..domain.draft import Action, DraftState
from ..domain.series import GameResult
from ..domain.states import GameStatus, MatchState, check_transition
from .errors import NotAllowed, UserError

JOB_NOSHOW_REMINDER = "noshow_reminder"
JOB_NOSHOW_ESCALATE = "noshow_escalate"
JOB_SIDE_CHOICE = "side_choice"
JOB_DRAFT_STEP = "draft_step"
JOB_AUTO_CONFIRM = "auto_confirm"
JOB_REMATCH_OFFER = "rematch_offer"
JOB_ARCHIVE = "archive_thread"
MATCH_JOBS = (JOB_NOSHOW_REMINDER, JOB_NOSHOW_ESCALATE, JOB_REMATCH_OFFER)
GAME_JOBS = (JOB_SIDE_CHOICE, JOB_DRAFT_STEP, JOB_AUTO_CONFIRM)


async def get_match(s: AsyncSession, match_id: int) -> Match:
    m = await s.get(Match, match_id)
    if m is None:
        raise UserError(f"Match #{match_id} doesn't exist")
    return m


async def get_tournament(s: AsyncSession, tournament_id: int) -> Tournament:
    t = await s.get(Tournament, tournament_id)
    if t is None:
        raise UserError(f"Tournament #{tournament_id} doesn't exist")
    return t


async def games_of(s: AsyncSession, match_id: int) -> list[Game]:
    rows = await s.scalars(select(Game).where(Game.match_id == match_id).order_by(Game.number, Game.attempt))
    return list(rows)


async def current_game(s: AsyncSession, match_id: int) -> Game | None:
    return await s.scalar(
        select(Game)
        .where(Game.match_id == match_id, Game.status != GameStatus.VOIDED)
        .order_by(Game.number.desc(), Game.attempt.desc())
        .limit(1)
    )


async def draft_rows(s: AsyncSession, game_id: int) -> list[DraftActionRow]:
    q = select(DraftActionRow).where(DraftActionRow.game_id == game_id).order_by(DraftActionRow.step)
    rows = await s.scalars(q)
    return list(rows)


def to_state(order: tuple[tuple[str, str], ...], rows: list[DraftActionRow]) -> DraftState:
    return DraftState(order=order, actions=[Action(r.step, r.side, r.kind, r.aniimo_slug, r.auto) for r in rows])


def results(games: list[Game]) -> list[GameResult]:
    return [GameResult(g.number, g.attempt, g.status, g.winner_id) for g in games]


async def fearless_locked(s: AsyncSession, match: Match, game: Game, player_id: int | None) -> set[str]:
    """Aniimo this player picked in earlier confirmed games of the series (Fearless)."""
    if player_id is None:
        return set()
    t = await s.get(Tournament, match.tournament_id)
    if not t or not t.fearless:
        return set()
    rows = await s.scalars(
        select(DraftActionRow.aniimo_slug)
        .join(Game, Game.id == DraftActionRow.game_id)
        .where(
            Game.match_id == match.id,
            Game.status == GameStatus.CONFIRMED,
            Game.number < game.number,
            DraftActionRow.kind == "pick",
            DraftActionRow.player_id == player_id,
        )
    )
    return {r for r in rows if r}


def transition(match: Match, target: MatchState) -> None:
    check_transition(match.state, target)
    match.state = target.value


def require_state(match: Match, *states: MatchState, message: str | None = None) -> None:
    if match.state not in {s.value for s in states}:
        raise UserError(message or f"That isn't possible right now (match is {match.state.replace('_', ' ')})")


def require_player(match: Match, actor_id: int) -> int:
    if actor_id not in (match.p1_id, match.p2_id):
        raise NotAllowed("You're not a player in this match")
    return actor_id


def opponent(match: Match, player_id: int | None) -> int | None:
    if player_id == match.p1_id:
        return match.p2_id
    if player_id == match.p2_id:
        return match.p1_id
    return None


async def audit(s: AsyncSession, tournament_id: int | None, match_id: int | None, actor_id: int | None,
                action: str, **payload: Any) -> None:
    s.add(AuditLog(tournament_id=tournament_id, match_id=match_id, actor_id=actor_id, action=action,
                   payload=payload or None))


async def entry_for(s: AsyncSession, tournament_id: int, discord_id: int) -> Entry | None:
    return await s.scalar(select(Entry).where(Entry.tournament_id == tournament_id, Entry.discord_id == discord_id))


async def player_name(s: AsyncSession, tournament_id: int, discord_id: int | None) -> str:
    if discord_id is None:
        return "TBD"
    e = await entry_for(s, tournament_id, discord_id)
    if e:
        return e.display_name
    p = await s.get(Player, discord_id)
    return p.display_name if p else str(discord_id)
