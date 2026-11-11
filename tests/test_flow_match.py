import pytest
from sqlalchemy import select
from tests.conftest import (
    ADMIN,
    game_state,
    make_tournament,
    open_matches,
    play_draft,
    play_game,
    start_game,
)

from aniimo_pvp.db.models import DraftActionRow, Game, Job
from aniimo_pvp.services.errors import NotAllowed, UserError


async def first_match(ctx, n=2, **kw):
    t = await make_tournament(ctx, n, **kw)
    return t, (await open_matches(ctx, t.id))[0]


async def games(ctx, mid):
    async with ctx.db.session() as s:
        return list(await s.scalars(select(Game).where(Game.match_id == mid).order_by(Game.number, Game.attempt)))


async def test_design_example_bo3_with_same_teams_rematch(ctx):
    # design §4.1: G1 Luna, G2 voided (rematch same teams), G2 attempt 2 Kai, G3 Luna -> 2-1
    t, m = await first_match(ctx, 2, fmt="championship", cap=8)  # final of a 2-player Championship is Bo5
    async with ctx.db.transaction() as s:
        mm = await s.get(type(m), m.id)
        mm.bo = 3
    luna, kai = m.p1_id, m.p2_id
    await play_game(ctx, m.id, luna)
    await ctx.matches.next_game(m.id, kai)
    await start_game(ctx, m.id)
    g2, st = await game_state(ctx, m.id)
    picks_before = (st.picks("A"), st.picks("B"))
    await ctx.matches.request_rematch(m.id, kai, "same_teams")
    with pytest.raises(NotAllowed):
        await ctx.matches.answer_rematch(m.id, kai, True)  # requester can't accept their own offer
    await ctx.matches.answer_rematch(m.id, luna, True)
    g2b, st2 = await game_state(ctx, m.id)
    assert (g2b.number, g2b.attempt, g2b.status, g2b.draft_source_id) == (2, 2, "playing", g2.id)
    assert (st2.picks("A"), st2.picks("B")) == picks_before and st2.complete
    assert (await ctx.matches.get(m.id)).state == "playing"
    await ctx.matches.report(m.id, kai, kai)
    await ctx.matches.confirm(m.id, luna)
    await play_game(ctx, m.id, luna)
    gs = await games(ctx, m.id)
    assert [(g.number, g.attempt, g.status) for g in gs] == [
        (1, 1, "confirmed"), (2, 1, "voided"), (2, 2, "confirmed"), (3, 1, "confirmed")]
    assert gs[1].void_reason == "rematch_same_teams"
    final = await ctx.matches.get(m.id)
    assert final.state == "done" and final.winner_id == luna
    assert ctx.notifier.last("game_confirmed")["score"] == (2, 1)


async def test_rematch_new_draft_restarts_at_step_one(ctx):
    _, m = await first_match(ctx)
    await start_game(ctx, m.id)
    await ctx.matches.request_rematch(m.id, m.p1_id, "new_draft")
    await ctx.matches.answer_rematch(m.id, m.p2_id, True)
    g, st = await game_state(ctx, m.id)
    assert (g.number, g.attempt, g.status, st.step) == (1, 2, "drafting", 0)
    assert (await ctx.matches.get(m.id)).state == "drafting"
    await play_draft(ctx, m.id)
    assert (await ctx.matches.get(m.id)).state == "playing"


async def test_rematch_rules(ctx):
    _, m = await first_match(ctx)
    with pytest.raises(UserError):
        await ctx.matches.request_rematch(m.id, m.p1_id, "same_teams")  # not playing yet
    await start_game(ctx, m.id)
    await ctx.matches.request_rematch(m.id, m.p1_id, "same_teams")
    with pytest.raises(UserError):
        await ctx.matches.request_rematch(m.id, m.p2_id, "new_draft")  # answer the open offer instead
    await ctx.matches.answer_rematch(m.id, m.p2_id, False)
    assert "rematch_declined" in ctx.notifier.names("match", m.id)
    await ctx.matches.report(m.id, m.p1_id, m.p1_id)
    with pytest.raises(UserError):
        await ctx.matches.request_rematch(m.id, m.p2_id, "same_teams")  # reported: dispute instead


