"""Upload Aniimo thumbnails as the bot's application emojis so select menus show icons."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import discord

from ..domain.roster import ELEMENT_EMOJI, Roster

if TYPE_CHECKING:
    from .client import AniimoBot

log = logging.getLogger(__name__)
EMOJI_SIZE = 128
_NAME_RE = re.compile(r"[^a-z0-9_]")


def emoji_name(slug: str) -> str:
    return ("an_" + _NAME_RE.sub("_", slug))[:32]


class EmojiStore:
    def __init__(self, bot: AniimoBot):
        self.bot = bot
        self.map: dict[str, discord.PartialEmoji] = {}
        self.cache_path = Path(bot.settings.paths.emoji_cache)
        self._lock = asyncio.Lock()
        self.status = "not synced"

    def get(self, slug: str) -> discord.PartialEmoji | None:
        return self.map.get(slug)

    def element(self, element: str) -> str | None:
        return ELEMENT_EMOJI.get(element)

    def url(self, slug: str) -> str | None:
        e = self.map.get(slug)
        return e.url if e and e.id else None

    def _load_cache(self) -> dict[str, dict]:
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_cache(self, data: dict[str, dict]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(data, indent=1), encoding="utf-8")

    def _image_bytes(self, roster: Roster, slug: str) -> bytes:
        a = roster.get(slug)
        img = self.bot.thumbs.get(a, EMOJI_SIZE)
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue()

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
            wanted = set()
            for a in roster.items:
                name = emoji_name(a.slug)
                wanted.add(name)
                data = await asyncio.to_thread(self._image_bytes, roster, a.slug)
                digest = hashlib.sha1(data).hexdigest()
                cur = existing.get(name)
                if cur and cache.get(a.slug, {}).get("hash") == digest:
                    self.map[a.slug] = discord.PartialEmoji(name=cur.name, id=cur.id)
                    reused += 1
                    continue
                try:
                    if cur:
                        await cur.delete()
                    emoji = await self.bot.create_application_emoji(name=name, image=data)
                except discord.HTTPException as e:
                    log.warning("Emoji upload failed for %s: %s", a.slug, e)
                    failed += 1
                    continue
                self.map[a.slug] = discord.PartialEmoji(name=emoji.name, id=emoji.id)
                cache[a.slug] = {"hash": digest, "id": emoji.id, "name": emoji.name}
                created += 1
                self._save_cache(cache)
            # remove emojis for Aniimo no longer in the roster
            for name, e in existing.items():
                if name.startswith("an_") and name not in wanted:
                    try:
                        await e.delete()
                    except discord.HTTPException:
                        pass
            self._save_cache({k: v for k, v in cache.items() if emoji_name(k) in wanted})
            self.status = f"{created} uploaded, {reused} unchanged, {failed} failed"
            log.info("Emoji sync: %s", self.status)
            return self.status
