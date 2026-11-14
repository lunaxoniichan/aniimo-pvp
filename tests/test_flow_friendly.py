"""Friendly duels: challenge → room → drafts → end. No results, ratings or stats."""

import pytest
from sqlalchemy import select, text
from tests.conftest import ADMIN, GUILD, game_state, play_draft, player_id

from aniimo_pvp.db.database import Database
from aniimo_pvp.db.models import DraftActionRow, Game, Job, Player, Tournament
from aniimo_pvp.services.errors import NotAllowed, UserError

CH = 4242  # the #friendly channel
LUNA, KAI, MIRA = player_id(1), player_id(2), player_id(3)


async def challenge(ctx, a=LUNA, b=KAI, **kw):
    t, m = await ctx.friendlies.challenge(GUILD, CH, (a, f"P{a % 1000}"), (b, f"P{b % 1000}"), **kw)
    return t, m


async def accepted(ctx, **kw):
    t, m = await challenge(ctx, **kw)
    await ctx.friendlies.accept(t.id, KAI)
    return t, m


async def draft_once(ctx, mid, side="A"):
    """From the ready check or a side choice, play one whole draft."""
    m = await ctx.matches.get(mid)
    if m.state == "waiting":
        await ctx.matches.ready(mid, m.p1_id)
        await ctx.matches.ready(mid, m.p2_id)
        m = await ctx.matches.get(mid)
    assert m.state == "side_choice"
    chooser = m.side_chooser_id
    await ctx.matches.choose_side(mid, chooser, side)
    await play_draft(ctx, mid)
    assert (await ctx.matches.get(mid)).state == "playing"
    return chooser


async def games(ctx, mid):
    async with ctx.db.session() as s:
        return list(await s.scalars(select(Game).where(Game.match_id == mid).order_by(Game.number, Game.attempt)))


async def pending_jobs(ctx, kind):
    async with ctx.db.session() as s:
        return list(await s.scalars(select(Job).where(Job.kind == kind, Job.status == "pending")))


# ----- the happy path -----


async def test_full_duel_two_drafts_then_end(ctx):
    t, m = await challenge(ctx)
    assert (t.kind, t.status, t.bracket_size, m.bo, m.state) == ("friendly", "registration", 2, 1, "pending")
    assert ctx.notifier.names("tournament") == ["duel_challenged"]
    await ctx.friendlies.accept(t.id, KAI)
    assert (await ctx.matches.get(m.id)).state == "waiting"
    assert "opened" in ctx.notifier.names("match", m.id)
    assert ctx.notifier.names("tournament")[-1] == "duel_accepted"

    first = await draft_once(ctx, m.id)
    assert len(await pending_jobs(ctx, "friendly_idle")) == 1
    await ctx.friendlies.new_draft(m.id, LUNA)
    mm = await ctx.matches.get(m.id)
    assert mm.state == "side_choice" and mm.side_chooser_id != first  # the other player picks the side this time
    assert ctx.notifier.last("side_choice") | {"game_id": 0} == {
        "game_id": 0,
        "chooser_id": mm.side_chooser_id,
        "coin": False,
        "number": 2,
    }
    assert not await pending_jobs(ctx, "friendly_idle")  # a new draft stops the idle timer
    second = await draft_once(ctx, m.id)
    assert second != first
    await ctx.friendlies.new_draft(m.id, KAI)
    assert (await ctx.matches.get(m.id)).side_chooser_id == first  # and back again

    assert await ctx.friendlies.end(m.id, KAI) == 2  # the third draft never started
    done = await ctx.matches.get(m.id)
    assert (done.state, done.winner_id, done.walkover) == ("done", None, False)
    tt = await ctx.tournaments.get(t.id)
    assert tt.status == "finished" and tt.champion_id is None
    assert [(g.number, g.status, g.void_reason) for g in await games(ctx, m.id)] == [
        (1, "finished", None),
        (2, "finished", None),
        (3, "voided", "duel_ended"),
    ]
    info = ctx.notifier.last("duel_ended")
    assert (info["drafts"], info["by"], info["reason"]) == (2, KAI, "ended")
    assert ctx.notifier.last("done")["reason"] == "duel_ended"
    assert not await pending_jobs(ctx, "side_choice")
    assert "advanced" not in ctx.notifier.names("match", m.id)
    assert "finished" not in ctx.notifier.names("tournament")  # no champion post