async def test_dispute_then_admin_set_result_and_admin_replay(ctx):
    _, m = await first_match(ctx, fmt="standard")
    await start_game(ctx, m.id)
    await ctx.matches.report(m.id, m.p1_id, m.p1_id)
    with pytest.raises(NotAllowed):
        await ctx.matches.confirm(m.id, m.p1_id)  # reporter can't confirm
    await ctx.matches.dispute(m.id, m.p2_id)
    assert (await ctx.matches.get(m.id)).state == "disputed"
    new_id = await ctx.matches.admin_replay(m.id, ADMIN, "same_teams")
    g, _ = await game_state(ctx, m.id)
    assert g.id == new_id and g.attempt == 2 and g.status == "playing"
    await ctx.matches.report(m.id, m.p1_id, m.p1_id)
    await ctx.matches.dispute(m.id, m.p2_id)
    await ctx.matches.admin_set_result(m.id, ADMIN, m.p2_id)
    gs = await games(ctx, m.id)
    assert gs[0].void_reason == "admin_replay" and gs[-1].winner_id == m.p2_id
    assert (await ctx.matches.get(m.id)).state == "between_games"


async def test_draft_turn_validation(ctx):
    _, m = await first_match(ctx)
    await ctx.matches.ready(m.id, m.p1_id)
    await ctx.matches.ready(m.id, m.p2_id)
    m = await ctx.matches.get(m.id)
    await ctx.matches.choose_side(m.id, m.side_chooser_id, "B")
    g, st = await game_state(ctx, m.id)
    assert g.side_b_id == m.side_chooser_id
    a = g.side_a_id
    b = g.side_b_id
    with pytest.raises(NotAllowed):
        await ctx.drafts.submit(m.id, g.id, 0, b, "a00")  # A bans first
    with pytest.raises(UserError):
        await ctx.drafts.submit(m.id, g.id, 3, a, "a00")  # stale/wrong step
    await ctx.drafts.submit(m.id, g.id, 0, a, "a00")
    with pytest.raises(UserError):
        await ctx.drafts.submit(m.id, g.id, 1, b, "a00")  # already banned
    with pytest.raises(UserError):
        await ctx.drafts.submit(m.id, g.id, 0, a, "a01")  # double click on an old turn
    opts = await ctx.drafts.options(m.id, g.id, 1, b)
    assert "a00" not in {x.slug for x in opts.available} and opts.kind == "ban"


async def test_draft_timeout_auto_actions(ctx):
    _, m = await first_match(ctx)
    await ctx.matches.ready(m.id, m.p1_id)
    await ctx.matches.ready(m.id, m.p2_id)
    m = await ctx.matches.get(m.id)
    await ctx.matches.choose_side(m.id, m.side_chooser_id, "A")
    ctx.clock.advance(46)
    await ctx.scheduler.run_due()  # step 1 ban -> skipped
    ctx.clock.advance(46)
    await ctx.scheduler.run_due()  # step 2 ban -> skipped
    ctx.clock.advance(46)
    await ctx.scheduler.run_due()  # step 3 pick -> random legal
    g, st = await game_state(ctx, m.id)
    assert [a.kind for a in st.actions] == ["skip", "skip", "pick"] and all(a.auto for a in st.actions)
    assert st.actions[2].slug in ctx.roster.slugs
    ctx.clock.advance(10)
    assert await ctx.scheduler.run_due() == 0  # next timer restarted at the last action


async def test_side_choice_timeout_gives_side_a(ctx):
    _, m = await first_match(ctx)
    await ctx.matches.ready(m.id, m.p1_id)
    await ctx.matches.ready(m.id, m.p2_id)
    m = await ctx.matches.get(m.id)
    ctx.clock.advance(121)
    await ctx.scheduler.run_due()
    g, _ = await game_state(ctx, m.id)
    assert g.side_a_id == m.side_chooser_id and (await ctx.matches.get(m.id)).state == "drafting"
    assert ctx.notifier.last("side_chosen")["auto"] is True


async def test_auto_confirm(ctx):
    _, m = await first_match(ctx)
    await start_game(ctx, m.id)
    await ctx.matches.report(m.id, m.p2_id, m.p2_id)
    ctx.clock.advance(601)
    await ctx.scheduler.run_due()
    assert ctx.notifier.last("game_confirmed")["reason"] == "auto"


async def test_noshow_reminder_escalation_extend_award(ctx):
    _, m = await first_match(ctx, 3)
    await ctx.matches.ready(m.id, m.p1_id)
    ctx.clock.advance(601)
    await ctx.scheduler.run_due()
    assert ctx.notifier.last("noshow_reminder")["missing"] == [m.p2_id]
    ctx.clock.advance(300)
    await ctx.scheduler.run_due()
    assert (await ctx.matches.get(m.id)).state == "noshow"
    await ctx.matches.admin_extend(m.id, ADMIN, 10)
    assert (await ctx.matches.get(m.id)).state == "waiting"
    ctx.clock.advance(601)
    await ctx.scheduler.run_due()
    assert (await ctx.matches.get(m.id)).state == "noshow"
    await ctx.matches.admin_award(m.id, ADMIN, m.p1_id)
    m2 = await ctx.matches.get(m.id)
    assert m2.state == "done" and m2.walkover and m2.winner_id == m.p1_id
    assert m2.p1_rating_delta is None  # walkovers don't change ratings


