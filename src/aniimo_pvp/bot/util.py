"""Small Discord helpers shared by the bot modules."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

import discord

from ..clock import unix

if TYPE_CHECKING:
    from .client import AniimoBot

log = logging.getLogger(__name__)

PURPLE = discord.Colour(0x8B7CF0)
AMBER = discord.Colour(0xE5A83A)
GREEN = discord.Colour(0x3DBE6B)
RED = discord.Colour(0xE5484D)
GOLD = discord.Colour(0xE5B53A)
BLUE = discord.Colour(0x4C8DFF)
ORANGE = discord.Colour(0xFF8A3D)

KIND_LABEL = {"ban": "BAN", "pick": "PICK"}


def rel(dt: datetime | None) -> str:
    return f"<t:{unix(dt)}:R>" if dt else ""


def clock_time(dt: datetime | None) -> str:
    return f"<t:{unix(dt)}:t>" if dt else ""


def is_admin(bot: AniimoBot, user: discord.abc.User) -> bool:
    if not isinstance(user, discord.Member):
        guild = bot.get_guild(bot.settings.guild_id)
        user = guild.get_member(user.id) if guild else user  # type: ignore[assignment]
    if isinstance(user, discord.Member):
        if user.guild_permissions.administrator:
            return True
        return any(r.id == bot.settings.admin_role_id for r in user.roles)
    return False


def who(player, *, mention: bool = True) -> str:
    """Mention a real player; fake (dev) players are shown by name."""
    if player is None:
        return "TBD"
    if mention and not getattr(player, "is_fake", False):
        return f"<@{player.id}>"
    return f"**{player.name}**"


def admin_role_mention(bot: AniimoBot) -> str:
    return f"<@&{bot.settings.admin_role_id}>" if bot.settings.admin_role_id else "admins"


async def resolve_channel(bot: AniimoBot, channel_id: int | None) -> discord.abc.Messageable | None:
    if not channel_id:
        return None
    ch = bot.get_channel(channel_id)
    if ch is None:
        try:
            ch = await bot.fetch_channel(channel_id)
        except discord.HTTPException:
            log.warning("Channel %s not found", channel_id)
            return None
    return ch  # type: ignore[return-value]


async def delete_message(channel, message_id: int | None) -> None:
    if not channel or not message_id:
        return
    try:
        await channel.get_partial_message(message_id).delete()
    except discord.HTTPException:
        pass


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