async def test_no_ratings_stats_or_leaderboard(ctx):
    _, m = await accepted(ctx)
    await draft_once(ctx, m.id)
    await ctx.friendlies.end(m.id, LUNA)
    async with ctx.db.session() as s:
        for pid in (LUNA, KAI):
            p = await s.get(Player, pid)
            assert (p.rating, p.wins, p.losses) == (1000, 0, 0)
    assert await ctx.stats.leaderboard() == []
    drafts, rows = await ctx.stats.aniimo()
    assert drafts == 0 and all(r.picks == 0 and r.bans == 0 for r in rows)
    prof = await ctx.stats.profile(LUNA)
    assert (prof.matches, prof.games_won, prof.games_lost, prof.friendly_drafts) == (0, 0, 0, 1)


async def test_both_players_can_duel_again_after_it_ends(ctx):
    _, m = await accepted(ctx)
    await ctx.friendlies.end(m.id, LUNA)
    t2, _ = await challenge(ctx, KAI, LUNA)
    assert t2.status == "registration"


# ----- the challenge -----


async def test_challenge_rules(ctx):
    with pytest.raises(UserError, match="yourself"):
        await challenge(ctx, LUNA, LUNA)
    t, _ = await challenge(ctx)
    with pytest.raises(UserError, match="already have an open duel"):
        await challenge(ctx, LUNA, MIRA)
    with pytest.raises(UserError, match="already has an open duel"):
        await challenge(ctx, MIRA, KAI)
    with pytest.raises(NotAllowed):
        await ctx.friendlies.accept(t.id, LUNA)  # the challenger can't accept for the friend
    with pytest.raises(NotAllowed):
        await ctx.friendlies.decline(t.id, MIRA)
    with pytest.raises(NotAllowed):
        await ctx.friendlies.cancel_challenge(t.id, KAI)


async def test_decline(ctx):
    t, m = await challenge(ctx)
    await ctx.friendlies.decline(t.id, KAI)
    assert (await ctx.tournaments.get(t.id)).status == "cancelled"
    assert (await ctx.matches.get(m.id)).state == "done"
    assert ctx.notifier.last("duel_declined")["by"] == KAI
    with pytest.raises(UserError, match="no longer open"):
        await ctx.friendlies.accept(t.id, KAI)
    await challenge(ctx)  # free to challenge again


async def test_cancel_by_challenger_or_admin(ctx):
    t, _ = await challenge(ctx)
    await ctx.friendlies.cancel_challenge(t.id, LUNA)
    assert ctx.notifier.last("duel_cancelled")["by"] == LUNA
    t2, _ = await challenge(ctx)
    await ctx.friendlies.cancel_challenge(t2.id, ADMIN, is_admin=True)
    assert (await ctx.tournaments.get(t2.id)).status == "cancelled"


async def test_challenge_expires(ctx):
    t, _ = await challenge(ctx)
    ctx.clock.advance(ctx.settings.timers.challenge_minutes * 60 - 5)
    assert await ctx.scheduler.run_due() == 0
    ctx.clock.advance(10)
    await ctx.scheduler.run_due()
    assert (await ctx.tournaments.get(t.id)).status == "cancelled"
    assert ctx.notifier.last("duel_expired")["by"] is None
    with pytest.raises(UserError):
        await ctx.friendlies.accept(t.id, KAI)


async def test_accept_cancels_the_expiry(ctx):
    t, _ = await accepted(ctx)
    ctx.clock.advance(ctx.settings.timers.challenge_minutes * 60 + 5)
    await ctx.scheduler.run_due()
    assert (await ctx.tournaments.get(t.id)).status == "running"
    assert "duel_expired" not in ctx.notifier.names("tournament")


# ----- what a friendly doesn't do -----


