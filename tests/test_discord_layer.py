import asyncio
import logging
import random
import re

import discord
import pytest
from tests.conftest import ADMIN, GUILD, lookup_all, player_id
from tests.fakes_discord import FakeBot, FakeInteraction

from aniimo_pvp.bot import handlers, panels
from aniimo_pvp.bot import notifier as notifier_mod
from aniimo_pvp.bot.components import MatchButton, SetupSelect, TournamentButton
from aniimo_pvp.bot.notifier import DiscordNotifier
from aniimo_pvp.bot.views.choices import ChoiceView
from aniimo_pvp.bot.views.picker import PickerView
from aniimo_pvp.clock import FakeClock
from aniimo_pvp.config import Channels, Settings
from aniimo_pvp.db.database import Database
from aniimo_pvp.domain.roster import load_roster
from aniimo_pvp.services.context import build_context
from aniimo_pvp.services.views import load_match_view

ADMIN_IDS = {ADMIN}


@pytest.fixture
async def world(tmp_path, roster_file, monkeypatch, caplog):
    notifier_mod.LIVE_DELAY = 0
    monkeypatch.setattr(handlers, "is_admin", lambda bot, user: user.id in ADMIN_IDS)
    settings = Settings(
        token="x",
        guild_id=GUILD,
        admin_role_id=5,
        dev_mode=False,
        channels=Channels(admin=10, announce=11, matches=12, live_draft=13),
    )
    bot = FakeBot(settings)
    db = Database(str(tmp_path / "bot.db"))  # a file DB like production: one connection per session
    await db.create_all()
    roster, _ = load_roster(roster_file, tmp_path)
    bot.ctx = build_context(settings, db, roster, DiscordNotifier(bot), clock=FakeClock(), rng=random.Random(4))
    caplog.set_level(logging.WARNING)
    yield bot
    await asyncio.sleep(0)
    for t in list(bot.ctx.notifier._live_pending.values()) + list(bot.ctx.notifier._status_pending.values()):
        await t
    await db.dispose()
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, [r.getMessage() + (r.exc_text or "") for r in errors]


async def setup_tournament(bot, n=4, fmt="quick", cap=8):
    ctx = bot.ctx
    t = await ctx.tournaments.create(GUILD, "AniiCup #1", ADMIN)
    msg = await bot.ch("admin").send(embed=panels.setup_embed(t), view=panels.setup_view(t))
    await ctx.tournaments.set_message_ids(t.id, setup_channel_id=10, setup_message_id=msg.id)
    await ctx.tournaments.set_options(t.id, ADMIN, cap=cap, fmt=fmt)
    await ctx.tournaments.to_registration(t.id, ADMIN)
    csv = "\n".join(f"{player_id(i)},{500 + i},P{i}" for i in range(1, n + 1))
    await ctx.tournaments.preview_players(t.id, ADMIN, csv, lookup_all)
    await ctx.tournaments.confirm_players(t.id, ADMIN)
    await ctx.tournaments.start(t.id, ADMIN, seed="S")
    return t


def buttons(view) -> list[str]:
    return [c.custom_id for c in view.children if getattr(c, "custom_id", None)]


async def click(bot, user, action, mid, arg=None):
    inter = FakeInteraction(bot, user)
    await handlers.handle_match_action(inter, action, mid, arg)
    return inter


async def draft_via_picker(bot, mid):
    """Play the whole draft by clicking the turn button and using the private picker."""
    while True:
        v = await load_match_view(bot.ctx, mid)
        if v.match.state != "drafting":
            return
        player = v.turn_player
        inter = await click(bot, player.id, "pick", mid, f"{v.game.id}-{v.draft.step}")
        view: PickerView = inter.last_view()
        assert isinstance(view, PickerView), inter.texts()
        el = view.children[0]  # element select
        sel = FakeInteraction(bot, player.id, data={"values": [el.options[0].value]})
        await view.on_element(sel)
        view2: PickerView = sel.last_view()
        an = next(c for c in view2.children if isinstance(c, discord.ui.Select) and c.placeholder == "Choose an Aniimo")
        pick = FakeInteraction(bot, player.id, data={"values": [an.options[0].value]})
        await view2.on_aniimo(pick)
        view3: PickerView = pick.last_view()
        lock = FakeInteraction(bot, player.id)
        await view3.on_lock(lock)
        assert any("You banned" in t or "You picked" in t for t in lock.texts()), lock.texts()


