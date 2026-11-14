"""Wires settings, database, roster, clock, notifier and services together."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..clock import Clock
from ..config import Settings
from ..db.database import Database
from ..domain.roster import Roster
from .locks import LockRegistry
from .notifier import Notifier
from .scheduler import Scheduler

if TYPE_CHECKING:
    from .drafts import DraftService
    from .friendly import FriendlyService
    from .matches import MatchService
    from .stats import StatsService
    from .tournament import TournamentService


@dataclass
class AppContext:
    settings: Settings
    db: Database
    roster: Roster
    notifier: Notifier
    clock: Clock = field(default_factory=Clock)
    rng: random.Random = field(default_factory=random.SystemRandom)
    locks: LockRegistry = field(default_factory=LockRegistry)
    scheduler: Scheduler = None  # type: ignore[assignment]
    tournaments: TournamentService = None  # type: ignore[assignment]
    matches: MatchService = None  # type: ignore[assignment]
    drafts: DraftService = None  # type: ignore[assignment]
    stats: StatsService = None  # type: ignore[assignment]
    friendlies: FriendlyService = None  # type: ignore[assignment]

    @property
    def order(self) -> tuple[tuple[str, str], ...]:
        return self.settings.draft_order


def build_context(
    settings: Settings,
    db: Database,
    roster: Roster,
    notifier: Notifier,
    clock: Clock | None = None,
    rng: random.Random | None = None,
) -> AppContext:
    from .common import (
        JOB_AUTO_CONFIRM,
        JOB_CHALLENGE_EXPIRE,
        JOB_DRAFT_STEP,
        JOB_FRIENDLY_IDLE,
        JOB_NOSHOW_ESCALATE,
        JOB_NOSHOW_REMINDER,
        JOB_REMATCH_OFFER,
        JOB_SIDE_CHOICE,
    )
    from .drafts import DraftService
    from .friendly import FriendlyService
    from .matches import MatchService
    from .stats import StatsService
    from .tournament import TournamentService

    ctx = AppContext(
        settings=settings,
        db=db,
        roster=roster,
        notifier=notifier,
        clock=clock or Clock(),
        rng=rng or random.SystemRandom(),
    )
    ctx.scheduler = Scheduler(db, ctx.clock)
    ctx.tournaments = TournamentService(ctx)
    ctx.matches = MatchService(ctx)
    ctx.drafts = DraftService(ctx)
    ctx.stats = StatsService(ctx)
    ctx.friendlies = FriendlyService(ctx)
    sch = ctx.scheduler
    sch.register(JOB_NOSHOW_REMINDER, ctx.matches.job_noshow_reminder)
    sch.register(JOB_NOSHOW_ESCALATE, ctx.matches.job_noshow_escalate)
    sch.register(JOB_SIDE_CHOICE, ctx.matches.job_side_choice)
    sch.register(JOB_AUTO_CONFIRM, ctx.matches.job_auto_confirm)
    sch.register(JOB_REMATCH_OFFER, ctx.matches.job_rematch_offer)
    sch.register(JOB_DRAFT_STEP, ctx.drafts.job_step_timeout)
    sch.register(JOB_CHALLENGE_EXPIRE, ctx.friendlies.job_challenge_expire)
    sch.register(JOB_FRIENDLY_IDLE, ctx.friendlies.job_idle)
    return ctx
