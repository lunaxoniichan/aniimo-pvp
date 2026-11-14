"""/roster reload and /roster info."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ...domain.roster import RosterError, load_roster
from ..handlers import reply
from ..util import PURPLE, is_admin

if TYPE_CHECKING:
    from ..client import AniimoBot


class RosterCog(commands.GroupCog, group_name="roster", group_description="Aniimo roster and thumbnails"):
    def __init__(self, bot: AniimoBot):
        self.bot = bot
        super().__init__()

    @app_commands.command(name="reload", description="Re-read data/roster.csv and the thumbnails, then sync emojis")
    async def reload(self, interaction: discord.Interaction) -> None:
        if not is_admin(self.bot, interaction.user):
            await reply(interaction, "⚠️ Only tournament admins can reload the roster")
            return
        p = self.bot.settings.paths
        try:
            roster, warnings = load_roster(p.roster_csv, p.images_dir)
        except RosterError as e:
            await reply(interaction, f"⚠️ Roster not reloaded: {e}")
            return
        self.bot.ctx.roster = roster
        self.bot.thumbs.clear()
        self.bot.icons.clear()
        with_img = sum(1 for a in roster.items if a.image_path)
        text = f"Reloaded {len(roster)} Aniimo ({with_img} with thumbnails). Emoji sync is running in the background."
        if warnings:
            text += "\n" + "\n".join(f"• {w}" for w in warnings[:10])
        await reply(interaction, text)
        asyncio.create_task(self.bot.aniimo_emojis.sync(roster))

    @app_commands.command(name="info", description="Roster size, missing thumbnails and emoji status")
    async def info(self, interaction: discord.Interaction) -> None:
        r = self.bot.ctx.roster
        missing = [a.name for a in r.items if not a.image_path]
        e = discord.Embed(title="Roster", colour=PURPLE)
        e.add_field(name="Aniimo", value=str(len(r)))
        e.add_field(name="Elements", value=", ".join(r.elements()) or "—", inline=False)
        e.add_field(
            name="Missing thumbnails",
            value=(", ".join(missing[:30]) + (" …" if len(missing) > 30 else "")) or "None",
            inline=False,
        )
        e.add_field(name="Emojis", value=self.bot.aniimo_emojis.status, inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)
