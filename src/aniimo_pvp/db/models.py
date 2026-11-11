"""SQLAlchemy models. See docs/SYSTEM_DESIGN.md §7."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..clock import utcnow


class Base(DeclarativeBase):
    pass


class Player(Base):
    __tablename__ = "players"
    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    aniimo_uid: Mapped[str] = mapped_column(String(32))
    display_name: Mapped[str] = mapped_column(String(64))
    rating: Mapped[int] = mapped_column(Integer, default=1000)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    is_fake: Mapped[bool] = mapped_column(Boolean, default=False)


class Tournament(Base):
    __tablename__ = "tournaments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), index=True)
    player_cap: Mapped[int] = mapped_column(Integer, default=32)
    format: Mapped[str] = mapped_column(String(20), default="standard")
    fearless: Mapped[bool] = mapped_column(Boolean, default=False)
    rng_seed: Mapped[str | None] = mapped_column(String(32))
    bracket_size: Mapped[int | None] = mapped_column(Integer)
    current_round: Mapped[int] = mapped_column(Integer, default=0)
    completed_round: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    champion_id: Mapped[int | None] = mapped_column(BigInteger)
    setup_channel_id: Mapped[int | None] = mapped_column(BigInteger)
    setup_message_id: Mapped[int | None] = mapped_column(BigInteger)
    preview_message_id: Mapped[int | None] = mapped_column(BigInteger)
    live_status_message_id: Mapped[int | None] = mapped_column(BigInteger)
    pending_players: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)


class Entry(Base):
    __tablename__ = "entries"
    __table_args__ = (
        UniqueConstraint("tournament_id", "discord_id"),
        UniqueConstraint("tournament_id", "aniimo_uid"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id", ondelete="CASCADE"), index=True)
    discord_id: Mapped[int] = mapped_column(ForeignKey("players.discord_id"))
    display_name: Mapped[str] = mapped_column(String(64))
    aniimo_uid: Mapped[str] = mapped_column(String(32))
    seed: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="active")


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("tournament_id", "round", "slot"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id", ondelete="CASCADE"), index=True)
    round: Mapped[int] = mapped_column(Integer)
    slot: Mapped[int] = mapped_column(Integer)
    bo: Mapped[int] = mapped_column(Integer, default=1)
    p1_id: Mapped[int | None] = mapped_column(BigInteger)
    p2_id: Mapped[int | None] = mapped_column(BigInteger)
    winner_id: Mapped[int | None] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    is_bye: Mapped[bool] = mapped_column(Boolean, default=False)
    walkover: Mapped[bool] = mapped_column(Boolean, default=False)
    forfeit_slot: Mapped[int | None] = mapped_column(Integer)  # 1/2: that slot's player was removed
    advanced: Mapped[bool] = mapped_column(Boolean, default=False)  # winner pushed to the next match
    next_match_id: Mapped[int | None] = mapped_column(ForeignKey("matches.id"))
    next_slot: Mapped[int | None] = mapped_column(Integer)
    thread_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    panel_message_id: Mapped[int | None] = mapped_column(BigInteger)
    turn_message_id: Mapped[int | None] = mapped_column(BigInteger)
    live_message_id: Mapped[int | None] = mapped_column(BigInteger)
    p1_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    p2_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    coin_winner_id: Mapped[int | None] = mapped_column(BigInteger)
    side_chooser_id: Mapped[int | None] = mapped_column(BigInteger)
    rematch_by: Mapped[int | None] = mapped_column(BigInteger)
    rematch_mode: Mapped[str | None] = mapped_column(String(20))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    p1_rating_delta: Mapped[int | None] = mapped_column(Integer)
    p2_rating_delta: Mapped[int | None] = mapped_column(Integer)


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        UniqueConstraint("match_id", "number", "attempt"),
        # at most one live (not voided) attempt per game number
        Index("uq_game_live_attempt", "match_id", "number", unique=True,
              sqlite_where=text("status != 'voided'")),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16))
    side_a_id: Mapped[int | None] = mapped_column(BigInteger)
    side_b_id: Mapped[int | None] = mapped_column(BigInteger)
    winner_id: Mapped[int | None] = mapped_column(BigInteger)
    reported_by: Mapped[int | None] = mapped_column(BigInteger)
    reported_winner_id: Mapped[int | None] = mapped_column(BigInteger)
    screenshot_url: Mapped[str | None] = mapped_column(String(500))
    void_reason: Mapped[str | None] = mapped_column(String(32))
    draft_source_id: Mapped[int | None] = mapped_column(ForeignKey("games.id"))
    step_deadline: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)


class DraftActionRow(Base):
    __tablename__ = "draft_actions"
    __table_args__ = (UniqueConstraint("game_id", "step"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    step: Mapped[int] = mapped_column(Integer)
    side: Mapped[str] = mapped_column(String(1))
    player_id: Mapped[int | None] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(8))  # ban | pick | skip
    aniimo_slug: Mapped[str | None] = mapped_column(String(64))
    auto: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_due", "status", "due_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    ref_id: Mapped[int] = mapped_column(Integer, index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime)
    duration_seconds: Mapped[float] = mapped_column()
    status: Mapped[str] = mapped_column(String(12), default="pending")
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int | None] = mapped_column(Integer, index=True)
    match_id: Mapped[int | None] = mapped_column(Integer)
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
