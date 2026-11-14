"""Turns service events into Discord messages (threads, panels, turn messages, live feed, alerts)."""

from __future__ import annotations

import asyncio
import io
import logging
from typing import TYPE_CHECKING, Any

import discord
from sqlalchemy import select

from ..db.models import Entry, Match
from ..domain import formats
from ..domain.states import TournamentKind
from ..services.common import JOB_CHALLENGE_EXPIRE
from ..services.notifier import Notifier
from ..services.views import MatchView, PlayerInfo, load_bracket, load_match_view
from . import panels
from .util import admin_role_mention, delete_message, rel, resolve_channel, truncate, who

if TYPE_CHECKING:
    from .client import AniimoBot

log = logging.getLogger(__name__)
MENTIONS = discord.AllowedMentions(everyone=False, users=True, roles=True)
LIVE_DELAY = 1.0  # seconds to coalesce live-feed edits


class DiscordNotifier(Notifier):
    def __init__(self, bot: AniimoBot):
        self.bot = bot
        self._live_pending: dict[int, asyncio.Task] = {}
        self._status_pending: dict[int, asyncio.Task] = {}

    @property
    def ctx(self):
        return self.bot.ctx

    # ---------------- dispatch ----------------
    async def tournament_event(self, tournament_id: int, event: str, **info: Any) -> None:
        fn = getattr(self, f"t_{event}", None)
        if fn:
            await fn(tournament_id, **info)

    async def match_event(self, match_id: int, event: str, **info: Any) -> None:
        fn = getattr(self, f"m_{event}", None)
        if fn:
            await fn(match_id, **info)
        if event in (
            "opened",
            "side_choice",
            "draft_complete",
            "between_games",
            "done",
            "noshow",
            "disputed",
            "reported",
            "playing",
        ):
            m = await self.ctx.matches.get(match_id)
            self.schedule_status(m.tournament_id)

    # ---------------- channels ----------------
    async def admin_channel(self):
        return await resolve_channel(self.bot, self.bot.settings.channels.admin)

    async def announce_channel(self):
        return await resolve_channel(self.bot, self.bot.settings.channels.announce)

    async def live_channel(self):
        return await resolve_channel(self.bot, self.bot.settings.channels.live_draft)

    async def setup_channel(self, t):
        return await resolve_channel(self.bot, t.setup_channel_id) or await self.admin_channel()

    async def thread(self, m: Match):
        return await resolve_channel(self.bot, m.thread_id)

    async def system(self, m: Match, text: str) -> None:
        th = await self.thread(m)
        if th:
            await th.send(f"-# {text}", allowed_mentions=discord.AllowedMentions.none())

    async def render_board(self, v: MatchView) -> discord.File:
        png = await asyncio.to_thread(self.bot.board.render, v)
        return discord.File(io.BytesIO(png), filename="board.png")

    async def render_bracket(self, tournament_id: int, highlight: int | None = None) -> discord.File:
        t, rounds = await load_bracket(self.ctx, tournament_id)
        png = await asyncio.to_thread(self.bot.bracket.render, t.name, t.format, rounds, highlight)
        return discord.File(io.BytesIO(png), filename="bracket.png")

    # ---------------- tournament events ----------------
    async def _edit_setup(self, tid: int) -> None:
        t = await self.ctx.tournaments.get(tid)
        ch = await self.setup_channel(t)
        if ch and t.setup_message_id:
            try:
                await ch.get_partial_message(t.setup_message_id).edit(
                    embed=panels.setup_embed(t), view=panels.setup_view(t)
                )
            except discord.HTTPException as e:
                log.warning("Couldn't edit setup panel: %s", e)

    async def t_setup_changed(self, tid: int) -> None:
        await self._edit_setup(tid)

    async def t_registration_opened(self, tid: int) -> None:
        await self._edit_setup(tid)
        t = await self.ctx.tournaments.get(tid)
        ch = await self.setup_channel(t)
        if ch:
            await ch.send(embed=panels.step2_embed(t))

    async def t_preview(self, tid: int, preview) -> None:
        t = await self.ctx.tournaments.get(tid)
        ch = await self.setup_channel(t)
        if not ch:
            return
        if t.preview_message_id:
            try:
                await ch.get_partial_message(t.preview_message_id).edit(view=None)
            except discord.HTTPException:
                pass
        msg = await ch.send(embed=panels.preview_embed(t, preview), view=panels.preview_view(t, preview))
        await self.ctx.tournaments.set_message_ids(tid, preview_message_id=msg.id)

    async def t_players_confirmed(self, tid: int, count: int) -> None:
        t = await self.ctx.tournaments.get(tid)
        ch = await self.setup_channel(t)
        if ch and t.preview_message_id:
            try:
                await ch.get_partial_message(t.preview_message_id).edit(
                    embed=panels.confirmed_embed(t, count), view=panels.start_view(t)
                )
                return
            except discord.HTTPException:
                pass
        if ch:
            await ch.send(embed=panels.confirmed_embed(t, count), view=panels.start_view(t))

    async def t_started(self, tid: int, seed: str) -> None:
        await self._edit_setup(tid)
        t = await self.ctx.tournaments.get(tid)
        entries = await self.ctx.tournaments.entries(tid)
        fmt = formats.FORMATS[t.format]
        plan = formats.plan(t.format, t.bracket_size or 2)
        ann = await self.announce_channel()
        if ann:
            text = (
                f"The **{t.name}** bracket is live. {len(entries)} players · {fmt.label} format: "
                + ", ".join(f"{r.name} Bo{r.bo}" for r in plan)
                + "."
            )
            await ann.send(text, file=await self.render_bracket(tid))
        admin = await self.admin_channel()
        if admin:
            await admin.send(
                embed=discord.Embed(
                    title=f"{t.name} has started",
                    colour=panels.GREEN,
                    description=f"Players shuffled (seed {seed}). Bracket posted in the announce channel. "
                    "Match threads are opening now.",
                )
            )

    async def _round_rows(self, tid: int, round_no: int):
        rows = []
        for m in await self.ctx.tournaments.matches(tid, round_no):
            v = await load_match_view(self.ctx, m.id)
            rows.append((m, v.p1, v.p2))
        return rows

    async def t_round_opened(self, tid: int, round: int, match_ids: list[int]) -> None:
        t = await self.ctx.tournaments.get(tid)
        if t.kind == TournamentKind.FRIENDLY:
            return  # a duel isn't announced; its card in the friendly channel links the room
        ann = await self.announce_channel()
        if ann:
            rows = await self._round_rows(tid, round)
            embeds = panels.round_table_embeds(t, round, rows)
            if round > 1:
                await ann.send(
                    f"**Round {round}** of {t.name} is starting.",
                    file=await self.render_bracket(tid),
                    embeds=embeds,
                    allowed_mentions=MENTIONS,
                )
            else:
                await ann.send(embeds=embeds, allowed_mentions=MENTIONS)
        self.schedule_status(tid)

    async def t_round_complete(self, tid: int, round: int, next_round: int) -> None:
        t = await self.ctx.tournaments.get(tid)
        ann = await self.announce_channel()
        if ann:
            await ann.send(
                f"Round {round} of **{t.name}** is complete. Updated bracket:", file=await self.render_bracket(tid)
            )
        admin = await self.admin_channel()
        if admin:
            await admin.send(embed=panels.round_complete_embed(t, round), view=panels.next_round_view(t, next_round))

    async def t_finished(self, tid: int, champion_id: int | None) -> None:
        t = await self.ctx.tournaments.get(tid)
        await self._edit_setup(tid)
        champion = None
        if champion_id:
            async with self.ctx.db.session() as s:
                e = await s.scalar(select(Entry).where(Entry.tournament_id == tid, Entry.discord_id == champion_id))
                champion = PlayerInfo(champion_id, e.display_name if e else str(champion_id), "")
        summary = await self.ctx.stats.summary(tid)
        embed = panels.champion_embed(t, champion, summary, self.ctx.roster.name)
        ann = await self.announce_channel()
        if ann:
            mention = f"<@{champion_id}>" if champion_id else ""
            await ann.send(
                f"{mention} is the champion of **{t.name}**! 🏆".strip(),
                embed=embed,
                file=await self.render_bracket(tid),
                allowed_mentions=MENTIONS,
            )
        admin = await self.admin_channel()
        if admin:
            await admin.send(f"{t.name} is finished. Results and stats are in the announce channel.")
        self.schedule_status(tid)

    async def t_cancelled(self, tid: int) -> None:
        t = await self.ctx.tournaments.get(tid)
        await self._edit_setup(tid)
        for ch in (await self.admin_channel(), await self.announce_channel()):
            if ch:
                await ch.send(f"**{t.name}** was cancelled by an admin.")

    # ---------------- friendly duels ----------------
    async def edit_duel_card(self, tid: int, **kwargs: Any) -> None:
        t = await self.ctx.tournaments.get(tid)
        if not (t.setup_channel_id and t.setup_message_id):
            return
        matches = await self.ctx.tournaments.matches(tid)
        if not matches:
            return
        v = await load_match_view(self.ctx, matches[0].id)
        jobs = await self.ctx.scheduler.pending(JOB_CHALLENGE_EXPIRE, tid)
        _, embed, view = panels.duel_card(v, due=jobs[0].due_at if jobs else None, **kwargs)
        ch = await resolve_channel(self.bot, t.setup_channel_id)
        if ch is None:
            return
        try:  # the ping stays as it was; only the card and its buttons change
            await ch.get_partial_message(t.setup_message_id).edit(embed=embed, view=view)
        except discord.HTTPException as e:
            log.warning("Couldn't edit duel card: %s", e)

    async def t_duel_accepted(self, tid: int, match_id: int) -> None:
        await self.edit_duel_card(tid)

    async def t_duel_declined(self, tid: int, match_id: int, by: int | None) -> None:
        await self.edit_duel_card(tid, outcome="declined")

    async def t_duel_cancelled(self, tid: int, match_id: int, by: int | None) -> None:
        await self.edit_duel_card(tid, outcome="cancelled")

    async def t_duel_expired(self, tid: int, match_id: int, by: int | None) -> None:
        await self.edit_duel_card(tid, outcome="expired")

    async def t_duel_ended(self, tid: int, match_id: int, drafts: int, by: int | None, reason: str) -> None:
        await self.edit_duel_card(tid, outcome=reason, drafts=drafts)
        v = await load_match_view(self.ctx, match_id)
        n = f"{drafts} draft{'s' if drafts != 1 else ''}"
        text = {
            "ended": f"🏁 {v.name(by) if by in (v.match.p1_id, v.match.p2_id) else 'An admin'} ended the duel "
            f"after {n}. GG!",
            "idle": f"🏁 The duel closed after {n}: nobody started a new draft for a while.",
            "noshow": "🏁 The duel closed because you weren't both ready in time.",
        }.get(reason, f"🏁 The duel is over after {n}.")
        th = await self.thread(v.match)
        if th:
            await th.send(
                f"{text}\n-# This thread is archived in a few minutes.", allowed_mentions=discord.AllowedMentions.none()
            )

    # ---------------- live status ----------------
    def schedule_status(self, tid: int) -> None:
        if not self.bot.settings.channels.live_draft:
            return
        task = self._status_pending.get(tid)
        if task and not task.done():
            return
        self._status_pending[tid] = asyncio.create_task(self._update_status(tid))

    async def _update_status(self, tid: int) -> None:
        await asyncio.sleep(LIVE_DELAY * 2)
        try:
            t = await self.ctx.tournaments.get(tid)
            ch = await self.live_channel()
            if not ch or not t.current_round or t.kind == TournamentKind.FRIENDLY:
                return
            rows = await self._round_rows(tid, t.current_round)
            embed = panels.live_status_embed(t, rows)
            if t.live_status_message_id:
                try:
                    await ch.get_partial_message(t.live_status_message_id).edit(embed=embed)
                    return
                except discord.NotFound:
                    pass
            msg = await ch.send(embed=embed)
            await self.ctx.tournaments.set_message_ids(tid, live_status_message_id=msg.id)
        except Exception:
            log.exception("Live status update failed")

    def schedule_live_board(self, mid: int) -> None:
        if not self.bot.settings.channels.live_draft:
            return
        task = self._live_pending.get(mid)
        if task and not task.done():
            return
        self._live_pending[mid] = asyncio.create_task(self._update_live_board(mid))

    async def _update_live_board(self, mid: int) -> None:
        await asyncio.sleep(LIVE_DELAY)
        try:
            v = await load_match_view(self.ctx, mid)
            ch = await self.live_channel()
            if not ch or v.game is None or v.draft is None:
                return
            tp = v.turn_player
            if v.turn:
                status = (
                    f"Game {v.game.number} · step {v.draft.step + 1} of {len(v.order)} · "
                    f"{tp.name if tp else '?'} is {'banning' if v.turn[1] == 'ban' else 'picking'}"
                )
                head = "🔴 **LIVE**"
            else:
                status = f"Game {v.game.number} · draft complete"
                head = "✅"
            content = f"{head} **{v.label} · {v.p1.name} vs {v.p2.name}** · {status}"
            file = await self.render_board(v)
            live_id = v.match.live_message_id
            if live_id and v.draft.step > 0:
                try:
                    await ch.get_partial_message(live_id).edit(content=content, attachments=[file])
                    return
                except discord.NotFound:
                    pass
            msg = await ch.send(content, file=file)
            await self.ctx.matches.set_message_ids(mid, live_message_id=msg.id)
        except Exception:
            log.exception("Live board update failed")

    # ---------------- match room ----------------
    async def m_opened(self, mid: int) -> None:
        v = await load_match_view(self.ctx, mid)
        if v.is_friendly:  # the room opens next to the challenge, in the friendly channel
            parent_id = v.tournament.setup_channel_id or self.bot.settings.channels.friendly
        else:
            parent_id = self.bot.settings.channels.matches
        parent = await resolve_channel(self.bot, parent_id)
        if parent is None:
            log.error("Channel %s not found; can't open match %s", parent_id, mid)
            return
        name = truncate(f"{v.label} · {v.p1.name} vs {v.p2.name}", 100)
        try:
            thread = await parent.create_thread(
                name=name,
                type=discord.ChannelType.private_thread,
                invitable=v.is_friendly,  # friends may bring spectators; tournament rooms stay closed
                auto_archive_duration=1440,
            )
        except discord.HTTPException as e:
            log.warning("Private thread failed (%s); falling back to a public thread", e)
            thread = await parent.create_thread(
                name=name, type=discord.ChannelType.public_thread, auto_archive_duration=1440
            )
            admin = await self.admin_channel()
            if admin:
                await admin.send(
                    f"⚠️ Couldn't create a private thread for {v.label} ({e}); a public one was used. "
                    "Check the bot's Create Private Threads permission in the matches channel."
                )
        for p in (v.p1, v.p2):
            if p and not p.is_fake:
                try:
                    await thread.add_user(discord.Object(id=p.id))
                except discord.HTTPException as e:
                    log.warning("Couldn't add %s to thread: %s", p.id, e)
        await self.ctx.matches.set_message_ids(mid, thread_id=thread.id)
        content, embed = panels.uid_card(v, self.bot)
        await thread.send(content, embed=embed, allowed_mentions=MENTIONS)
        await self.post_panel(mid, ping=True)
        await asyncio.sleep(LIVE_DELAY * 0.3)  # pace thread creation for big rounds

    async def post_panel(self, mid: int, *, ping: bool = True) -> None:
        """Replace the control panel with a fresh message at the bottom (pings the player who must act)."""
        v = await load_match_view(self.ctx, mid)
        th = await self.thread(v.match)
        if not th:
            return
        content, embed, view = panels.panel(v, self.bot)
        await delete_message(th, v.match.panel_message_id)
        kwargs = {"embed": embed, "allowed_mentions": MENTIONS if ping else discord.AllowedMentions.none()}
        if view is not None:
            kwargs["view"] = view
        msg = await th.send(content if ping else None, **kwargs)
        await self.ctx.matches.set_message_ids(mid, panel_message_id=msg.id)

    async def edit_panel(self, mid: int) -> None:
        v = await load_match_view(self.ctx, mid)
        th = await self.thread(v.match)
        if not th or not v.match.panel_message_id:
            return await self.post_panel(mid)
        _, embed, view = panels.panel(v, self.bot)
        try:
            await th.get_partial_message(v.match.panel_message_id).edit(embed=embed, view=view)
        except discord.NotFound:
            await self.post_panel(mid)

    async def post_turn(self, mid: int) -> None:
        v = await load_match_view(self.ctx, mid)
        th = await self.thread(v.match)
        if not th or v.game is None:
            return
        await delete_message(th, v.match.turn_message_id)
        content, view = panels.turn_message(v, self.bot)
        kwargs: dict[str, Any] = {"file": await self.render_board(v), "allowed_mentions": MENTIONS}
        if view is not None:
            kwargs["view"] = view
        msg = await th.send(content, **kwargs)
        await self.ctx.matches.set_message_ids(mid, turn_message_id=msg.id)
        if not v.is_friendly:  # duels have no live feed
            self.schedule_live_board(mid)

    async def m_ready(self, mid: int, player_id: int) -> None:
        await self.edit_panel(mid)

    async def m_noshow_reminder(self, mid: int, missing: list[int]) -> None:
        v = await load_match_view(self.ctx, mid)
        th = await self.thread(v.match)
        if th:
            names = " ".join(who(v.player(p)) for p in missing)
            due = v.due.get("noshow_escalate")
            when = rel(due) if due else "soon"
            then = f"The duel closes {when}" if v.is_friendly else f"An admin decides {when}"
            await th.send(
                f"⏰ {names} reminder: click **Ready** in the panel above. {then} if you don't.",
                allowed_mentions=MENTIONS,
            )

    async def m_noshow(self, mid: int, missing: list[int]) -> None:
        v = await load_match_view(self.ctx, mid)
        await self.post_panel(mid, ping=False)
        admin = await self.admin_channel()
        if admin:
            embed, view = panels.admin_noshow(v, missing, self.bot)
            await admin.send(
                f"{admin_role_mention(self.bot)} a player hasn't shown up.",
                embed=embed,
                view=view,
                allowed_mentions=MENTIONS,
            )

    async def m_extended(self, mid: int, minutes: float, by: int) -> None:
        m = await self.ctx.matches.get(mid)
        await self.system(m, f"An admin extended the ready check by {int(minutes)} minutes.")
        await self.post_panel(mid)

    async def m_side_choice(self, mid: int, **info) -> None:
        v = await load_match_view(self.ctx, mid)
        th = await self.thread(v.match)
        if th and v.match.turn_message_id:
            await delete_message(th, v.match.turn_message_id)
            await self.ctx.matches.set_message_ids(mid, turn_message_id=None)
        await self.post_panel(mid)

    async def m_side_chosen(self, mid: int, game_id: int, chooser_id: int, side: str, auto: bool) -> None:
        v = await load_match_view(self.ctx, mid)
        chooser, other = v.player(chooser_id), v.opponent(chooser_id)
        other_side = "B" if side == "A" else "A"
        first = chooser if side == "A" else other
        if auto:
            text = f"{chooser.name} didn't choose in time, so {chooser.name} is Side A and bans first."
        else:
            text = f"{chooser.name} took Side {side}. {other.name} is Side {other_side}. {first.name} bans first."
        await self.system(v.match, text + " The draft starts now.")
        await self.edit_panel(mid)

    async def m_turn(self, mid: int, **info) -> None:
        await self.post_turn(mid)

    async def m_action(
        self, mid: int, step: int, kind: str, slug: str | None, auto: bool, player_id: int | None, **info
    ) -> None:
        if not auto:
            return
        v = await load_match_view(self.ctx, mid)
        name = v.name(player_id)
        if slug:
            text = f"⏱️ {name} ran out of time on step {step + 1}, so {self.ctx.roster.name(slug)} was picked at random."
        else:
            text = f"⏱️ {name} ran out of time on step {step + 1}, so no Aniimo was banned."
        await self.system(v.match, text)

    async def m_draft_complete(self, mid: int, game_id: int) -> None:
        await self.post_turn(mid)  # final board, no buttons
        m = await self.ctx.matches.get(mid)
        t = await self.ctx.tournaments.get(m.tournament_id)
        if t.kind == TournamentKind.FRIENDLY:  # no live feed keeps it, so the board stays in the room as history
            await self.ctx.matches.set_message_ids(mid, turn_message_id=None)
        await self.post_panel(mid)

    async def m_paused(self, mid: int, **info) -> None:
        m = await self.ctx.matches.get(mid)
        await self.system(m, "⏸️ An admin paused the draft. The timer is stopped.")
        await self.post_turn(mid)
        await self.edit_panel(mid)

    async def m_resumed(self, mid: int, **info) -> None:
        m = await self.ctx.matches.get(mid)
        await self.system(m, "▶️ An admin resumed the draft. The current step gets a full timer.")
        await self.edit_panel(mid)

    async def m_undo(self, mid: int, step: int, slug: str | None, **info) -> None:
        m = await self.ctx.matches.get(mid)
        what = self.ctx.roster.name(slug) if slug else "a skipped ban"
        await self.system(m, f"↩️ An admin undid step {step + 1} ({what}).")

    async def m_reported(self, mid: int, **info) -> None:
        await self.post_panel(mid)

    async def m_screenshot(self, mid: int, **info) -> None:
        m = await self.ctx.matches.get(mid)
        if m.state == "confirming":
            await self.edit_panel(mid)

    async def m_disputed(self, mid: int, **info) -> None:
        v = await load_match_view(self.ctx, mid)
        await self.post_panel(mid, ping=False)
        admin = await self.admin_channel()
        if admin:
            embed, view = panels.admin_dispute(v)
            await admin.send(
                f"{admin_role_mention(self.bot)} a result is disputed.",
                embed=embed,
                view=view,
                allowed_mentions=MENTIONS,
            )

    async def m_game_confirmed(self, mid: int, game_id: int, winner_id: int, reason: str, score, **info) -> None:
        v = await load_match_view(self.ctx, mid)
        g = next((x for x in v.games if x.id == game_id), None)
        how = {"auto": " (auto-confirmed)", "admin": " (set by an admin)"}.get(reason, "")
        await self.system(
            v.match,
            f"✅ Game {g.number if g else '?'} confirmed{how} · {v.name(winner_id)} wins · "
            f"{v.p1.name} {score[0]}–{score[1]} {v.p2.name}",
        )

    async def m_between_games(self, mid: int, **info) -> None:
        await self.post_panel(mid)

    async def m_rematch_offered(self, mid: int, **info) -> None:
        await self.post_panel(mid)

    async def m_rematch_declined(self, mid: int, by: int, **info) -> None:
        v = await load_match_view(self.ctx, mid)
        await self.system(v.match, f"The rematch requested by {v.name(by)} was declined.")
        await self.post_panel(mid, ping=False)

    async def m_rematch_expired(self, mid: int, by: int) -> None:
        v = await load_match_view(self.ctx, mid)
        await self.system(v.match, f"The rematch offer from {v.name(by)} expired.")
        await self.post_panel(mid, ping=False)

    async def m_rematch_accepted(self, mid: int, mode: str, number: int, attempt: int, reason: str, **info) -> None:
        m = await self.ctx.matches.get(mid)
        how = "with the same teams" if mode == "same_teams" else "with a new draft"
        who_ = "An admin" if reason == "admin_replay" else "Both players agreed:"
        await self.system(
            m,
            f"🔁 {who_} Game {number} is replayed {how} (attempt {attempt}). "
            "The voided attempt doesn't count toward the score or stats.",
        )

    async def m_playing(self, mid: int, **info) -> None:
        await self.post_turn(mid)
        await self.post_panel(mid)

    async def m_call_admin(self, mid: int, by: int, note: str | None = None) -> None:
        v = await load_match_view(self.ctx, mid)
        th = await self.thread(v.match)
        if th:
            await th.send(f"🆘 {admin_role_mention(self.bot)} was called by {v.name(by)}.", allowed_mentions=MENTIONS)
        admin = await self.admin_channel()
        if admin:
            link = f" <#{v.match.thread_id}>" if v.match.thread_id else ""
            await admin.send(f"🆘 {v.name(by)} called an admin in **{v.label} · {v.p1.name} vs {v.p2.name}**.{link}")

    async def m_admin_action(self, mid: int, action: str, by: int, **info) -> None:
        m = await self.ctx.matches.get(mid)
        v = await load_match_view(self.ctx, mid)
        text = {
            "award": f"gave the match to {v.name(info.get('winner_id'))}",
            "dq_both": "disqualified both players",
            "set_result": f"set the game result: {v.name(info.get('winner_id'))} won",
            "replay": "ordered a replay",
            "dq": f"disqualified {v.name(info.get('player_id'))}",
        }.get(action, action)
        await self.system(m, f"🛡️ Admin <@{by}> {text}.")

    async def m_player_swapped(self, mid: int, old_id: int, new_id: int, by: int) -> None:
        v = await load_match_view(self.ctx, mid)
        th = await self.thread(v.match)
        if th is None:
            return
        p = v.player(new_id)
        if p and not p.is_fake:
            try:
                await th.add_user(discord.Object(id=new_id))
            except discord.HTTPException:
                pass
        await self.system(v.match, f"🛡️ An admin replaced a player with {p.name if p else new_id}.")
        content, embed = panels.uid_card(v, self.bot)
        await th.send(content, embed=embed, allowed_mentions=MENTIONS)
        await self.post_panel(mid)

    async def m_done(self, mid: int, winner_id: int | None, walkover: bool, reason: str) -> None:
        v = await load_match_view(self.ctx, mid)
        th = await self.thread(v.match)
        if not th:
            return
        await delete_message(th, v.match.turn_message_id)
        if v.is_friendly:
            await self.edit_panel(mid)  # "Duel over", no buttons
            return
        if v.match.panel_message_id:
            try:
                await th.get_partial_message(v.match.panel_message_id).edit(view=None)
            except discord.HTTPException:
                pass

    async def m_advanced(
        self, mid: int, winner_id: int | None, loser_id: int | None, next_match_id: int | None
    ) -> None:
        v = await load_match_view(self.ctx, mid)
        th = await self.thread(v.match)
        if not th:
            return
        info: dict[str, Any] = {}
        if next_match_id and winner_id:
            nv = await load_match_view(self.ctx, next_match_id)
            opp = nv.opponent(winner_id)
            size = nv.tournament.bracket_size or 2
            rname = formats.round_name(size >> (nv.match.round - 1))
            if opp:
                info["next_label"] = f"{v.name(winner_id)} plays {opp.name} in the {rname}."
            else:
                feeder_slot = 2 if nv.match.p1_id == winner_id else 1
                n = nv.match.slot * 2 + feeder_slot
                info["next_label"] = f"{v.name(winner_id)} plays the winner of M{n} in the {rname}."
        elif winner_id:
            info["next_label"] = f"{v.name(winner_id)} is the champion!"
        content, embed = panels.match_won_embed(v, info)
        await th.send(content, embed=embed, allowed_mentions=MENTIONS)

    async def archive_job(self, job) -> None:
        m = await self.ctx.matches.get(job.ref_id)
        th = await self.thread(m)
        if isinstance(th, discord.Thread):
            try:
                await th.edit(archived=True, locked=True)
            except discord.HTTPException as e:
                log.warning("Couldn't archive thread %s: %s", th.id, e)

    async def resync(self) -> None:
        """After a restart: refresh turn messages (timers were reset) and panels of active matches."""
        async with self.ctx.db.session() as s:
            active = list(await s.scalars(select(Match).where(Match.state.not_in(["pending", "done"]))))
        for m in active:
            try:
                if m.state in ("drafting", "paused"):
                    await self.post_turn(m.id)
                await self.edit_panel(m.id)
            except Exception:
                log.exception("Resync failed for match %s", m.id)
