"""Friendly duels driven through the real slash commands, buttons and notifier, with fake Discord objects."""

import asyncio
import logging
import random
import re
from types import SimpleNamespace

import pytest
from tests.conftest import ADMIN, GUILD, player_id
from tests.fakes_discord import FakeBot, FakeInteraction
from tests.test_discord_layer import buttons, click, draft_via_picker

from aniimo_pvp.bot import handlers
from aniimo_pvp.bot import notifier as notifier_mod
from aniimo_pvp.bot.cogs.friendly import TEST_FRIEND, FriendlyCog
from aniimo_pvp.bot.components import MatchButton, TournamentButton
from aniimo_pvp.bot.notifier import DiscordNotifier
from aniimo_pvp.bot.views.choices import ChoiceView
from aniimo_pvp.clock import FakeClock
from aniimo_pvp.config import Channels, Settings
from aniimo_pvp.db.database import Database
from aniimo_pvp.domain.roster import load_roster
from aniimo_pvp.services.common import JOB_ARCHIVE
from aniimo_pvp.services.context import build_context
from aniimo_pvp.services.errors import UserError
from aniimo_pvp.services.views import load_match_view

FRIENDLY = 14
LUNA, KAI = player_id(1), player_id(2)


@pytest.fixture
async def make_world(tmp_path, roster_file, monkeypatch, caplog):
    notifier_mod.LIVE_DELAY = 0
    monkeypatch.setattr(handlers, "is_admin", lambda bot, user: user.id == ADMIN)
    caplog.set_level(logging.WARNING)
    made = []

    async def factory(*, dev_mode=False, friendly=FRIENDLY):
        settings = Settings(
            token="x",
            guild_id=GUILD,
            admin_role_id=5,
            dev_mode=dev_mode,
            channels=Channels(admin=10, announce=11, matches=12, live_draft=13, friendly=friendly),
        )
        bot = FakeBot(settings)
        db = Database(str(tmp_path / f"bot{len(made)}.db"))
        await db.create_all()
        roster, _ = load_roster(roster_file, tmp_path)
        notifier = DiscordNotifier(bot)
        bot.ctx = build_context(settings, db, roster, notifier, clock=FakeClock(), rng=random.Random(4))
        bot.ctx.scheduler.register(JOB_ARCHIVE, notifier.archive_job)
        made.append(bot)
        return bot, FriendlyCog(bot)

    yield factory
    await asyncio.sleep(0)
    for bot in made:
        n = bot.ctx.notifier
        for t in list(n._live_pending.values()) + list(n._status_pending.values()):
            await t
        await bot.ctx.db.dispose()
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, [r.getMessage() + (r.exc_text or "") for r in errors]


def member(uid, name, bot=False):
    return SimpleNamespace(id=uid, display_name=name, bot=bot)


async def duel(bot, cog, user=LUNA, opponent=None, fearless=False, channel=FRIENDLY):
    inter = FakeInteraction(bot, user, channel=bot.channels.get(channel) or SimpleNamespace(id=channel))
    inter.user.display_name = "Luna" if user == LUNA else f"user{user}"
    await cog.duel.callback(cog, inter, opponent or member(KAI, "Kai"), fearless)
    return inter


async def press(bot, user, action, tid):
    inter = FakeInteraction(bot, user)
    await handlers.handle_tournament_action(inter, action, tid, None)
    return inter


def card(bot):
    return bot.ch("friendly").messages[0]


async def settle(bot):
    await asyncio.sleep(0)
    n = bot.ctx.notifier
    for t in list(n._live_pending.values()) + list(n._status_pending.values()):
        await t


