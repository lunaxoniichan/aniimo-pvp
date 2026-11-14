"""Pick/ban stats, player profiles and the ELO leaderboard. Only confirmed games count."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from ..db.models import DraftActionRow, Game, Match, Player
from ..domain.states import GameStatus

if TYPE_CHECKING:
    from .context import AppContext


@dataclass
class AniimoStat:
    slug: str
    picks: int = 0
    bans: int = 0
    wins: int = 0

    def presence(self, drafts: int) -> float:
        return (self.picks + self.bans) / drafts if drafts else 0.0

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.picks if self.picks else None


@dataclass
class PlayerProfile:
    player: Player
    matches: int
    games_won: int
    games_lost: int
    top_picks: list[tuple[str, int]] = field(default_factory=list)
    banned_against: list[tuple[str, int]] = field(default_factory=list)
    friendly_drafts: int = 0  # drafts played in friendly duels (not part of the record above)


class StatsService:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx

    async def _confirmed_games(self, s, tournament_id: int | None):
        q = select(Game).where(Game.status == GameStatus.CONFIRMED)
        if tournament_id is not None:
            q = q.join(Match, Match.id == Game.match_id).where(Match.tournament_id == tournament_id)
        return list(await s.scalars(q))

    async def aniimo(self, tournament_id: int | None = None) -> tuple[int, list[AniimoStat]]:
        async with self.ctx.db.session() as s:
            games = await self._confirmed_games(s, tournament_id)
            if not games:
                return 0, []
            winners = {g.id: g.winner_id for g in games}
            rows = await s.scalars(select(DraftActionRow).where(DraftActionRow.game_id.in_(list(winners))))
            stats: dict[str, AniimoStat] = {}
            for r in rows:
                if not r.aniimo_slug:
                    continue
                st = stats.setdefault(r.aniimo_slug, AniimoStat(r.aniimo_slug))
                if r.kind == "pick":
                    st.picks += 1
                    if r.player_id is not None and r.player_id == winners[r.game_id]:
                        st.wins += 1
                elif r.kind == "ban":
                    st.bans += 1
            return len(games), sorted(stats.values(), key=lambda x: (-(x.picks + x.bans), x.slug))

    async def summary(self, tournament_id: int) -> dict[str, list[tuple[str, int | float]]]:
        drafts, stats = await self.aniimo(tournament_id)
        most_banned = sorted((x for x in stats if x.bans), key=lambda x: -x.bans)[:3]
        most_picked = sorted((x for x in stats if x.picks), key=lambda x: -x.picks)[:3]
        best = sorted((x for x in stats if x.picks >= 3), key=lambda x: -(x.win_rate or 0))[:3]
        return {
            "drafts": [("games", drafts)],
            "most_banned": [(x.slug, x.bans) for x in most_banned],
            "most_picked": [(x.slug, x.picks) for x in most_picked],
            "best_win_rate": [(x.slug, round((x.win_rate or 0) * 100)) for x in best],
        }

    async def profile(self, player_id: int) -> PlayerProfile | None:
        async with self.ctx.db.session() as s:
            p = await s.get(Player, player_id)
            if p is None:
                return None
            matches = await s.scalar(
                select(func.count())
                .select_from(Match)
                .where(
                    (Match.p1_id == player_id) | (Match.p2_id == player_id),
                    Match.winner_id.is_not(None),
                    Match.is_bye.is_(False),
                    Match.walkover.is_(False),
                )
            )
            games = list(
                await s.scalars(
                    select(Game)
                    .join(Match, Match.id == Game.match_id)
                    .where(Game.status == GameStatus.CONFIRMED, (Match.p1_id == player_id) | (Match.p2_id == player_id))
                )
            )
            won = sum(1 for g in games if g.winner_id == player_id)
            ids = [g.id for g in games]
            picks: Counter[str] = Counter()
            banned: Counter[str] = Counter()
            if ids:
                for r in await s.scalars(select(DraftActionRow).where(DraftActionRow.game_id.in_(ids))):
                    if not r.aniimo_slug:
                        continue
                    if r.kind == "pick" and r.player_id == player_id:
                        picks[r.aniimo_slug] += 1
                    elif r.kind == "ban" and r.player_id is not None and r.player_id != player_id:
                        banned[r.aniimo_slug] += 1
        friendly = await self.ctx.friendlies.drafts_played(player_id)
        return PlayerProfile(
            p, matches or 0, won, len(games) - won, picks.most_common(3), banned.most_common(3), friendly
        )

    async def leaderboard(self, limit: int = 10) -> list[Player]:
        async with self.ctx.db.session() as s:
            q = select(Player).where((Player.wins + Player.losses) > 0)
            if not self.ctx.settings.dev_mode:
                q = q.where(Player.is_fake.is_(False))
            rows = await s.scalars(q.order_by(Player.rating.desc(), Player.wins.desc()).limit(limit))
            return list(rows)
