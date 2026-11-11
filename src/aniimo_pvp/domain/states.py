"""Status values stored in the database and the allowed match transitions."""

from __future__ import annotations

from enum import StrEnum


class TournamentStatus(StrEnum):
    SETUP = "setup"
    REGISTRATION = "registration"
    RUNNING = "running"
    FINISHED = "finished"
    CANCELLED = "cancelled"


ACTIVE_TOURNAMENT = (TournamentStatus.SETUP, TournamentStatus.REGISTRATION, TournamentStatus.RUNNING)


class EntryStatus(StrEnum):
    ACTIVE = "active"
    ELIMINATED = "eliminated"
    DQ = "dq"
    CHAMPION = "champion"


class MatchState(StrEnum):
    PENDING = "pending"  # created, not opened yet (players may be unknown)
    WAITING = "waiting"  # room open, ready check
    NOSHOW = "noshow"  # ready check timed out, admin decides
    SIDE_CHOICE = "side_choice"
    DRAFTING = "drafting"
    PAUSED = "paused"
    PLAYING = "playing"
    CONFIRMING = "confirming"
    DISPUTED = "disputed"
    BETWEEN_GAMES = "between_games"
    DONE = "done"


class GameStatus(StrEnum):
    SIDE_CHOICE = "side_choice"
    DRAFTING = "drafting"
    PLAYING = "playing"
    REPORTED = "reported"
    CONFIRMED = "confirmed"
    DISPUTED = "disputed"
    VOIDED = "voided"


class VoidReason(StrEnum):
    REMATCH_SAME_TEAMS = "rematch_same_teams"
    REMATCH_NEW_DRAFT = "rematch_new_draft"
    ADMIN_REPLAY = "admin_replay"


S = MatchState

ALLOWED: dict[MatchState, set[MatchState]] = {
    S.PENDING: {S.WAITING, S.DONE},
    S.WAITING: {S.SIDE_CHOICE, S.NOSHOW, S.DONE},
    S.NOSHOW: {S.WAITING, S.DONE},
    S.SIDE_CHOICE: {S.DRAFTING, S.DONE},
    S.DRAFTING: {S.PLAYING, S.PAUSED, S.DONE},
    S.PAUSED: {S.DRAFTING, S.DONE},
    # PLAYING -> PLAYING is a same-teams rematch; PLAYING -> DRAFTING is a new-draft rematch or an undo
    S.PLAYING: {S.CONFIRMING, S.PLAYING, S.DRAFTING, S.BETWEEN_GAMES, S.DONE},
    S.CONFIRMING: {S.BETWEEN_GAMES, S.DONE, S.DISPUTED, S.PLAYING, S.DRAFTING},
    S.DISPUTED: {S.BETWEEN_GAMES, S.DONE, S.PLAYING, S.DRAFTING},
    S.BETWEEN_GAMES: {S.SIDE_CHOICE, S.DONE},
    S.DONE: set(),
}


class InvalidTransition(ValueError):
    pass


def check_transition(current: str, target: str) -> None:
    cur, tgt = MatchState(current), MatchState(target)
    if tgt not in ALLOWED[cur]:
        raise InvalidTransition(f"Match can't go from {cur.value} to {tgt.value}")
