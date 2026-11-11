"""Button/menu handlers. Every handler answers privately on errors."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from ..services.errors import NotAllowed, UserError
from ..services.views import MatchView, PlayerInfo, load_match_view
from .util import is_admin
from .views.choices import ChoiceView, RenameModal
from .views.picker import open_picker

if TYPE_CHECKING:
    from .client import AniimoBot

log = logging.getLogger(__name__)
B = discord.ButtonStyle
ADMIN_MATCH_ACTIONS = {"award", "extend", "dqboth", "setres", "replay"}
ADMIN_TOURNAMENT_ACTIONS = {"next", "rename", "cancel", "confirm", "discard", "start", "nextround"}


async def reply(interaction: discord.Interaction, text: str, **kwargs) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True, **kwargs)
        else:
            await interaction.response.send_message(text, ephemeral=True, **kwargs)
    except discord.HTTPException:
        log.warning("Could not reply to interaction")


async def thinking(interaction: discord.Interaction) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)


def pick_actor(bot: AniimoBot, interaction: discord.Interaction, candidates: list[PlayerInfo | None]) -> int:
    """The clicking player, or in dev mode a test player an admin is acting for."""
    uid = interaction.user.id
    cands = [c for c in candidates if c]
    if any(c.id == uid for c in cands):
        return uid
    if bot.settings.dev_mode and is_admin(bot, interaction.user):
        for c in cands:
            if c.is_fake:
                return c.id
    return uid


def acting_note(interaction: discord.Interaction, v: MatchView, actor: int) -> str:
    if actor != interaction.user.id:
        p = v.player(actor)
        return f" (dev mode: acting for {p.name if p else actor})"
    return ""


async def handle_match_action(interaction: discord.Interaction, action: str, match_id: int, arg: str | None) -> None:
    bot: AniimoBot = interaction.client  # type: ignore[assignment]
    try:
        if action in ADMIN_MATCH_ACTIONS and not is_admin(bot, interaction.user):
            raise NotAllowed("Only tournament admins can do that")
        handler = MATCH_HANDLERS.get(action)
        if handler is None:
            raise UserError("This button is no longer supported")
        await handler(bot, interaction, match_id, arg)
    except UserError as e:
        await reply(interaction, f"⚠️ {e}")
    except Exception:
        log.exception("Match action %s failed", action)
        await reply(interaction, "⚠️ Something went wrong. An admin can check the bot log.")


async def _ready(bot, interaction, mid, arg):
    v = await load_match_view(bot.ctx, mid)
    m = v.match
    not_ready = [p for p, r in ((v.p1, m.p1_ready), (v.p2, m.p2_ready)) if not r]
    actor = pick_actor(bot, interaction, not_ready or [v.p1, v.p2])
    await thinking(interaction)
    both = await bot.ctx.matches.ready(mid, actor)
    note = acting_note(interaction, v, actor)
    if both:
        await reply(interaction, f"Both players are ready! The coin flip is below.{note}")
    else:
        other = v.opponent(actor)
        await reply(interaction, f"You're ready. Waiting for {other.name if other else 'your opponent'}.{note}")


async def _side(bot, interaction, mid, arg):
    v = await load_match_view(bot.ctx, mid)
    actor = pick_actor(bot, interaction, [v.player(v.match.side_chooser_id)])
    await thinking(interaction)
    await bot.ctx.matches.choose_side(mid, actor, arg or "A")
    await reply(interaction, f"You took Side {arg}.{acting_note(interaction, v, actor)}")


async def _pick(bot, interaction, mid, arg):
    try:
        game_id, step = (int(x) for x in (arg or "").split("-"))
    except ValueError as e:
        raise UserError("This button is outdated") from e
    v = await load_match_view(bot.ctx, mid)
    actor = pick_actor(bot, interaction, [v.turn_player])
    try:
        opts = await bot.ctx.drafts.options(mid, game_id, step, actor)
    except NotAllowed:
        tp = v.turn_player
        kind = "ban" if v.turn and v.turn[1] == "ban" else "pick"
        raise UserError(
            f"It's {tp.name if tp else 'the other player'}'s turn to {kind}. The board updates here "
            "after each step, and you'll be pinged when it's your turn."
        ) from None
    await open_picker(bot, interaction, opts)


async def _report(bot, interaction, mid, arg):
    v = await load_match_view(bot.ctx, mid)
    actor = pick_actor(bot, interaction, [v.p1, v.p2])
    if actor not in (v.p1.id, v.p2.id):
        raise NotAllowed("Only the players can report the result")
    if v.match.state != "playing":
        raise UserError("There's no game in progress to report")

    def win(p: PlayerInfo):
        async def go() -> str:
            await bot.ctx.matches.report(mid, actor, p.id)
            other = v.opponent(actor)
            return f"Reported: **{p.name} won**. Waiting for {other.name} to confirm."

        return go

    view = ChoiceView([(f"{v.p1.name} won", B.primary, win(v.p1)), (f"{v.p2.name} won", B.primary, win(v.p2))])
    await interaction.response.send_message(
        f"**Who won Game {v.game.number}?**{acting_note(interaction, v, actor)}\n"
        "-# Optional: post a screenshot of the result screen in this thread and it's attached to the report.",
        view=view,
        ephemeral=True,
    )


async def _confirm(bot, interaction, mid, arg):
    v = await load_match_view(bot.ctx, mid)
    actor = pick_actor(bot, interaction, [v.opponent(v.game.reported_by)] if v.game else [])
    await thinking(interaction)
    await bot.ctx.matches.confirm(mid, actor)
    await reply(interaction, f"Result confirmed.{acting_note(interaction, v, actor)}")


async def _dispute(bot, interaction, mid, arg):
    v = await load_match_view(bot.ctx, mid)
    actor = pick_actor(bot, interaction, [v.opponent(v.game.reported_by)] if v.game else [])
    await thinking(interaction)
    await bot.ctx.matches.dispute(mid, actor)
    await reply(interaction, "Dispute sent. An admin will set the result or replay the game.")


async def _rematch(bot, interaction, mid, arg):
    v = await load_match_view(bot.ctx, mid)
    actor = pick_actor(bot, interaction, [v.p1, v.p2])
    if actor not in (v.p1.id, v.p2.id):
        raise NotAllowed("Only the players can ask for a rematch")
    if v.match.state != "playing":
        raise UserError(
            "A rematch can only be requested while the game is being played. After a report, use Dispute instead."
        )

    def req(mode: str, label: str):
        async def go() -> str:
            await bot.ctx.matches.request_rematch(mid, actor, mode)
            return f"Rematch offer sent ({label}). Your opponent has a few minutes to accept."

        return go

    view = ChoiceView(
        [
            ("Replay same teams", B.primary, req("same_teams", "replay with the same teams")),
            ("New draft", B.secondary, req("new_draft", "redo the draft")),
        ]
    )
    await interaction.response.send_message(
        f"**What kind of rematch for Game {v.game.number}?**{acting_note(interaction, v, actor)}\n"
        "• **Replay same teams**: disconnect or bug. The draft is kept.\n"
        "• **New draft**: redo this game's draft from step 1.\n"
        "-# The series score doesn't change. This is not the next game.",
        view=view,
        ephemeral=True,
    )


async def _rematch_answer(bot, interaction, mid, accept: bool):
    v = await load_match_view(bot.ctx, mid)
    actor = pick_actor(bot, interaction, [v.opponent(v.match.rematch_by)] if v.match.rematch_by else [])
    await thinking(interaction)
    await bot.ctx.matches.answer_rematch(mid, actor, accept)
    await reply(interaction, "Rematch accepted." if accept else "Rematch declined.")


async def _rematch_yes(bot, interaction, mid, arg):
    await _rematch_answer(bot, interaction, mid, True)


async def _rematch_no(bot, interaction, mid, arg):
    await _rematch_answer(bot, interaction, mid, False)


async def _next(bot, interaction, mid, arg):
    v = await load_match_view(bot.ctx, mid)
    actor = pick_actor(bot, interaction, [v.p1, v.p2])
    await thinking(interaction)
    await bot.ctx.matches.next_game(mid, actor)
    await reply(interaction, "Next game started. The side choice is below.")


async def _call_admin(bot, interaction, mid, arg):
    v = await load_match_view(bot.ctx, mid)
    if is_admin(bot, interaction.user) and interaction.user.id not in (v.p1.id, v.p2.id):
        raise UserError("You're an admin already. Use the /admin commands in this thread.")
    actor = pick_actor(bot, interaction, [v.p1, v.p2])
    await thinking(interaction)
    await bot.ctx.matches.call_admin(mid, actor)
    await reply(interaction, "An admin has been notified.")


async def _admin_done(interaction: discord.Interaction, text: str) -> None:
    await reply(interaction, text)
    if interaction.message is not None and interaction.message.embeds:
        try:
            e = interaction.message.embeds[0]
            e.set_footer(text=f"Handled by {interaction.user.display_name}: {text}")
            await interaction.message.edit(embed=e, view=None)
        except discord.HTTPException:
            pass


async def _award(bot, interaction, mid, arg):
    await thinking(interaction)
    await bot.ctx.matches.admin_award(mid, interaction.user.id, int(arg), reason="admin")
    v = await load_match_view(bot.ctx, mid)
    await _admin_done(interaction, f"Win given to {v.name(int(arg))}.")


async def _extend(bot, interaction, mid, arg):
    await thinking(interaction)
    await bot.ctx.matches.admin_extend(mid, interaction.user.id)
    await _admin_done(interaction, "Ready check extended.")


async def _dqboth(bot, interaction, mid, arg):
    await thinking(interaction)
    await bot.ctx.matches.admin_dq_both(mid, interaction.user.id)
    await _admin_done(interaction, "Both players disqualified.")


async def _setres(bot, interaction, mid, arg):
    await thinking(interaction)
    await bot.ctx.matches.admin_set_result(mid, interaction.user.id, int(arg))
    v = await load_match_view(bot.ctx, mid)
    await _admin_done(interaction, f"Result set: {v.name(int(arg))} won the game.")


async def _replay(bot, interaction, mid, arg):
    await thinking(interaction)
    await bot.ctx.matches.admin_replay(mid, interaction.user.id, arg or "same_teams")
    await _admin_done(interaction, "Game will be replayed.")


MATCH_HANDLERS = {
    "ready": _ready,
    "side": _side,
    "pick": _pick,
    "report": _report,
    "confirm": _confirm,
    "dispute": _dispute,
    "rematch": _rematch,
    "rematch_yes": _rematch_yes,
    "rematch_no": _rematch_no,
    "next": _next,
    "admin": _call_admin,
    "award": _award,
    "extend": _extend,
    "dqboth": _dqboth,
    "setres": _setres,
    "replay": _replay,
}


# ---------------- tournament buttons + setup menus ----------------
async def handle_tournament_action(interaction: discord.Interaction, action: str, tid: int, arg: str | None) -> None:
    bot: AniimoBot = interaction.client  # type: ignore[assignment]
    ctx = bot.ctx
    try:
        if action in ADMIN_TOURNAMENT_ACTIONS and not is_admin(bot, interaction.user):
            raise NotAllowed("Only tournament admins can do that")
        uid = interaction.user.id
        if action == "next":
            await interaction.response.defer()
            await ctx.tournaments.to_registration(tid, uid)
        elif action == "rename":
            t = await ctx.tournaments.get(tid)
            await interaction.response.send_modal(RenameModal(bot, tid, t.name))
        elif action == "cancel":
            t = await ctx.tournaments.get(tid)

            async def go() -> str:
                await ctx.tournaments.cancel(tid, uid)
                return f"{t.name} was cancelled."

            await interaction.response.send_message(
                f"Cancel **{t.name}**? Open matches stop and this can't be undone.",
                view=ChoiceView([("Yes, cancel it", B.danger, go)]),
                ephemeral=True,
            )
        elif action == "confirm":
            await interaction.response.defer()
            await ctx.tournaments.confirm_players(tid, uid)
        elif action == "discard":
            await interaction.response.defer()
            await ctx.tournaments.discard_preview(tid, uid)
            if interaction.message:
                await interaction.message.edit(content="Preview discarded. Paste a new CSV.", view=None)
        elif action == "start":
            await thinking(interaction)
            await ctx.tournaments.start(tid, uid)
            await reply(interaction, "Started! The bracket is posted in the announce channel.")
        elif action == "nextround":
            await thinking(interaction)
            n = await ctx.tournaments.start_next_round(tid, uid)
            await _admin_done(interaction, f"Round {n} started.")
        else:
            raise UserError("This button is no longer supported")
    except UserError as e:
        await reply(interaction, f"⚠️ {e}")
    except Exception:
        log.exception("Tournament action %s failed", action)
        await reply(interaction, "⚠️ Something went wrong. An admin can check the bot log.")


async def handle_setup_select(interaction: discord.Interaction, field: str, tid: int, value: str) -> None:
    bot: AniimoBot = interaction.client  # type: ignore[assignment]
    try:
        if not is_admin(bot, interaction.user):
            raise NotAllowed("Only tournament admins can change the format")
        await interaction.response.defer()
        if field == "cap":
            await bot.ctx.tournaments.set_options(tid, interaction.user.id, cap=int(value))
        elif field == "fmt":
            await bot.ctx.tournaments.set_options(tid, interaction.user.id, fmt=value)
        elif field == "fear":
            await bot.ctx.tournaments.set_options(tid, interaction.user.id, fearless=value == "on")
    except UserError as e:
        await reply(interaction, f"⚠️ {e}")
    except Exception:
        log.exception("Setup select failed")
        await reply(interaction, "⚠️ Something went wrong. An admin can check the bot log.")
