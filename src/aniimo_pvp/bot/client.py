"""The Discord bot: wires settings, database, services, renderers and cogs."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ..config import Settings
from ..db.database import Database, backup_database
from ..domain.roster import load_roster
from ..render.board import BoardRenderer
from ..render.bracket import BracketRenderer
from ..render.elements import ElementIcons
from ..render.fonts import Fonts
from ..render.thumbs import ThumbCache
from ..services.common import JOB_ARCHIVE
from ..services.context import AppContext, build_context
from .components import ALL_DYNAMIC
from .emojis import EmojiStore
from .notifier import DiscordNotifier

log = logging.getLogger(__name__)


class AniimoBot(commands.Bot):
    def __init__(self, settings: Settings):
        intents = discord.Intents.default()
        intents.message_content = True  # read pasted CSV in the admin channel
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            allowed_mentions=discord.AllowedMentions(everyone=False, users=True, roles=True),
        )
        self.settings = settings
        self.fonts = Fonts(settings.paths.fonts_dir)
        self.thumbs = ThumbCache(self.fonts)
        self.icons = ElementIcons(settings.paths.elements_dir, self.fonts)
        self.board = BoardRenderer(self.fonts, self.thumbs, self.icons)
        self.bracket = BracketRenderer(self.fonts)
        self.aniimo_emojis = EmojiStore(self)
        self.ctx: AppContext = None  # type: ignore[assignment]
        self.db: Database | None = None
        self._resynced = False

    async def setup_hook(self) -> None:
        s = self.settings
        backup = backup_database(s.paths.database, s.paths.backups_dir)
        if backup:
            log.info("Database backup: %s", backup)
        self.db = Database(s.paths.database)
        for column in await self.db.create_all():
            log.info("Database upgraded: added %s", column)
        roster, warnings = load_roster(s.paths.roster_csv, s.paths.images_dir)
        for w in warnings:
            log.warning("Roster: %s", w)
        notifier = DiscordNotifier(self)
        self.ctx = build_context(s, self.db, roster, notifier)
        self.ctx.scheduler.register(JOB_ARCHIVE, notifier.archive_job)
        self.add_dynamic_items(*ALL_DYNAMIC)

        from .cogs.admin import AdminCog
        from .cogs.friendly import FriendlyCog
        from .cogs.roster import RosterCog
        from .cogs.stats import StatsCog
        from .cogs.tournament import TournamentCog

        for cog in (TournamentCog(self), AdminCog(self), StatsCog(self), RosterCog(self), FriendlyCog(self)):
            await self.add_cog(cog)
        if not s.dev_mode:
            self.tree.remove_command("duel-test")  # only offered while testing
        if not s.channels.friendly:
            log.info("Friendly duels are off: set channels.friendly in config.yaml to turn them on")

        guild = discord.Object(id=s.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info("Synced %d slash commands to guild %s", len(synced), s.guild_id)
        recovered = await self.ctx.scheduler.recover()
        if recovered:
            log.info("Restarted %d timers that were due while the bot was offline", recovered)
        self.ctx.scheduler.start()

    async def on_ready(self) -> None:
        log.info(
            "Logged in as %s (%s). Dev mode: %s", self.user, self.user.id if self.user else "?", self.settings.dev_mode
        )
        guild = self.get_guild(self.settings.guild_id)
        if guild is None:
            log.error("The bot isn't in guild %s. Invite it with the URL from the README.", self.settings.guild_id)
        if not self._resynced:
            self._resynced = True
            asyncio.create_task(self.aniimo_emojis.sync(self.ctx.roster))
            asyncio.create_task(self.ctx.notifier.resync())  # type: ignore[attr-defined]

    async def close(self) -> None:
        if self.ctx and self.ctx.scheduler:
            await self.ctx.scheduler.stop()
        if self.db:
            await self.db.dispose()
        await super().close()