async def test_full_tournament_through_buttons_and_notifier(world):
    bot = world
    ctx = bot.ctx
    t = await setup_tournament(bot, n=4, fmt="quick", cap=8)
    admin, announce, matches, live = bot.ch("admin"), bot.ch("announce"), bot.ch("matches"), bot.ch("live-draft")
    assert any(m.kwargs.get("file") for m in announce.messages)  # bracket image posted
    assert len(matches.threads) == 2 and all(th.private for th in matches.threads)
    th = matches.threads[0]
    assert len(th.members) == 2
    first = th.messages[0]
    assert "match is ready" in first.content and first.embeds[0].fields[0].value.count("```") == 2

    for mm in [m for m in await ctx.tournaments.matches(t.id, 1) if m.state == "waiting"]:
        v = await load_match_view(ctx, mm.id)
        r1 = await click(bot, v.p1.id, "ready", mm.id)
        assert any("Waiting for" in x for x in r1.texts())
        r2 = await click(bot, v.p2.id, "ready", mm.id)
        assert any("Both players are ready" in x for x in r2.texts())
        v = await load_match_view(ctx, mm.id)
        wrong = v.opponent(v.match.side_chooser_id)
        bad = await click(bot, wrong.id, "side", mm.id, "A")
        assert any("other player chooses" in x for x in bad.texts())
        await click(bot, v.match.side_chooser_id, "side", mm.id, "B")
        # not-your-turn message
        v = await load_match_view(ctx, mm.id)
        other = v.opponent(v.turn_player.id)
        nope = await click(bot, other.id, "pick", mm.id, f"{v.game.id}-{v.draft.step}")
        assert any("turn to ban" in x for x in nope.texts())
        await draft_via_picker(bot, mm.id)
        # report via the private choice view, confirm via button
        rep = await click(bot, v.p1.id, "report", mm.id)
        view: ChoiceView = rep.last_view()
        choose = FakeInteraction(bot, v.p1.id)
        await view.children[0].callback(choose)
        assert any("Reported" in x for x in choose.texts())
        conf = await click(bot, v.p2.id, "confirm", mm.id)
        assert (await ctx.matches.get(mm.id)).state == "done", conf.sent

    # round complete -> admin gets Start Round 2
    rc = [m for m in admin.messages if m.embeds and "Round 1 complete" in (m.embeds[0].title or "")]
    assert rc and "at:nextround" in buttons(rc[0].kwargs["view"])[0]
    inter = FakeInteraction(bot, ADMIN, message=rc[0])
    await handlers.handle_tournament_action(inter, "nextround", t.id, "2")
    assert len(matches.threads) == 3, inter.sent
    (final,) = [m for m in await ctx.tournaments.matches(t.id, 2)]
    assert final.bo == 3
    v = await load_match_view(ctx, final.id)
    for _ in range(2):
        m = await ctx.matches.get(final.id)
        if m.state == "between_games":
            await click(bot, v.p2.id, "next", final.id)
        else:
            await click(bot, v.p1.id, "ready", final.id)
            await click(bot, v.p2.id, "ready", final.id)
        m = await ctx.matches.get(final.id)
        await click(bot, m.side_chooser_id, "side", final.id, "A")
        await draft_via_picker(bot, final.id)
        rep = await click(bot, v.p1.id, "report", final.id)
        await rep.last_view().children[0].callback(FakeInteraction(bot, v.p1.id))
        await click(bot, v.p2.id, "confirm", final.id)
    assert (await ctx.tournaments.get(t.id)).status == "finished"
    assert any("champion" in (m.content or "") for m in announce.messages)
    won = [m for m in matches.threads[-1].messages if m.embeds and "🏆" in (m.embeds[0].title or "")]
    assert won and any(f.name == "Rating" for f in won[0].embeds[0].fields)
    for task in list(ctx.notifier._live_pending.values()) + list(ctx.notifier._status_pending.values()):
        await task
    assert any(m.kwargs.get("file") for m in live.messages)  # live boards