async def test_ready_after_noshow_still_starts(ctx):
    _, m = await first_match(ctx)
    ctx.clock.advance(15 * 60 + 1)
    await ctx.scheduler.run_due()
    assert (await ctx.matches.get(m.id)).state == "noshow"
    await ctx.matches.ready(m.id, m.p1_id)
    await ctx.matches.ready(m.id, m.p2_id)
    assert (await ctx.matches.get(m.id)).state == "side_choice"


async def test_restart_recovery_gives_full_time_again(ctx):
    _, m = await first_match(ctx)
    await ctx.matches.ready(m.id, m.p1_id)
    await ctx.matches.ready(m.id, m.p2_id)
    mm = await ctx.matches.get(m.id)
    await ctx.matches.choose_side(m.id, mm.side_chooser_id, "A")
    ctx.clock.advance(300)  # bot was offline: the 45 s timer is long overdue
    assert await ctx.scheduler.recover() >= 1
    assert await ctx.scheduler.run_due() == 0
    ctx.clock.advance(44)
    assert await ctx.scheduler.run_due() == 0
    ctx.clock.advance(2)
    await ctx.scheduler.run_due()
    _, st = await game_state(ctx, m.id)
    assert st.step == 1 and st.actions[0].auto


async def test_pause_resume_undo(ctx):
    _, m = await first_match(ctx)
    await ctx.matches.ready(m.id, m.p1_id)
    await ctx.matches.ready(m.id, m.p2_id)
    mm = await ctx.matches.get(m.id)
    await ctx.matches.choose_side(m.id, mm.side_chooser_id, "A")
    g, _ = await game_state(ctx, m.id)
    await ctx.drafts.submit(m.id, g.id, 0, g.side_a_id, "a05")
    await ctx.matches.admin_pause(m.id, ADMIN)
    ctx.clock.advance(100)
    assert await ctx.scheduler.run_due() == 0  # no timer while paused
    with pytest.raises(UserError):
        await ctx.drafts.submit(m.id, g.id, 1, g.side_b_id, "a06")
    await ctx.drafts.admin_undo(m.id, ADMIN)
    _, st = await game_state(ctx, m.id)
    assert st.step == 0 and (await ctx.matches.get(m.id)).state == "paused"
    await ctx.matches.admin_resume(m.id, ADMIN)
    await play_draft(ctx, m.id)
    assert (await ctx.matches.get(m.id)).state == "playing"
    await ctx.drafts.admin_undo(m.id, ADMIN)  # undo right after the draft goes back to drafting
    _, st = await game_state(ctx, m.id)
    assert st.step == 11 and (await ctx.matches.get(m.id)).state == "drafting"
    with pytest.raises(UserError):
        await ctx.drafts.admin_undo(10**6, ADMIN)


async def test_fearless_blocks_own_confirmed_picks_only(make_ctx):
    ctx = await make_ctx()
    _, m = await first_match(ctx, 2, fmt="quick", fearless=True, cap=8)  # 2-player Quick final is Bo3
    await play_game(ctx, m.id, m.p1_id)
    async with ctx.db.session() as s:
        g1_picks = {(r.player_id, r.aniimo_slug) for r in await s.scalars(
            select(DraftActionRow).where(DraftActionRow.kind == "pick"))}
    await ctx.matches.next_game(m.id, m.p1_id)
    mm = await ctx.matches.get(m.id)
    await ctx.matches.choose_side(m.id, mm.side_chooser_id, "A")
    g, st = await game_state(ctx, m.id)
    await ctx.drafts.submit(m.id, g.id, 0, g.side_a_id, "a30")
    await ctx.drafts.submit(m.id, g.id, 1, g.side_b_id, "a31")
    opts = await ctx.drafts.options(m.id, g.id, 2, g.side_a_id)
    mine = {slug for pid, slug in g1_picks if pid == g.side_a_id}
    theirs = {slug for pid, slug in g1_picks if pid != g.side_a_id}
    available = {a.slug for a in opts.available}
    assert mine and not (mine & available) and theirs <= available
    with pytest.raises(UserError):
        await ctx.drafts.submit(m.id, g.id, 2, g.side_a_id, sorted(mine)[0])


