"""Upload Aniimo thumbnails and element icons as the bot's application emojis so select menus show them."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import discord

from ..domain.roster import ELEMENT_EMOJI, ELEMENTS, Roster

if TYPE_CHECKING:
    from .client import AniimoBot

log = logging.getLogger(__name__)
EMOJI_SIZE = 128
_NAME_RE = re.compile(r"[^a-z0-9_]")


def emoji_name(slug: str) -> str:
    return ("an_" + _NAME_RE.sub("_", slug))[:32]


def element_emoji_name(element: str) -> str:
    return ("el_" + _NAME_RE.sub("_", element.lower()))[:32]


@dataclass
class _Item:
    key: str  # cache key, e.g. "emberpup" or "el:fire"
    name: str  # emoji name
    png: Callable[[], bytes]


class EmojiStore:
    def __init__(self, bot: AniimoBot):
        self.bot = bot
        self.map: dict[str, discord.PartialEmoji] = {}  # Aniimo slug -> emoji
        self.elements: dict[str, discord.PartialEmoji] = {}  # element -> emoji
        self.cache_path = Path(bot.settings.paths.emoji_cache)
        self._lock = asyncio.Lock()
        self.status = "not synced"

    # ----- lookups used by menus and embeds -----
    def get(self, slug: str) -> discord.PartialEmoji | None:
        return self.map.get(slug)

    def element_emoji(self, element: str) -> discord.PartialEmoji | str | None:
        """The uploaded element icon, or a plain unicode emoji until it is uploaded."""
        return self.elements.get(element) or ELEMENT_EMOJI.get(element)

    def element_text(self, element: str) -> str:
        """Markup that renders the element icon inside message text and embeds."""
        e = self.element_emoji(element)
        return str(e) if e else ""

    def url(self, slug: str) -> str | None:
        e = self.map.get(slug)
        return e.url if e and e.id else None

    # ----- cache + upload -----
    def _load_cache(self) -> dict[str, dict]:
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_cache(self, data: dict[str, dict]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(data, indent=1), encoding="utf-8")

    def _png(self, img) -> bytes:
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue()

    def _items(self, roster: Roster) -> list[_Item]:
        items = [
            _Item(
                f"el:{el.lower()}", element_emoji_name(el), lambda el=el: self._png(self.bot.icons.get(el, EMOJI_SIZE))
            )
            for el in ELEMENTS
        ]
        for a in roster.items:
            items.append(_Item(a.slug, emoji_name(a.slug), lambda a=a: self._png(self.bot.thumbs.get(a, EMOJI_SIZE))))
        return items

    async def sync(self, roster: Roster) -> str:
        """Create/replace emojis whose image changed. Safe to run in the background."""
        if not self.bot.settings.upload_emojis:
            self.status = "disabled in config"
            return self.status
        async with self._lock:
            cache = self._load_cache()
            try:
                existing = {e.name: e for e in await self.bot.fetch_application_emojis()}
            except discord.HTTPException as e:
                self.status = f"couldn't list emojis: {e}"
                log.warning(self.status)
                return self.status
            created = reused = failed = 0
            wanted: set[str] = set()
            for item in self._items(roster):
                wanted.add(item.name)
                data = await asyncio.to_thread(item.png)
                digest = hashlib.sha1(data).hexdigest()
                cur = existing.get(item.name)
                emoji = None
                if cur and cache.get(item.key, {}).get("hash") == digest:
                    emoji = cur
                    reused += 1
                else:
                    try:
                        if cur:
                            await cur.delete()
                        emoji = await self.bot.create_application_emoji(name=item.name, image=data)
                    except discord.HTTPException as e:
                        log.warning("Emoji upload failed for %s: %s", item.key, e)
                        failed += 1
                        continue
                    cache[item.key] = {"hash": digest, "id": emoji.id, "name": emoji.name}
                    created += 1
                    self._save_cache(cache)
                partial = discord.PartialEmoji(name=emoji.name, id=emoji.id)
                if item.key.startswith("el:"):
                    self.elements[item.key[3:].title()] = partial
                else:
                    self.map[item.key] = partial
            # remove emojis for Aniimo no longer in the roster
            for name, e in existing.items():
                if name.startswith(("an_", "el_")) and name not in wanted:
                    try:
                        await e.delete()
                    except discord.HTTPException:
                        pass
            keep = {i.key for i in self._items(roster)}
            self._save_cache({k: v for k, v in cache.items() if k in keep})
            self.status = f"{created} uploaded, {reused} unchanged, {failed} failed"
            log.info("Emoji sync: %s", self.status)
            return self.status