async def test_rematch_dispute_and_admin_buttons(world):
    bot = world
    ctx = bot.ctx
    t = await setup_tournament(bot, n=2, fmt="standard", cap=8)
    (m,) = await ctx.tournaments.matches(t.id, 1)
    v = await load_match_view(ctx, m.id)
    await click(bot, v.p1.id, "ready", m.id)
    await click(bot, v.p2.id, "ready", m.id)
    mm = await ctx.matches.get(m.id)
    await click(bot, mm.side_chooser_id, "side", m.id, "A")
    await draft_via_picker(bot, m.id)
    req = await click(bot, v.p1.id, "rematch", m.id)
    await req.last_view().children[0].callback(FakeInteraction(bot, v.p1.id))  # same teams
    th = bot.ch("matches").threads[0]
    panel = next(x for x in reversed(th.messages) if x.embeds and x.kwargs.get("view"))
    assert "ap:rematch_yes" in " ".join(buttons(panel.kwargs["view"]))
    own = await click(bot, v.p1.id, "rematch_yes", m.id)
    assert any("Only the other player" in x for x in own.texts())
    await click(bot, v.p2.id, "rematch_yes", m.id)
    g = (await load_match_view(ctx, m.id)).game
    assert g.attempt == 2 and g.status == "playing"
    rep = await click(bot, v.p1.id, "report", m.id)
    await rep.last_view().children[0].callback(FakeInteraction(bot, v.p1.id))
    await click(bot, v.p2.id, "dispute", m.id)
    admin = bot.ch("admin")
    alert = next(x for x in reversed(admin.messages) if x.embeds and "Dispute" in (x.embeds[0].title or ""))
    ids = buttons(alert.kwargs["view"])
    assert any(i.startswith("ap:setres:") for i in ids)
    nonadmin = await click(bot, v.p1.id, "setres", m.id, str(v.p2.id))
    assert any("Only tournament admins" in x for x in nonadmin.texts())
    inter = FakeInteraction(bot, ADMIN, message=alert)
    await handlers.handle_match_action(inter, "setres", m.id, str(v.p2.id))
    assert (await ctx.matches.get(m.id)).state == "between_games"
    assert alert.edits and alert.edits[-1].get("view") is None


async def test_noshow_alert_and_award(world):
    bot = world
    ctx = bot.ctx
    t = await setup_tournament(bot, n=2, fmt="quick", cap=8)
    (m,) = await ctx.tournaments.matches(t.id, 1)
    ctx.clock.advance(15 * 60 + 1)
    await ctx.scheduler.run_due()
    alert = next(x for x in bot.ch("admin").messages if x.embeds and "No-show" in (x.embeds[0].title or ""))
    ids = buttons(alert.kwargs["view"])
    assert len([i for i in ids if i.startswith("ap:award:")]) == 2
    v = await load_match_view(ctx, m.id)
    inter = FakeInteraction(bot, ADMIN, message=alert)
    await handlers.handle_match_action(inter, "award", m.id, str(v.p1.id))
    assert (await ctx.matches.get(m.id)).winner_id == v.p1.id


async def test_setup_panel_controls(world):
    bot = world
    ctx = bot.ctx
    t = await ctx.tournaments.create(GUILD, "Cup", ADMIN)
    view = panels.setup_view(t)
    ids = [c.custom_id for c in view.children]
    assert ids[:3] == [f"as:cap:{t.id}", f"as:fmt:{t.id}", f"as:fear:{t.id}"]
    msg = await bot.ch("admin").send(embed=panels.setup_embed(t), view=view)
    await ctx.tournaments.set_message_ids(t.id, setup_channel_id=10, setup_message_id=msg.id)
    inter = FakeInteraction(bot, ADMIN)
    await handlers.handle_setup_select(inter, "cap", t.id, "128")
    await handlers.handle_setup_select(FakeInteraction(bot, ADMIN), "fmt", t.id, "championship")
    t2 = await ctx.tournaments.get(t.id)
    assert (t2.player_cap, t2.format) == (128, "championship")
    assert "Round of 128" in msg.edits[-1]["embed"].description and "387" in msg.edits[-1]["embed"].description
    deny = FakeInteraction(bot, 777)
    await handlers.handle_setup_select(deny, "cap", t.id, "8")
    assert any("Only tournament admins" in x for x in deny.texts())
    await handlers.handle_tournament_action(FakeInteraction(bot, ADMIN), "next", t.id, None)
    assert any(m.embeds and "STEP 2" in (m.embeds[0].author.name or "") for m in bot.ch("admin").messages)


