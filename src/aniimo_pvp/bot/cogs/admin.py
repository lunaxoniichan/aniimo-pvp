"""/admin commands. Run them inside a match thread, or pass match:<id> (see /tournament status)."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands

from ...services.errors import UserError
from ...services.views import load_match_view
from ..handlers import reply
from ..util import is_admin

if TYPE_CHECKING:
    from ..client import AniimoBot

log = logging.getLogger(__name__)


def parse_id(text: str) -> int:
    digits = re.sub(r"\D", "", text or "")
    if not digits:
        raise UserError("Give a Discord user (mention or ID)")
    return int(digits)


class AdminCog(commands.GroupCog, group_name="admin", group_description="Tournament admin tools"):
    def __init__(self, bot: AniimoBot):
        self.bot = bot
        super().__init__()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not is_admin(self.bot, interaction.user):
            await reply(interaction, "⚠️ Only tournament admins can use /admin")
            return False
        return True

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        original = getattr(error, "original", error)
        if isinstance(original, UserError):
            await reply(interaction, f"⚠️ {original}")
        elif isinstance(error, app_commands.CheckFailure):
            return
        else:
            log.exception("Admin command failed", exc_info=original)
            await reply(interaction, "⚠️ Something went wrong. Check the bot log.")

    async def _match(self, interaction: discord.Interaction, match: int | None) -> int:
        if match:
            return match
        if isinstance(interaction.channel, discord.Thread):
            m = await self.bot.ctx.matches.find_by_thread(interaction.channel.id)
            if m:
                return m.id
        raise UserError("Run this inside a match thread, or pass match:<id> (see /tournament status)")

    async def _done(self, interaction: discord.Interaction, text: str) -> None:
        await reply(interaction, f"✅ {text}")

    # ----- autocompletes -----
    async def _match_players(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        try:
            mid = await self._match(interaction, getattr(interaction.namespace, "match", None))
            v = await load_match_view(self.bot.ctx, mid)
        except Exception:
            return []
        return [
            app_commands.Choice(name=p.name, value=str(p.id))
            for p in (v.p1, v.p2)
            if p and current.lower() in p.name.lower()
        ]

    async def _entries(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        t = await self.bot.ctx.tournaments.active(interaction.guild_id)
        if t is None:
            return []
        entries = await self.bot.ctx.tournaments.entries(t.id)
        return [
            app_commands.Choice(name=f"{e.display_name} ({e.status})", value=str(e.discord_id))
            for e in entries
            if current.lower() in e.display_name.lower()
        ][:25]

    # ----- commands -----
    @app_commands.command(name="undo", description="Undo the last draft step")
    async def undo(self, interaction: discord.Interaction, match: int | None = None) -> None:
        mid = await self._match(interaction, match)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.ctx.drafts.admin_undo(mid, interaction.user.id)
        await self._done(interaction, "Last draft step undone.")

    @app_commands.command(name="pause", description="Pause the draft timer")
    async def pause(self, interaction: discord.Interaction, match: int | None = None) -> None:
        mid = await self._match(interaction, match)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.ctx.matches.admin_pause(mid, interaction.user.id)
        await self._done(interaction, "Draft paused.")

    @app_commands.command(name="resume", description="Resume a paused draft (full timer for the current step)")
    async def resume(self, interaction: discord.Interaction, match: int | None = None) -> None:
        mid = await self._match(interaction, match)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.ctx.matches.admin_resume(mid, interaction.user.id)
        await self._done(interaction, "Draft resumed.")

    @app_commands.command(name="set-result", description="Set the winner of the current game")
    @app_commands.autocomplete(winner=_match_players)
    async def set_result(self, interaction: discord.Interaction, winner: str, match: int | None = None) -> None:
        mid = await self._match(interaction, match)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.ctx.matches.admin_set_result(mid, interaction.user.id, parse_id(winner))
        await self._done(interaction, "Game result set.")

    @app_commands.command(name="award", description="Give the whole match to a player (walkover)")
    @app_commands.autocomplete(winner=_match_players)
    async def award(self, interaction: discord.Interaction, winner: str, match: int | None = None) -> None:
        mid = await self._match(interaction, match)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.ctx.matches.admin_award(mid, interaction.user.id, parse_id(winner))
        await self._done(interaction, "Match awarded.")

    @app_commands.command(name="replay", description="Replay the current game (same teams or a new draft)")
    async def replay(
        self, interaction: discord.Interaction, mode: Literal["same_teams", "new_draft"], match: int | None = None
    ) -> None:
        mid = await self._match(interaction, match)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.ctx.matches.admin_replay(mid, interaction.user.id, mode)
        await self._done(interaction, "Replay started.")

    @app_commands.command(name="extend", description="Extend the ready check")
    async def extend(
        self, interaction: discord.Interaction, minutes: app_commands.Range[int, 1, 120] = 10, match: int | None = None
    ) -> None:
        mid = await self._match(interaction, match)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.ctx.matches.admin_extend(mid, interaction.user.id, minutes)
        await self._done(interaction, f"Ready check extended by {minutes} minutes.")

    @app_commands.command(name="dq", description="Disqualify a player (their opponent advances)")
    @app_commands.autocomplete(player=_entries)
    async def dq(self, interaction: discord.Interaction, player: str) -> None:
        t = await self.bot.ctx.tournaments.require_active(interaction.guild_id)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.ctx.matches.admin_dq_player(t.id, interaction.user.id, parse_id(player))
        await self._done(interaction, "Player disqualified.")

    @app_commands.command(name="swap", description="Replace a player who hasn't played yet")
    @app_commands.autocomplete(old=_entries)
    @app_commands.describe(
        new="The new player (mention or user ID)",
        aniimo_uid="The new player's Aniimo UID",
        name="Display name (optional)",
    )
    async def swap(
        self, interaction: discord.Interaction, old: str, new: str, aniimo_uid: str, name: str | None = None
    ) -> None:
        t = await self.bot.ctx.tournaments.require_active(interaction.guild_id)
        new_id = parse_id(new)
        member = interaction.guild.get_member(new_id) if interaction.guild else None
        if member is None and interaction.guild:
            try:
                member = await interaction.guild.fetch_member(new_id)
            except discord.HTTPException:
                member = None
        if member is None and not self.bot.settings.dev_mode:
            raise UserError("The new player isn't a member of this server")
        display = name or (member.display_name if member else f"Player {str(new_id)[-4:]}")
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.ctx.matches.admin_swap(
            t.id, interaction.user.id, parse_id(old), new_id, aniimo_uid.strip(), display, is_fake=member is None
        )
        await self._done(interaction, f"{display} replaced the old player.")
