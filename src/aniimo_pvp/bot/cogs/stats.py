"""/stats, /leaderboard, /profile."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands

from ...services.errors import UserError
from ..handlers import reply
from ..util import GOLD, PURPLE

if TYPE_CHECKING:
    from ..client import AniimoBot


class StatsCog(commands.Cog):
    stats = app_commands.Group(name="stats", description="Pick/ban stats")

    def __init__(self, bot: AniimoBot):
        self.bot = bot

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        original = getattr(error, "original", error)
        await reply(interaction, f"⚠️ {original}" if isinstance(original, UserError) else "⚠️ Something went wrong.")

    @stats.command(name="aniimo", description="Most picked/banned Aniimo and win rates")
    async def aniimo(self, interaction: discord.Interaction, scope: Literal["current", "all"] = "current") -> None:
        tid = None
        title = "All tournaments"
        if scope == "current":
            t = await self.bot.ctx.tournaments.latest(interaction.guild_id)
            if t:
                tid, title = t.id, t.name
        drafts, stats = await self.bot.ctx.stats.aniimo(tid)
        if not stats:
            raise UserError("No confirmed games yet")
        name = self.bot.ctx.roster.name
        lines = [f"`{'Aniimo':<14}{'Pick':>5}{'Ban':>5}{'Pres':>6}{'Win':>6}`"]
        for s in stats[:20]:
            wr = f"{round(s.win_rate * 100)}%" if s.win_rate is not None else "—"
            lines.append(
                f"`{name(s.slug)[:14]:<14}{s.picks:>5}{s.bans:>5}{round(s.presence(drafts) * 100):>5}%{wr:>6}`"
            )
        e = discord.Embed(title=f"Aniimo stats · {title}", colour=PURPLE, description="\n".join(lines))
        e.set_footer(text=f"{drafts} confirmed games · presence = picked or banned")
        await interaction.response.send_message(embed=e)

    @stats.command(name="player", description="A player's record and favourite Aniimo")
    async def player(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        await self._profile(interaction, member or interaction.user)

    @app_commands.command(name="profile", description="A player's rating, record and favourite Aniimo")
    async def profile(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        await self._profile(interaction, member or interaction.user)

    async def _profile(self, interaction: discord.Interaction, user: discord.abc.User) -> None:
        prof = await self.bot.ctx.stats.profile(user.id)
        if prof is None:
            raise UserError(f"{user.display_name} hasn't played in a tournament yet")
        name = self.bot.ctx.roster.name
        p = prof.player
        e = discord.Embed(title=f"{p.display_name}", colour=PURPLE)
        e.add_field(name="Rating", value=str(p.rating))
        e.add_field(name="Matches", value=f"{p.wins}W – {p.losses}L")
        e.add_field(name="Games", value=f"{prof.games_won}W – {prof.games_lost}L")
        if prof.top_picks:
            e.add_field(name="Favourite Aniimo", value="\n".join(f"{name(s)} · {n}" for s, n in prof.top_picks))
        if prof.banned_against:
            e.add_field(
                name="Most banned against them", value="\n".join(f"{name(s)} · {n}" for s, n in prof.banned_against)
            )
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="leaderboard", description="Top players by rating across tournaments")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        players = await self.bot.ctx.stats.leaderboard(10)
        if not players:
            raise UserError("No rated matches yet")
        medals = ["🥇", "🥈", "🥉"]
        lines = [
            f"{medals[i] if i < 3 else f'`{i + 1:>2}`'} **{p.display_name}** · {p.rating} · {p.wins}W–{p.losses}L"
            for i, p in enumerate(players)
        ]
        await interaction.response.send_message(
            embed=discord.Embed(title="Leaderboard", colour=GOLD, description="\n".join(lines))
        )