async def test_results_and_series_actions_are_blocked(ctx):
    _, m = await accepted(ctx)
    await draft_once(ctx, m.id)
    for call in (
        lambda: ctx.matches.report(m.id, LUNA, LUNA),
        lambda: ctx.matches.next_game(m.id, LUNA),
        lambda: ctx.matches.request_rematch(m.id, LUNA, "new_draft"),
        lambda: ctx.matches.admin_award(m.id, ADMIN, LUNA),
        lambda: ctx.matches.admin_set_result(m.id, ADMIN, LUNA),
        lambda: ctx.matches.admin_dq_both(m.id, ADMIN),
        lambda: ctx.matches.admin_replay(m.id, ADMIN, "new_draft"),
    ):
        with pytest.raises(UserError, match="Friendly duels don't record results"):
            await call()
    assert (await ctx.matches.get(m.id)).state == "playing"


async def test_new_draft_rules(ctx):
    t, m = await accepted(ctx)
    with pytest.raises(UserError, match="Finish the current draft"):
        await ctx.friendlies.new_draft(m.id, LUNA)
    await draft_once(ctx, m.id)
    with pytest.raises(NotAllowed):
        await ctx.friendlies.new_draft(m.id, MIRA)
    with pytest.raises(NotAllowed):
        await ctx.friendlies.end(m.id, MIRA)
    # tournament matches don't have New draft
    from tests.conftest import make_tournament, open_matches

    tt = await make_tournament(ctx, 2)
    tm = (await open_matches(ctx, tt.id))[0]
    with pytest.raises(UserError):
        await ctx.friendlies.new_draft(tm.id, tm.p1_id)
    with pytest.raises(UserError):
        await ctx.friendlies.end(tm.id, tm.p1_id)


async def test_tournaments_and_duels_dont_block_each_other(ctx):
    _, m = await accepted(ctx)
    assert await ctx.tournaments.active(GUILD) is None
    t = await ctx.tournaments.create(GUILD, "Cup", ADMIN)  # an open duel doesn't count as "a tournament running"
    assert (await ctx.tournaments.active(GUILD)).id == t.id
    assert (await ctx.tournaments.latest(GUILD)).id == t.id
    await challenge(ctx, MIRA, player_id(4))  # and a tournament doesn't block duels
    assert (await ctx.matches.get(m.id)).state == "waiting"


# ----- ending early and abandoned duels -----


async def test_end_mid_draft_voids_it_and_cancels_timers(ctx):
    t, m = await accepted(ctx)
    await ctx.matches.ready(m.id, LUNA)
    await ctx.matches.ready(m.id, KAI)
    mm = await ctx.matches.get(m.id)
    await ctx.matches.choose_side(m.id, mm.side_chooser_id, "A")
    g, st = await game_state(ctx, m.id)
    await ctx.drafts.submit(m.id, g.id, 0, g.side_a_id, "a01")
    assert await ctx.friendlies.end(m.id, ADMIN, is_admin=True) == 0
    assert (await ctx.tournaments.get(t.id)).status == "cancelled"  # nothing was played
    assert [(x.status, x.void_reason) for x in await games(ctx, m.id)] == [("voided", "duel_ended")]
    assert not await pending_jobs(ctx, "draft_step")
    with pytest.raises(UserError):
        await ctx.friendlies.end(m.id, LUNA)
    with pytest.raises(UserError):  # the room is closed
        await ctx.drafts.submit(m.id, g.id, 1, g.side_b_id, "a02")


async def test_idle_duel_ends_by_itself(ctx):
    t, m = await accepted(ctx)
    await draft_once(ctx, m.id)
    ctx.clock.advance(ctx.settings.timers.friendly_idle_minutes * 60 + 1)
    await ctx.scheduler.run_due()
    assert (await ctx.matches.get(m.id)).state == "done"
    assert (await ctx.tournaments.get(t.id)).status == "finished"
    assert ctx.notifier.last("duel_ended")["reason"] == "idle"
    assert [g.status for g in await games(ctx, m.id)] == ["finished"]


async def test_noshow_ends_the_duel_without_admin(ctx):
    t, m = await accepted(ctx)
    await ctx.matches.ready(m.id, LUNA)
    ctx.clock.advance(ctx.settings.timers.noshow_escalate_minutes * 60 + 1)
    await ctx.scheduler.run_due()
    assert (await ctx.matches.get(m.id)).state == "done"
    assert (await ctx.tournaments.get(t.id)).status == "cancelled"
    assert ctx.notifier.last("duel_ended")["reason"] == "noshow"
    assert "noshow" not in ctx.notifier.names("match", m.id)  # no admin buttons