async def test_dq_player_with_unknown_next_opponent_forfeits_slot(ctx):
    t = await make_tournament(ctx, 4, fmt="quick", cap=8)
    r1 = await open_matches(ctx, t.id)
    winner_m = await __import__("tests.conftest", fromlist=["play_match"]).play_match(ctx, r1[0].id)
    # the winner of match 0 waits in the final for match 1; disqualify them
    await ctx.matches.admin_dq_player(t.id, ADMIN, winner_m.winner_id)
    final = (await ctx.tournaments.matches(t.id, 2))[0]
    assert final.forfeit_slot is not None
    m1 = await ctx.matches.get(r1[1].id)
    from tests.conftest import play_match
    done = await play_match(ctx, m1.id)
    assert ctx.notifier.last("round_complete")["round"] == 1
    await ctx.tournaments.start_next_round(t.id, ADMIN)
    final = (await ctx.tournaments.matches(t.id, 2))[0]
    assert final.state == "done" and final.winner_id == done.winner_id and final.walkover
    assert (await ctx.tournaments.get(t.id)).status == "finished"


async def test_dq_player_in_open_match_awards_opponent(ctx):
    t = await make_tournament(ctx, 2)
    (m,) = await open_matches(ctx, t.id)
    await ctx.matches.admin_dq_player(t.id, ADMIN, m.p1_id)
    m = await ctx.matches.get(m.id)
    assert m.state == "done" and m.winner_id == m.p2_id
    t = await ctx.tournaments.get(t.id)
    assert t.status == "finished" and t.champion_id == m.p2_id


async def test_dq_both_lets_next_opponent_walk_through(ctx):
    t = await make_tournament(ctx, 4, fmt="quick", cap=8)
    a, b = await open_matches(ctx, t.id)
    await ctx.matches.admin_dq_both(a.id, ADMIN)
    from tests.conftest import play_match
    done = await play_match(ctx, b.id)
    await ctx.tournaments.start_next_round(t.id, ADMIN)
    final = (await ctx.tournaments.matches(t.id, 2))[0]
    assert final.winner_id == done.winner_id


async def test_swap_player_before_playing(ctx):
    t = await make_tournament(ctx, 2)
    (m,) = await open_matches(ctx, t.id)
    await ctx.matches.admin_swap(t.id, ADMIN, m.p1_id, 555555555555555555, "999999", "Sub")
    m2 = await ctx.matches.get(m.id)
    assert m2.p1_id == 555555555555555555 and not m2.p1_ready
    assert ctx.notifier.last("player_swapped")["new_id"] == 555555555555555555
    await ctx.matches.ready(m.id, m2.p1_id)
    await ctx.matches.ready(m.id, m2.p2_id)
    with pytest.raises(UserError):  # match already started
        await ctx.matches.admin_swap(t.id, ADMIN, m2.p2_id, 666666666666666666, "888888", "Sub2")


async def test_cancel_stops_timers_and_actions(ctx):
    t, m = await first_match(ctx)
    await ctx.tournaments.cancel(t.id, ADMIN)
    async with ctx.db.session() as s:
        pending = list(await s.scalars(select(Job).where(Job.status == "pending", Job.kind != "archive_thread")))
    assert pending == []
    with pytest.raises(UserError):
        await ctx.matches.ready(m.id, m.p1_id)


async def test_screenshot_attached_and_call_admin(ctx):
    _, m = await first_match(ctx)
    await start_game(ctx, m.id)
    assert await ctx.matches.attach_screenshot(m.id, m.p1_id, "https://cdn.example/x.png")
    assert not await ctx.matches.attach_screenshot(m.id, 42, "https://cdn.example/y.png")
    g, _ = await game_state(ctx, m.id)
    assert g.screenshot_url.endswith("x.png")
    await ctx.matches.call_admin(m.id, m.p2_id)
    assert ctx.notifier.last("call_admin")["by"] == m.p2_id


async def test_stats_and_leaderboard(ctx):
    t = await make_tournament(ctx, 4, fmt="quick", cap=8)
    from tests.conftest import play_match
    for m in await open_matches(ctx, t.id):
        await play_match(ctx, m.id)
    await ctx.tournaments.start_next_round(t.id, ADMIN)
    for m in await open_matches(ctx, t.id):
        await play_match(ctx, m.id)
    drafts, stats = await ctx.stats.aniimo(t.id)
    assert drafts >= 4 and stats and sum(x.picks for x in stats) == drafts * 8
    summary = await ctx.stats.summary(t.id)
    assert summary["most_picked"]
    board = await ctx.stats.leaderboard()
    assert board[0].rating > 1000
    t_done = await ctx.tournaments.get(t.id)
    prof = await ctx.stats.profile(t_done.champion_id)
    assert prof.matches == 2 and prof.games_won >= 2 and prof.top_picks
