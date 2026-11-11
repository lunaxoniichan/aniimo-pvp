"""Persistent components. Custom IDs encode the action + ids, so buttons survive restarts."""

from __future__ import annotations

import re

import discord
from discord.ui import Button, DynamicItem, Select


def match_custom_id(action: str, match_id: int, arg: str | None = None) -> str:
    return f"ap:{action}:{match_id}" + (f":{arg}" if arg else "")


class MatchButton(DynamicItem[Button], template=r"ap:(?P<action>[a-z_]+):(?P<mid>\d+)(?::(?P<arg>[\w\-]+))?"):
    def __init__(
        self,
        action: str,
        match_id: int,
        arg: str | None = None,
        *,
        label: str | None = None,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        emoji: str | None = None,
        disabled: bool = False,
    ):
        super().__init__(
            Button(
                label=label,
                style=style,
                emoji=emoji,
                disabled=disabled,
                custom_id=match_custom_id(action, match_id, arg),
            )
        )
        self.action, self.match_id, self.arg = action, match_id, arg

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: Button, match: re.Match[str], /):
        return cls(
            match["action"],
            int(match["mid"]),
            match["arg"],
            label=item.label,
            style=item.style,
            emoji=item.emoji,
            disabled=item.disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from .handlers import handle_match_action

        await handle_match_action(interaction, self.action, self.match_id, self.arg)


class TournamentButton(DynamicItem[Button], template=r"at:(?P<action>[a-z_]+):(?P<tid>\d+)(?::(?P<arg>[\w\-]+))?"):
    def __init__(
        self,
        action: str,
        tournament_id: int,
        arg: str | None = None,
        *,
        label: str | None = None,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        emoji: str | None = None,
        disabled: bool = False,
    ):
        cid = f"at:{action}:{tournament_id}" + (f":{arg}" if arg else "")
        super().__init__(Button(label=label, style=style, emoji=emoji, disabled=disabled, custom_id=cid))
        self.action, self.tournament_id, self.arg = action, tournament_id, arg

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: Button, match: re.Match[str], /):
        return cls(
            match["action"],
            int(match["tid"]),
            match["arg"],
            label=item.label,
            style=item.style,
            emoji=item.emoji,
            disabled=item.disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from .handlers import handle_tournament_action

        await handle_tournament_action(interaction, self.action, self.tournament_id, self.arg)


class SetupSelect(DynamicItem[Select], template=r"as:(?P<field>cap|fmt|fear):(?P<tid>\d+)"):
    def __init__(
        self,
        field: str,
        tournament_id: int,
        options: list[discord.SelectOption],
        placeholder: str | None = None,
        disabled: bool = False,
    ):
        super().__init__(
            Select(
                custom_id=f"as:{field}:{tournament_id}",
                options=options,
                placeholder=placeholder,
                min_values=1,
                max_values=1,
                disabled=disabled,
            )
        )
        self.field, self.tournament_id = field, tournament_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: Select, match: re.Match[str], /):
        return cls(match["field"], int(match["tid"]), list(item.options), item.placeholder, item.disabled)

    async def callback(self, interaction: discord.Interaction) -> None:
        from .handlers import handle_setup_select

        await handle_setup_select(interaction, self.field, self.tournament_id, self.item.values[0])


def persistent_view(*items: DynamicItem) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for item in items:
        view.add_item(item)
    return view


ALL_DYNAMIC = (MatchButton, TournamentButton, SetupSelect)