async def test_fearless_duel_locks_own_earlier_picks(ctx):
    _, m = await accepted(ctx, fearless=True)
    await draft_once(ctx, m.id)
    async with ctx.db.session() as s:
        g1_picks = {
            (r.player_id, r.aniimo_slug)
            for r in await s.scalars(select(DraftActionRow).where(DraftActionRow.kind == "pick"))
        }
    await ctx.friendlies.new_draft(m.id, LUNA)
    mm = await ctx.matches.get(m.id)
    await ctx.matches.choose_side(m.id, mm.side_chooser_id, "A")
    g, _ = await game_state(ctx, m.id)
    await ctx.drafts.submit(m.id, g.id, 0, g.side_a_id, "a30")
    await ctx.drafts.submit(m.id, g.id, 1, g.side_b_id, "a31")
    opts = await ctx.drafts.options(m.id, g.id, 2, g.side_a_id)
    mine = {slug for pid, slug in g1_picks if pid == g.side_a_id}
    theirs = {slug for pid, slug in g1_picks if pid != g.side_a_id}
    available = {a.slug for a in opts.available}
    assert mine and not (mine & available) and theirs <= available


async def test_restart_keeps_the_idle_timer(ctx):
    _, m = await accepted(ctx)
    await draft_once(ctx, m.id)
    ctx.clock.advance(ctx.settings.timers.friendly_idle_minutes * 60 + 60)  # bot was offline past the deadline
    await ctx.scheduler.recover()
    assert await ctx.scheduler.run_due() == 0  # overdue timers restart with full time
    assert (await ctx.matches.get(m.id)).state == "playing"


# ----- Aniimo UID -----


async def test_register_uid(ctx):
    await ctx.friendlies.register_uid(MIRA, "Mira", "100234567")
    _, m = await challenge(ctx, MIRA, KAI)
    async with ctx.db.session() as s:
        assert (await s.get(Player, MIRA)).aniimo_uid == "100234567"
        assert (await s.get(Player, KAI)).aniimo_uid == ""  # unknown until they register
    await ctx.friendlies.register_uid(KAI, "Kai", " K-77 ")
    async with ctx.db.session() as s:
        assert (await s.get(Player, KAI)).aniimo_uid == "K-77"
    with pytest.raises(UserError):
        await ctx.friendlies.register_uid(KAI, "Kai", "bad uid!")
    from aniimo_pvp.services.views import load_match_view

    v = await load_match_view(ctx, m.id)
    assert v.is_friendly and v.label == "Friendly" and v.round_name == "Friendly duel"
    assert v.p1.uid == "100234567" and v.p2.uid == "K-77"


# ----- upgrading an older database -----


async def test_old_database_gets_the_kind_column(tmp_path):
    path = tmp_path / "old.db"
    db = Database(str(path))
    await db.create_all()
    async with db.engine.begin() as conn:  # make it look like a database from before friendly duels
        await conn.exec_driver_sql(
            "INSERT INTO tournaments (guild_id, name, status, player_cap, format, fearless, current_round,"
            " completed_round, created_by, created_at) VALUES (1, 'Old Cup', 'finished', 8, 'quick', 0, 1, 1, 1,"
            " '2026-01-01 00:00:00')"
        )
        await conn.exec_driver_sql("DROP INDEX IF EXISTS ix_tournaments_kind")
        await conn.exec_driver_sql("ALTER TABLE tournaments DROP COLUMN kind")
    await db.dispose()

    db = Database(str(path))
    assert await db.create_all() == ["tournaments.kind"]
    async with db.session() as s:
        t = await s.scalar(select(Tournament))
        assert (t.name, t.kind) == ("Old Cup", "tournament")
        indexes = {r[1] for r in (await s.execute(text("PRAGMA index_list(tournaments)"))).all()}
        assert "ix_tournaments_kind" in indexes
    assert await db.create_all() == []  # already up to date
    await db.dispose()
