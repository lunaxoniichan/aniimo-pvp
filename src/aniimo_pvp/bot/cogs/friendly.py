"""/duel, /register and (dev mode) /duel-test: friendly 1v1 drafts in the friendly channel."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ...services.common import JOB_CHALLENGE_EXPIRE
from ...services.errors import UserError
from ...services.views import load_match_view
from .. import panels
from ..handlers import reply

if TYPE_CHECKING:
    from ..client import AniimoBot

log = logging.getLogger(__name__)
TEST_FRIEND = (424242424242424242, "Test Friend")  # dev mode: a pretend opponent the admin plays for
CARD_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)


class FriendlyCog(commands.Cog):
    def __init__(self, bot: AniimoBot):
        self.bot = bot

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        original = getattr(error, "original", error)
        if isinstance(original, UserError):
            await reply(interaction, f"⚠️ {original}")
        else:
            log.exception("Command failed", exc_info=original)
            await reply(interaction, "⚠️ Something went wrong. An admin can check the bot log.")

    def _check_channel(self, interaction: discord.Interaction) -> None:
        friendly = self.bot.settings.channels.friendly
        if not friendly:
            raise UserError("Friendly duels aren't set up on this server yet. An admin sets `channels.friendly`.")
        if interaction.channel_id != friendly:
            raise UserError(f"Duels start in <#{friendly}>. Use `/duel` there.")

    async def _challenge(
        self, interaction: discord.Interaction, opponent: tuple[int, str], fearless: bool, *, fake: bool
    ) -> None:
        ctx = self.bot.ctx
        user = interaction.user
        t, m = await ctx.friendlies.challenge(
            interaction.guild_id or self.bot.settings.guild_id,
            interaction.channel_id,
            (user.id, user.display_name),
            opponent,
            fearless=fearless,
            opponent_is_fake=fake,
        )
        v = await load_match_view(ctx, m.id)
        jobs = await ctx.scheduler.pending(JOB_CHALLENGE_EXPIRE, t.id)
        content, embed, view = panels.duel_card(v, due=jobs[0].due_at if jobs else None)
        await interaction.response.send_message(content, embed=embed, view=view, allowed_mentions=CARD_MENTIONS)
        msg = await interaction.original_response()
        await ctx.friendlies.set_card(t.id, msg.channel.id, msg.id)

    @app_commands.command(name="duel", description="Challenge a friend to a 1v1 draft (no results or ratings)")
    @app_commands.describe(opponent="Who you want to draft against", fearless="No re-picking your earlier Aniimo")
    async def duel(self, interaction: discord.Interaction, opponent: discord.Member, fearless: bool = False) -> None:
        self._check_channel(interaction)
        if opponent.bot:
            raise UserError("Bots don't draft. Pick a friend!")
        await self._challenge(interaction, (opponent.id, opponent.display_name), fearless, fake=False)

    @app_commands.command(name="duel-test", description="Dev mode: duel a test player you play for yourself")
    @app_commands.describe(fearless="No re-picking your earlier Aniimo")
    async def duel_test(self, interaction: discord.Interaction, fearless: bool = False) -> None:
        if not self.bot.settings.dev_mode:
            raise UserError("`/duel-test` only works in dev mode")
        self._check_channel(interaction)
        await self._challenge(interaction, TEST_FRIEND, fearless, fake=True)

    @app_commands.command(name="register", description="Save your Aniimo UID so it shows in your duel rooms")
    @app_commands.describe(uid="Your in-game Aniimo UID, e.g. 100234567")
    async def register(self, interaction: discord.Interaction, uid: str) -> None:
        await self.bot.ctx.friendlies.register_uid(interaction.user.id, interaction.user.display_name, uid)
        await reply(
            interaction,
            f"Saved. Your Aniimo UID `{uid.strip()}` now shows in your duel rooms.\n"
            "-# Tournaments use the UID from the admin's player list.",
        )