def test_custom_ids_round_trip_and_limits():
    for item, template in (
        (MatchButton("pick", 123456, "99999-11", label="x"), MatchButton),
        (TournamentButton("nextround", 42, "7", label="x"), TournamentButton),
    ):
        cid = item.item.custom_id
        assert len(cid) <= 100 and re.fullmatch(template.__discord_ui_compiled_template__, cid)
    sel = SetupSelect("cap", 3, [discord.SelectOption(label="a", value="8")])
    assert re.fullmatch(SetupSelect.__discord_ui_compiled_template__, sel.item.custom_id)


async def test_embed_limits_for_big_rounds_and_previews(world):
    bot = world
    ctx = bot.ctx
    t = await setup_tournament(bot, n=128, fmt="standard", cap=128)
    rows = []
    for m in await ctx.tournaments.matches(t.id, 1):
        v = await load_match_view(ctx, m.id)
        rows.append((m, v.p1, v.p2))
    embeds = panels.round_table_embeds(await ctx.tournaments.get(t.id), 1, rows)
    assert 1 <= len(embeds) <= 10 and all(len(e.description) <= 4096 for e in embeds)
    assert len(bot.ch("matches").threads) == 64
    from aniimo_pvp.domain.players_csv import CsvIssue
    from aniimo_pvp.services.tournament import PlayersPreview

    p = PlayersPreview(issues=[CsvIssue(i, "x" * 80) for i in range(60)], cap=8, blocking=["too many"])
    e = panels.preview_embed(await ctx.tournaments.get(t.id), p)
    assert all(len(f.value) <= 1024 for f in e.fields) and len(e) <= 6000


async def test_screenshot_listener_and_call_admin(world):
    bot = world
    ctx = bot.ctx
    t = await setup_tournament(bot, n=2, fmt="quick", cap=8)
    (m,) = await ctx.tournaments.matches(t.id, 1)
    v = await load_match_view(ctx, m.id)
    ca = await click(bot, v.p1.id, "admin", m.id)
    assert any("admin has been notified" in x for x in ca.texts())
    assert any("called an admin" in (x.content or "") for x in bot.ch("admin").messages)


async def test_dev_mode_admin_acts_for_fake_players(tmp_path, roster_file, monkeypatch):
    notifier_mod.LIVE_DELAY = 0
    monkeypatch.setattr(handlers, "is_admin", lambda bot, user: user.id in ADMIN_IDS)
    settings = Settings(
        token="x",
        guild_id=GUILD,
        admin_role_id=5,
        dev_mode=True,
        channels=Channels(admin=10, announce=11, matches=12, live_draft=0),
    )
    bot = FakeBot(settings)
    db = Database(":memory:")
    await db.create_all()
    roster, _ = load_roster(roster_file, tmp_path)
    bot.ctx = build_context(settings, db, roster, DiscordNotifier(bot), clock=FakeClock(), rng=random.Random(4))
    ctx = bot.ctx
    t = await ctx.tournaments.create(GUILD, "Dev", ADMIN)

    async def nobody(_):
        return None

    await ctx.tournaments.preview_players(t.id, ADMIN, "11,1,Fake A\n22,2,Fake B\n", nobody)
    await ctx.tournaments.confirm_players(t.id, ADMIN)
    await ctx.tournaments.start(t.id, ADMIN)
    (m,) = await ctx.tournaments.matches(t.id, 1)
    th = bot.ch("matches").threads[0]
    assert th.members == []  # fake players aren't added to threads
    r = await click(bot, ADMIN, "ready", m.id)
    assert any("acting for" in x for x in r.texts())
    await click(bot, ADMIN, "ready", m.id)
    assert (await ctx.matches.get(m.id)).state == "side_choice"
    await db.dispose()