async def test_duel_from_challenge_to_end(make_world):
    bot, cog = await make_world()
    ctx = bot.ctx
    friendly, announce, live, matches = bot.ch("friendly"), bot.ch("announce"), bot.ch("live-draft"), bot.ch("matches")

    # /duel posts a public challenge card that pings the friend
    await duel(bot, cog)
    c = card(bot)
    assert f"<@{KAI}>" in c.content and "challenges you" in c.content
    assert c.embeds[0].title == "⚔️ Friendly duel challenge"
    assert any(f.name == "Expires" and "<t:" in f.value for f in c.embeds[0].fields)
    ids = buttons(c.kwargs["view"])
    assert [re.sub(r"\d+", "N", i) for i in ids] == ["at:duel_accept:N", "at:duel_decline:N", "at:duel_cancel:N"]
    tid = int(ids[0].split(":")[2])

    nope = await press(bot, LUNA, "duel_accept", tid)
    assert any("Only the challenged player" in x for x in nope.texts())
    ok = await press(bot, KAI, "duel_accept", tid)

    # the room is a private thread in #friendly that the players may invite spectators to
    assert len(friendly.threads) == 1 and not matches.threads
    th = friendly.threads[0]
    assert th.private and th.invitable and sorted(th.members) == sorted([LUNA, KAI])
    assert th.name == "Friendly · Luna vs Kai"
    assert any(f"<#{th.id}>" in x for x in ok.texts())
    room = th.messages[0]
    assert "friendly duel room is ready" in room.content and "<@&" not in room.content  # no admin ping
    assert all("not set yet" in f.value for f in room.embeds[0].fields)  # nobody used /register
    assert c.embeds[0].title == "⚔️ Friendly duel · in progress" and f"<#{th.id}>" in c.embeds[0].description
    assert c.edits[-1]["view"] is None  # Accept/Decline are gone

    # draft 1: ready, side, draft (no Call admin anywhere, no Report)
    m = (await ctx.tournaments.matches(tid))[0]
    panel = th.live()[-1]
    assert buttons(panel.kwargs["view"]) == [f"ap:ready:{m.id}", f"ap:fend:{m.id}"]
    await click(bot, LUNA, "ready", m.id)
    await click(bot, KAI, "ready", m.id)
    v = await load_match_view(ctx, m.id)
    first_chooser = v.match.side_chooser_id
    await click(bot, first_chooser, "side", m.id, "A")
    turn = next(x for x in th.live() if x.content and "it's your" in x.content)
    assert buttons(turn.kwargs["view"]) == [f"ap:pick:{m.id}:{v.game.id}-0"]
    await draft_via_picker(bot, m.id)
    final_board = next(x for x in th.live() if x.content and "Draft 1 is complete" in x.content)
    assert final_board.kwargs.get("file") is not None
    panel = th.live()[-1]
    assert panel.embeds[0].title == "Draft 1 complete · play it in Aniimo"
    assert buttons(panel.kwargs["view"]) == [f"ap:fnext:{m.id}", f"ap:fend:{m.id}"]
    assert any(f.name == "Closes" for f in panel.embeds[0].fields)

    # a result can't be reported, even with an old button
    rep = await click(bot, LUNA, "report", m.id)
    assert any("don't have results or admins" in x for x in rep.texts()), rep.texts()

    # draft 2: the other player picks the side
    nxt = await click(bot, KAI, "fnext", m.id)
    second_chooser = (await ctx.matches.get(m.id)).side_chooser_id
    assert second_chooser != first_chooser
    assert any("Draft 2 started" in x and "chooses the side this time" in x for x in nxt.texts())
    panel = th.live()[-1]
    assert panel.embeds[0].title.startswith("Draft 2 · ")
    assert buttons(panel.kwargs["view"]) == [f"ap:side:{m.id}:A", f"ap:side:{m.id}:B", f"ap:fend:{m.id}"]
    assert not final_board.deleted  # the first board stays in the room

    # End duel asks first, then closes everything
    end = await click(bot, LUNA, "fend", m.id)
    assert "End the duel? 1 draft played. The draft in progress is dropped." in end.texts()[0]
    view: ChoiceView = end.last_view()
    yes = FakeInteraction(bot, LUNA)
    await view.children[0].callback(yes)
    assert yes.texts()[-1] == "Duel ended after 1 draft."
    assert any(x.content and "Luna ended the duel after 1 draft" in x.content for x in th.live())
    assert th.live()[0].embeds  # room card still there
    panel_id = (await ctx.matches.get(m.id)).panel_message_id
    panel = next(x for x in th.messages if x.id == panel_id)
    assert panel.embeds[0].title == "Duel over" and panel.edits[-1]["view"] is None
    assert c.embeds[0].description == "🏁 Duel over · 1 draft played."

    # nothing was announced, streamed or rated
    await settle(bot)
    assert not announce.messages and not live.messages
    assert await ctx.stats.leaderboard() == []

    # the thread is archived a few minutes later (the fake thread isn't a discord.Thread, so check the timer)
    assert len(await ctx.scheduler.pending(JOB_ARCHIVE, m.id)) == 1
    ctx.clock.advance(ctx.settings.timers.thread_archive_minutes * 60 + 1)
    assert await ctx.scheduler.run_due() == 1
    assert not await ctx.scheduler.pending(JOB_ARCHIVE, m.id)


async def test_decline_cancel_and_expiry_update_the_card(make_world):
    bot, cog = await make_world()
    ctx = bot.ctx
    await duel(bot, cog)
    tid = int(buttons(card(bot).kwargs["view"])[0].split(":")[2])
    stranger = await press(bot, player_id(9), "duel_decline", tid)
    assert any("Only the challenged player can decline" in x for x in stranger.texts())
    await press(bot, KAI, "duel_decline", tid)
    assert card(bot).embeds[0].description == "✖️ Kai declined the duel."
    assert card(bot).edits[-1]["view"] is None

    await duel(bot, cog)
    second = bot.ch("friendly").messages[1]
    tid2 = int(buttons(second.kwargs["view"])[0].split(":")[2])
    await press(bot, LUNA, "duel_cancel", tid2)
    assert second.embeds[0].description == "✖️ The challenge was cancelled."

    await duel(bot, cog)
    third = bot.ch("friendly").messages[2]
    ctx.clock.advance(ctx.settings.timers.challenge_minutes * 60 + 1)
    await ctx.scheduler.run_due()
    assert third.embeds[0].description == "⌛ The challenge expired. Kai didn't answer in time."
    late = await press(bot, KAI, "duel_accept", int(buttons(third.kwargs["view"])[0].split(":")[2]))
    assert any("no longer open" in x for x in late.texts())
    assert not bot.ch("friendly").threads


