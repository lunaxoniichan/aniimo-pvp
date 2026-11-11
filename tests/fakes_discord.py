"""Minimal fakes of Discord objects so the bot layer can run without a connection."""

from __future__ import annotations

import itertools
from types import SimpleNamespace

import discord

_ids = itertools.count(10_000)


class FakeMessage:
    def __init__(self, channel, content=None, **kwargs):
        self.id = next(_ids)
        self.channel = channel
        self.content = content
        self.kwargs = kwargs
        self.edits: list[dict] = []
        self.deleted = False
        self.embeds = [kwargs["embed"]] if kwargs.get("embed") else list(kwargs.get("embeds") or [])
        self.reactions: list[str] = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)
        if "embed" in kwargs and kwargs["embed"] is not None:
            self.embeds = [kwargs["embed"]]
        return self

    async def delete(self):
        self.deleted = True

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)

    async def reply(self, content, **kwargs):
        return await self.channel.send(content, **kwargs)


class FakePartial:
    def __init__(self, channel, message_id):
        self.channel, self.id = channel, message_id

    def _msg(self):
        for m in self.channel.messages:
            if m.id == self.id:
                return m
        raise discord.NotFound(SimpleNamespace(status=404, reason="nf"), "Unknown Message")

    async def edit(self, **kwargs):
        return await self._msg().edit(**kwargs)

    async def delete(self):
        await self._msg().delete()


class FakeChannel:
    def __init__(self, bot, cid: int, name: str):
        self.bot, self.id, self.name = bot, cid, name
        self.messages: list[FakeMessage] = []
        self.threads: list[FakeThread] = []

    async def send(self, content=None, **kwargs):
        m = FakeMessage(self, content, **kwargs)
        self.messages.append(m)
        return m

    def get_partial_message(self, mid):
        return FakePartial(self, mid)

    async def create_thread(self, *, name, type=None, invitable=True, auto_archive_duration=1440, **_):
        th = FakeThread(self.bot, next(_ids), name)
        th.private = type == discord.ChannelType.private_thread
        self.threads.append(th)
        self.bot.channels[th.id] = th
        return th

    def live(self):
        return [m for m in self.messages if not m.deleted]


class FakeThread(FakeChannel):
    def __init__(self, bot, cid, name):
        super().__init__(bot, cid, name)
        self.members: list[int] = []
        self.archived = False
        self.private = True

    async def add_user(self, obj):
        self.members.append(obj.id)

    async def edit(self, **kwargs):
        self.archived = kwargs.get("archived", self.archived)


class FakeEmojis:
    status = "disabled in tests"

    def get(self, slug):
        return None

    def url(self, slug):
        return None


class FakeBot:
    def __init__(self, settings):
        from aniimo_pvp.render.board import BoardRenderer
        from aniimo_pvp.render.bracket import BracketRenderer
        from aniimo_pvp.render.fonts import Fonts
        from aniimo_pvp.render.thumbs import ThumbCache

        self.settings = settings
        self.fonts = Fonts()
        self.thumbs = ThumbCache(self.fonts)
        self.board = BoardRenderer(self.fonts, self.thumbs)
        self.bracket = BracketRenderer(self.fonts)
        self.aniimo_emojis = FakeEmojis()
        self.channels: dict[int, FakeChannel] = {}
        self.ctx = None
        self.user = SimpleNamespace(id=1)
        ch = settings.channels
        for cid, name in (
            (ch.admin, "admin"),
            (ch.announce, "announce"),
            (ch.matches, "matches"),
            (ch.live_draft, "live-draft"),
        ):
            self.channels[cid] = FakeChannel(self, cid, name)

    def get_channel(self, cid):
        return self.channels.get(cid)

    async def fetch_channel(self, cid):
        if cid in self.channels:
            return self.channels[cid]
        raise discord.NotFound(SimpleNamespace(status=404, reason="nf"), "Unknown Channel")

    def get_guild(self, gid):
        return None

    def ch(self, name: str) -> FakeChannel:
        return next(c for c in self.channels.values() if c.name == name)


class FakeResponse:
    def __init__(self, inter):
        self.inter = inter
        self._done = False

    def is_done(self):
        return self._done

    async def send_message(self, content=None, **kwargs):
        self._done = True
        self.inter.sent.append(("send", content, kwargs))

    async def defer(self, **kwargs):
        self._done = True
        self.inter.sent.append(("defer", None, kwargs))

    async def edit_message(self, **kwargs):
        self._done = True
        self.inter.sent.append(("edit", kwargs.get("content"), kwargs))

    async def send_modal(self, modal):
        self._done = True
        self.inter.sent.append(("modal", None, {"modal": modal}))


class FakeFollowup:
    def __init__(self, inter):
        self.inter = inter

    async def send(self, content=None, **kwargs):
        self.inter.sent.append(("followup", content, kwargs))


class FakeInteraction:
    def __init__(self, bot, user_id: int, *, data=None, message=None, channel=None):
        self.client = bot
        self.user = SimpleNamespace(id=user_id, display_name=f"user{user_id}", roles=[])
        self.data = data or {}
        self.message = message
        self.channel = channel
        self.guild_id = bot.settings.guild_id
        self.sent: list[tuple] = []
        self.response = FakeResponse(self)
        self.followup = FakeFollowup(self)

    async def edit_original_response(self, **kwargs):
        self.sent.append(("edit_original", kwargs.get("content"), kwargs))

    def texts(self) -> list[str]:
        return [c for _, c, _ in self.sent if c]

    def last_view(self):
        for _, _, kw in reversed(self.sent):
            if kw.get("view") is not None:
                return kw["view"]
        return None
