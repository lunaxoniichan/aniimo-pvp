"""/tournament commands + CSV paste listener + screenshot listener."""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ...domain import formats
from ...domain.players_csv import looks_like_players_csv
from ...domain.states import TournamentStatus
from ...services.errors import NotAllowed, UserError
from ...services.views import load_bracket
from .. import panels
from ..handlers import reply
from ..util import PURPLE, is_admin

if TYPE_CHECKING:
    from ..client import AniimoBot

log = logging.getLogger(__name__)
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")


def member_lookup(guild: discord.Guild):
    cache: dict[int, str | None] = {}

    async def lookup(discord_id: int) -> str | None:
        if discord_id in cache:
            return cache[discord_id]
        m = guild.get_member(discord_id)
        if m is None:
            try:
                m = await guild.fetch_member(discord_id)
            except discord.HTTPException:
                m = None
        cache[discord_id] = m.display_name if m else None
        return cache[discord_id]

    return lookup


class TournamentCog(commands.GroupCog, group_name="tournament", group_description="Run an Aniimo draft tournament"):
    def __init__(self, bot: AniimoBot):
        self.bot = bot
        super().__init__()

    async def _admin(self, interaction: discord.Interaction) -> None:
        if not is_admin(self.bot, interaction.user):
            raise NotAllowed("Only tournament admins can do that")

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        original = getattr(error, "original", error)
        if isinstance(original, UserError):
            await reply(interaction, f"⚠️ {original}")
        else:
            log.exception("Command failed", exc_info=original)
            await reply(interaction, "⚠️ Something went wrong. An admin can check the bot log.")

    @app_commands.command(name="create", description="Step 1: create a tournament and pick the player cap + format")
    @app_commands.describe(name="Tournament name, e.g. AniiCup #1")
    async def create(self, interaction: discord.Interaction, name: str) -> None:
        await self._admin(interaction)
        t = await self.bot.ctx.tournaments.create(interaction.guild_id, name, interaction.user.id)
        await interaction.response.send_message(embed=panels.setup_embed(t), view=panels.setup_view(t))
        msg = await interaction.original_response()
        await self.bot.ctx.tournaments.set_message_ids(t.id, setup_channel_id=msg.channel.id, setup_message_id=msg.id)

    @app_commands.command(name="players", description="Step 2: upload the player CSV (discord_id, aniimo_uid, name)")
    async def players(self, interaction: discord.Interaction, file: discord.Attachment) -> None:
        await self._admin(interaction)
        t = await self.bot.ctx.tournaments.require_active(
            interaction.guild_id, TournamentStatus.SETUP, TournamentStatus.REGISTRATION
        )
        await interaction.response.defer(ephemeral=True, thinking=True)
        text = (await file.read()).decode("utf-8-sig", errors="replace")
        await self.bot.ctx.tournaments.preview_players(
            t.id, interaction.user.id, text, member_lookup(interaction.guild)
        )
        await reply(interaction, "Preview posted in the setup channel.")

    @app_commands.command(name="start", description="Shuffle the bracket and open the first round")
    async def start(self, interaction: discord.Interaction) -> None:
        await self._admin(interaction)
        t = await self.bot.ctx.tournaments.require_active(interaction.guild_id, TournamentStatus.REGISTRATION)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.ctx.tournaments.start(t.id, interaction.user.id)
        await reply(interaction, "Started! The bracket is posted in the announce channel.")

    @app_commands.command(name="status", description="Show the current tournament and its matches")
    async def status(self, interaction: discord.Interaction) -> None:
        t = await self.bot.ctx.tournaments.latest(interaction.guild_id)
        if t is None:
            raise UserError("No tournament yet")
        e = discord.Embed(title=f"{t.name} · {t.status}", colour=PURPLE)
        e.add_field(name="Format", value=f"{formats.FORMATS[t.format].label} · cap {t.player_cap}")
        e.add_field(name="Fearless", value="On" if t.fearless else "Off")
        if t.bracket_size:
            e.add_field(name="Round", value=f"{t.current_round} of {formats.round_count(t.bracket_size)}")
            lines = []
            for m in await self.bot.ctx.tournaments.matches(t.id, t.current_round):
                if m.is_bye:
                    continue
                link = f" <#{m.thread_id}>" if m.thread_id else ""
                lines.append(f"`#{m.id}` M{m.slot + 1} · {m.state.replace('_', ' ')}{link}")
            if lines:
                e.add_field(name="Matches (id · state)", value="\n".join(lines)[:1024], inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="bracket", description="Show the current bracket image")
    async def bracket(self, interaction: discord.Interaction) -> None:
        t = await self.bot.ctx.tournaments.latest(interaction.guild_id)
        if t is None or not t.bracket_size:
            raise UserError("There's no bracket yet")
        await interaction.response.defer(ephemeral=True, thinking=True)
        tt, rounds = await load_bracket(self.bot.ctx, t.id)
        png = self.bot.bracket.render(tt.name, tt.format, rounds)
        await interaction.followup.send(file=discord.File(io.BytesIO(png), "bracket.png"), ephemeral=True)

    @app_commands.command(name="cancel", description="Cancel the active tournament")
    async def cancel(self, interaction: discord.Interaction) -> None:
        await self._admin(interaction)
        t = await self.bot.ctx.tournaments.require_active(interaction.guild_id)
        from ..handlers import handle_tournament_action

        await handle_tournament_action(interaction, "cancel", t.id, None)

    # ---------- listeners ----------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        try:
            if isinstance(message.channel, discord.Thread):
                await self._maybe_screenshot(message)
            else:
                await self._maybe_csv(message)
        except UserError as e:
            await message.reply(f"⚠️ {e}", mention_author=False)
        except Exception:
            log.exception("on_message handling failed")

    async def _maybe_csv(self, message: discord.Message) -> None:
        t = await self.bot.ctx.tournaments.active(message.guild.id)
        if t is None or t.status not in (TournamentStatus.SETUP, TournamentStatus.REGISTRATION):
            return
        allowed = {self.bot.settings.channels.admin, t.setup_channel_id}
        if message.channel.id not in allowed or not is_admin(self.bot, message.author):
            return
        text = None
        for a in message.attachments:
            if a.filename.lower().endswith((".csv", ".txt")):
                text = (await a.read()).decode("utf-8-sig", errors="replace")
                break
        if text is None and looks_like_players_csv(message.content):
            text = message.content
        if text is None:
            return
        await self.bot.ctx.tournaments.preview_players(t.id, message.author.id, text, member_lookup(message.guild))

    async def _maybe_screenshot(self, message: discord.Message) -> None:
        images = [a for a in message.attachments if (a.content_type or "").split(";")[0] in IMAGE_TYPES]
        if not images:
            return
        m = await self.bot.ctx.matches.find_by_thread(message.channel.id)
        if m is None:
            return
        if await self.bot.ctx.matches.attach_screenshot(m.id, message.author.id, images[0].url):
            try:
                await message.add_reaction("📎")
            except discord.HTTPException:
                pass