async def test_where_and_whom_you_can_duel(make_world):
    bot, cog = await make_world()
    with pytest.raises(UserError, match="Duels start in <#14>"):
        await duel(bot, cog, channel=12)
    with pytest.raises(UserError, match="Bots don't draft"):
        await duel(bot, cog, opponent=member(77, "SomeBot", bot=True))
    with pytest.raises(UserError, match="yourself"):
        await duel(bot, cog, opponent=member(LUNA, "Luna"))
    inter = FakeInteraction(bot, ADMIN, channel=bot.ch("friendly"))
    with pytest.raises(UserError, match="only works in dev mode"):
        await cog.duel_test.callback(cog, inter, False)
    off, off_cog = await make_world(friendly=0)
    with pytest.raises(UserError, match="aren't set up"):
        await duel(off, off_cog, channel=12)


async def test_register_shows_uid_in_the_room(make_world):
    bot, cog = await make_world()
    inter = FakeInteraction(bot, KAI)
    await cog.register.callback(cog, inter, "K-77")
    assert "`K-77`" in inter.texts()[0]
    with pytest.raises(UserError):
        await cog.register.callback(cog, FakeInteraction(bot, KAI), "no spaces allowed")
    await duel(bot, cog)
    tid = int(buttons(card(bot).kwargs["view"])[0].split(":")[2])
    await press(bot, KAI, "duel_accept", tid)
    room = bot.ch("friendly").threads[0].messages[0]
    kai_field = room.embeds[0].fields[1]
    assert "```K-77```" in kai_field.value


async def test_dev_mode_duel_test_plays_both_sides(make_world):
    bot, cog = await make_world(dev_mode=True)
    ctx = bot.ctx
    inter = FakeInteraction(bot, ADMIN, channel=bot.ch("friendly"))
    await cog.duel_test.callback(cog, inter, True)
    c = card(bot)
    assert f"**{TEST_FRIEND[1]}**" in c.content  # a test player is named, not pinged
    tid = int(buttons(c.kwargs["view"])[0].split(":")[2])
    await press(bot, ADMIN, "duel_accept", tid)  # the challenger answers for the test player
    th = bot.ch("friendly").threads[0]
    assert th.members == [ADMIN]
    m = (await ctx.tournaments.matches(tid))[0]
    await click(bot, ADMIN, "ready", m.id)
    await click(bot, ADMIN, "ready", m.id)  # acts for the test player
    v = await load_match_view(ctx, m.id)
    await click(bot, ADMIN, "side", m.id, "B")
    await draft_via_picker(bot, m.id)
    assert (await ctx.matches.get(m.id)).state == "playing"
    await click(bot, ADMIN, "fnext", m.id)
    assert (await ctx.matches.get(m.id)).side_chooser_id != v.match.side_chooser_id


async def test_idle_duel_closes_itself_and_says_so(make_world):
    bot, cog = await make_world()
    ctx = bot.ctx
    await duel(bot, cog)
    tid = int(buttons(card(bot).kwargs["view"])[0].split(":")[2])
    await press(bot, KAI, "duel_accept", tid)
    m = (await ctx.tournaments.matches(tid))[0]
    await click(bot, LUNA, "ready", m.id)
    await click(bot, KAI, "ready", m.id)
    v = await load_match_view(ctx, m.id)
    await click(bot, v.match.side_chooser_id, "side", m.id, "A")
    await draft_via_picker(bot, m.id)
    ctx.clock.advance(ctx.settings.timers.friendly_idle_minutes * 60 + 1)
    await ctx.scheduler.run_due()
    th = bot.ch("friendly").threads[0]
    assert any(x.content and "nobody started a new draft" in x.content for x in th.live())
    assert "It closed after a while without a new draft." in card(bot).embeds[0].description


async def test_noshow_reminder_and_close(make_world):
    bot, cog = await make_world()
    ctx = bot.ctx
    await duel(bot, cog)
    tid = int(buttons(card(bot).kwargs["view"])[0].split(":")[2])
    await press(bot, KAI, "duel_accept", tid)
    th = bot.ch("friendly").threads[0]
    ctx.clock.advance(ctx.settings.timers.noshow_reminder_minutes * 60 + 1)
    await ctx.scheduler.run_due()
    assert any(x.content and "The duel closes" in x.content for x in th.live())
    ctx.clock.advance(ctx.settings.timers.noshow_escalate_minutes * 60)
    await ctx.scheduler.run_due()
    assert any(x.content and "weren't both ready" in x.content for x in th.live())
    assert not bot.ch("admin").messages  # no admin alert for a friendly


def test_buttons_survive_a_restart():
    assert re.fullmatch(TournamentButton.__discord_ui_compiled_template__, "at:duel_accept:12")
    assert re.fullmatch(MatchButton.__discord_ui_compiled_template__, "ap:fnext:7")
    assert re.fullmatch(MatchButton.__discord_ui_compiled_template__, "ap:fend:7")
