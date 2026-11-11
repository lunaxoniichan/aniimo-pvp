"""The private (ephemeral) draft picker: element menu -> Aniimo menu -> preview -> Lock in, plus search."""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

import discord

from ...domain.roster import ELEMENT_EMOJI, Aniimo
from ...services.drafts import TurnOptions
from ...services.errors import UserError
from ..util import KIND_LABEL, rel

if TYPE_CHECKING:
    from ..client import AniimoBot

log = logging.getLogger(__name__)
PAGE = 25


class PickerView(discord.ui.View):
    def __init__(
        self,
        bot: AniimoBot,
        opts: TurnOptions,
        *,
        element: str | None = None,
        selected: str | None = None,
        search: list[str] | None = None,
        query: str | None = None,
        page: int = 0,
    ):
        super().__init__(timeout=max(60.0, bot.settings.timers.draft_step_seconds + 30))
        self.bot, self.opts = bot, opts
        self.element, self.selected, self.search, self.query, self.page = element, selected, search, query, page
        avail = opts.available
        elements = [el for el in bot.ctx.roster.elements() if any(a.element == el for a in avail)]
        el_options = [
            discord.SelectOption(
                label=el,
                value=el,
                emoji=ELEMENT_EMOJI.get(el),
                default=el == element,
                description=f"{sum(1 for a in avail if a.element == el)} available",
            )
            for el in elements[:25]
        ]
        if el_options:
            sel = discord.ui.Select(placeholder="Choose an element", options=el_options, row=0)
            sel.callback = self.on_element
            self.add_item(sel)
        pool = self.pool()
        chunk = pool[page * PAGE : (page + 1) * PAGE]
        if chunk:
            options = [
                discord.SelectOption(
                    label=a.name,
                    value=a.slug,
                    default=a.slug == selected,
                    description=(a.element + (f" · {a.role}" if a.role else ""))[:100],
                    emoji=bot.aniimo_emojis.get(a.slug) or ELEMENT_EMOJI.get(a.element),
                )
                for a in chunk
            ]
            sel2 = discord.ui.Select(placeholder="Choose an Aniimo", options=options, row=1)
            sel2.callback = self.on_aniimo
            self.add_item(sel2)
        else:
            dummy = discord.ui.Select(
                placeholder="Choose an Aniimo (pick an element first)",
                disabled=True,
                row=1,
                options=[discord.SelectOption(label="—", value="none")],
            )
            self.add_item(dummy)
        if len(pool) > PAGE:
            prev = discord.ui.Button(label="◀ Prev", disabled=page == 0, row=2)
            nxt = discord.ui.Button(label="Next ▶", disabled=(page + 1) * PAGE >= len(pool), row=2)
            prev.callback, nxt.callback = self.on_prev, self.on_next
            self.add_item(prev)
            self.add_item(nxt)
        chosen = bot.ctx.roster.get(selected)
        kind = "ban" if opts.kind == "ban" else "pick"
        lock = discord.ui.Button(
            label=f"Lock in {chosen.name}" if chosen else f"Lock in {kind}",
            style=discord.ButtonStyle.success,
            emoji="🔒",
            disabled=chosen is None,
            row=3,
        )
        lock.callback = self.on_lock
        self.add_item(lock)
        search_btn = discord.ui.Button(label="Search by name", emoji="🔍", row=3)
        search_btn.callback = self.on_search
        self.add_item(search_btn)
        if search is not None:
            back = discord.ui.Button(label="Back to elements", row=3)
            back.callback = self.on_back
            self.add_item(back)

    def pool(self) -> list[Aniimo]:
        avail = self.opts.available
        if self.search is not None:
            wanted = set(self.search)
            return [a for a in avail if a.slug in wanted]
        if self.element:
            return [a for a in avail if a.element == self.element]
        return []

    def render(self) -> tuple[discord.Embed, list[discord.File]]:
        o = self.opts
        kind = KIND_LABEL[o.kind]
        due = o.deadline
        e = discord.Embed(colour=discord.Colour(0x7FB0FF if o.side == "A" else 0xFFAA70))
        e.description = f"**{kind}** · Step {o.step + 1} of {len(o.state.order)}" + (
            f" · ends {rel(due)}" if due else ""
        )
        lines = ["Choose an element, then an Aniimo. Banned and picked Aniimo are hidden."]
        if o.locked and o.kind == "pick":
            lines.append(f"Fearless: {len(o.locked)} Aniimo you already played are locked for you.")
        if self.search is not None:
            n = len(self.pool())
            lines.append(
                f"{n} match{'es' if n != 1 else ''} for “{self.query}”."
                if n
                else f"No available Aniimo match “{self.query}”."
            )
        e.add_field(name="​", value="\n".join(lines), inline=False)
        files: list[discord.File] = []
        chosen = self.bot.ctx.roster.get(self.selected)
        if chosen:
            e.colour = discord.Colour(int(chosen.color.lstrip("#"), 16))
            e.title = chosen.name
            e.add_field(name="Element", value=f"{ELEMENT_EMOJI.get(chosen.element, '')} {chosen.element}")
            if chosen.role:
                e.add_field(name="Role", value=chosen.role)
            e.set_footer(text="Selected. Click Lock in to confirm.")
            url = self.bot.aniimo_emojis.url(chosen.slug)
            if url:
                e.set_thumbnail(url=url)
            else:
                buf = io.BytesIO()
                self.bot.thumbs.get(chosen, 128).save(buf, "PNG")
                buf.seek(0)
                files.append(discord.File(buf, filename="aniimo.png"))
                e.set_thumbnail(url="attachment://aniimo.png")
        return e, files

    async def _refresh(self, interaction: discord.Interaction, **changes) -> None:
        try:
            opts = await self.bot.ctx.drafts.options(
                self.opts.match_id, self.opts.game_id, self.opts.step, self.opts.actor_id
            )
        except UserError as e:
            await interaction.response.edit_message(content=f"⚠️ {e}", embed=None, view=None, attachments=[])
            return
        state = {
            "element": self.element,
            "selected": self.selected,
            "search": self.search,
            "query": self.query,
            "page": self.page,
        }
        state.update(changes)
        view = PickerView(self.bot, opts, **state)
        embed, files = view.render()
        await interaction.response.edit_message(content=None, embed=embed, view=view, attachments=files)

    async def on_element(self, interaction: discord.Interaction) -> None:
        value = interaction.data["values"][0]  # type: ignore[index]
        await self._refresh(interaction, element=value, selected=None, search=None, query=None, page=0)

    async def on_aniimo(self, interaction: discord.Interaction) -> None:
        value = interaction.data["values"][0]  # type: ignore[index]
        await self._refresh(interaction, selected=value)

    async def on_prev(self, interaction: discord.Interaction) -> None:
        await self._refresh(interaction, page=max(0, self.page - 1))

    async def on_next(self, interaction: discord.Interaction) -> None:
        await self._refresh(interaction, page=self.page + 1)

    async def on_back(self, interaction: discord.Interaction) -> None:
        await self._refresh(interaction, search=None, query=None, page=0, selected=None)

    async def on_search(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(SearchModal(self))

    async def on_lock(self, interaction: discord.Interaction) -> None:
        chosen = self.bot.ctx.roster.get(self.selected)
        if chosen is None:
            await interaction.response.defer()
            return
        await interaction.response.edit_message(
            content=f"Locking in {chosen.name}…", embed=None, view=None, attachments=[]
        )
        o = self.opts
        try:
            await self.bot.ctx.drafts.submit(o.match_id, o.game_id, o.step, o.actor_id, chosen.slug)
        except UserError as e:
            await interaction.edit_original_response(content=f"⚠️ {e}")
            return
        except Exception:
            log.exception("Lock in failed")
            await interaction.edit_original_response(content="⚠️ Something went wrong. Try again or call an admin.")
            return
        verb = "banned" if o.kind == "ban" else "picked"
        await interaction.edit_original_response(content=f"🔒 You {verb} **{chosen.name}**.")
        self.stop()


class SearchModal(discord.ui.Modal, title="Search Aniimo"):
    query = discord.ui.TextInput(label="Name or part of a name", min_length=1, max_length=40, placeholder="e.g. emb")

    def __init__(self, picker: PickerView):
        super().__init__()
        self.picker = picker

    async def on_submit(self, interaction: discord.Interaction) -> None:
        q = str(self.query.value).strip()
        roster = self.picker.bot.ctx.roster
        available = {a.slug for a in self.picker.opts.available}
        found = [a.slug for a in roster.search(q, limit=len(roster)) if a.slug in available][:PAGE]
        await self.picker._refresh(interaction, search=found, query=q, page=0, selected=None, element=None)


async def open_picker(bot: AniimoBot, interaction: discord.Interaction, opts: TurnOptions) -> None:
    view = PickerView(bot, opts)
    embed, files = view.render()
    await interaction.response.send_message(embed=embed, view=view, files=files, ephemeral=True)
