"""Short-lived private views: report winner, rematch type, cancel confirmation, rename."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import discord

from ...services.errors import UserError

if TYPE_CHECKING:
    from ..client import AniimoBot

log = logging.getLogger(__name__)


class ChoiceView(discord.ui.View):
    """Buttons that each run an async action and then replace the message with a result line."""

    def __init__(
        self, choices: list[tuple[str, discord.ButtonStyle, Callable[[], Awaitable[str]]]], timeout: float = 300
    ):
        super().__init__(timeout=timeout)
        for label, style, action in choices:
            btn = discord.ui.Button(label=label[:80], style=style)
            btn.callback = self._make(action)
            self.add_item(btn)

    def _make(self, action):
        async def cb(interaction: discord.Interaction) -> None:
            await interaction.response.edit_message(content="Working…", view=None, embed=None)
            try:
                text = await action()
            except UserError as e:
                text = f"⚠️ {e}"
            except Exception:
                log.exception("Choice action failed")
                text = "⚠️ Something went wrong. An admin can check the bot log."
            await interaction.edit_original_response(content=text)
            self.stop()

        return cb


class RenameModal(discord.ui.Modal, title="Rename tournament"):
    name = discord.ui.TextInput(label="Tournament name", min_length=1, max_length=100)

    def __init__(self, bot: AniimoBot, tournament_id: int, current: str):
        super().__init__()
        self.bot, self.tournament_id = bot, tournament_id
        self.name.default = current

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            await self.bot.ctx.tournaments.set_options(self.tournament_id, interaction.user.id, name=str(self.name))
        except UserError as e:
            await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
