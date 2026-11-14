import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from tests.conftest import ADMIN, GUILD, lookup_all, make_tournament, open_matches, play_match, player_id

from aniimo_pvp.db.models import Entry, Game, Player
from aniimo_pvp.services.errors import NotAllowed, UserError


async def test_full_standard_tournament_7_players(ctx):
    n = ctx.notifier
    t = await make_tournament(ctx, 7, fmt="standard", cap=32)
    assert t.status == "running" and t.bracket_size == 8 and t.rng_seed == "TEST"
    r1 = await ctx.tournaments.matches(t.id, 1)
    assert [m.bo for m in r1] == [3, 3, 3, 3]  # 8-bracket starts at the quarterfinal: Bo3
    assert sum(m.is_bye for m in r1) == 1
    assert len(await open_matches(ctx, t.id)) == 3
    assert n.names("tournament")[:4] == ["setup_created", "setup_changed", "registration_opened", "preview"]
    assert "started" in n.names("tournament") and "round_opened" in n.names("tournament")

    for m in await open_matches(ctx, t.id):
        await play_match(ctx, m.id, winner_slot=1)
    assert n.last("round_complete")["next_round"] == 2
    with pytest.raises(UserError):
        await ctx.matches.next_game(r1[0].id, ADMIN)  # finished matches have no next game

    assert await ctx.tournaments.start_next_round(t.id, ADMIN) == 2
    semis = await open_matches(ctx, t.id)
    assert len(semis) == 2 and all(m.bo == 3 for m in semis)
    for m in semis:
        await play_match(ctx, m.id, winner_slot=2)
    await ctx.tournaments.start_next_round(t.id, ADMIN)
    (final,) = await open_matches(ctx, t.id)
    assert final.bo == 5
    done = await play_match(ctx, final.id, winner_slot=1)

    t = await ctx.tournaments.get(t.id)
    assert t.status == "finished" and t.champion_id == done.winner_id
    assert n.last("finished")["champion_id"] == done.winner_id
    async with ctx.db.session() as s:
        entries = list(await s.scalars(select(Entry).where(Entry.tournament_id == t.id)))
        assert sorted(e.status for e in entries).count("eliminated") == 6
        champ = await s.get(Player, done.winner_id)
        assert champ.rating > 1000 and champ.wins == 3
    with pytest.raises(UserError):
        await ctx.tournaments.start_next_round(t.id, ADMIN)


async def test_two_players_single_final(ctx):
    t = await make_tournament(ctx, 2, fmt="quick", cap=8)
    (m,) = await open_matches(ctx, t.id)
    assert m.bo == 3
    await play_match(ctx, m.id)
    assert (await ctx.tournaments.get(t.id)).status == "finished"


async def test_bo1_ends_after_one_confirmed_game_and_has_no_next_game(ctx):
    t = await make_tournament(ctx, 4, fmt="quick", cap=8)
    m = (await open_matches(ctx, t.id))[0]
    assert m.bo == 1
    from tests.conftest import play_game

    await play_game(ctx, m.id, m.p1_id)
    m = await ctx.matches.get(m.id)
    assert m.state == "done" and m.winner_id == m.p1_id
    assert "between_games" not in ctx.notifier.names("match", m.id)
    with pytest.raises(UserError):
        await ctx.matches.next_game(m.id, m.p1_id)


async def test_only_one_active_tournament_and_option_locks(ctx):
    t = await make_tournament(ctx, 3)
    with pytest.raises(UserError):
        await ctx.tournaments.create(GUILD, "Another", ADMIN)
    with pytest.raises(UserError):
        await ctx.tournaments.set_options(t.id, ADMIN, cap=64)
    await ctx.tournaments.cancel(t.id, ADMIN)
    t2 = await ctx.tournaments.create(GUILD, "Another", ADMIN)
    assert t2.status == "setup"
    with pytest.raises(UserError):
        await ctx.tournaments.set_options(t2.id, ADMIN, cap=12)
    with pytest.raises(UserError):
        await ctx.tournaments.set_options(t2.id, ADMIN, fmt="weird")


async def test_csv_preview_blocks_over_cap_and_non_members(make_ctx):
    ctx = await make_ctx()
    t = await ctx.tournaments.create(GUILD, "Cap", ADMIN)
    await ctx.tournaments.set_options(t.id, ADMIN, cap=8)
    text = "\n".join(f"{player_id(i)},{300 + i}" for i in range(1, 10))
    preview = await ctx.tournaments.preview_players(t.id, ADMIN, text, lookup_all)
    assert not preview.can_confirm and "more than the cap" in preview.blocking[0]
    with pytest.raises(UserError):
        await ctx.tournaments.confirm_players(t.id, ADMIN)

    async def only_some(did):
        return "Member" if did != player_id(2) else None

    text = "\n".join(f"{player_id(i)},{300 + i}" for i in range(1, 6))
    preview = await ctx.tournaments.preview_players(t.id, ADMIN, text, only_some)
    assert preview.can_confirm and len(preview.rows) == 4 and preview.bracket_size == 4 and preview.byes == 0
    assert any("not a member" in i.message for i in preview.issues)
    assert await ctx.tournaments.confirm_players(t.id, ADMIN) == 4


async def test_dev_mode_allows_fake_players(make_ctx):
    ctx = await make_ctx(dev_mode=True)
    t = await ctx.tournaments.create(GUILD, "Dev", ADMIN)

    async def nobody(_):
        return None

    preview = await ctx.tournaments.preview_players(t.id, ADMIN, "11,1\n22,2\n33,3\n", nobody)
    assert preview.can_confirm and all(r.fake for r in preview.rows)
    await ctx.tournaments.confirm_players(t.id, ADMIN)
    async with ctx.db.session() as s:
        assert (await s.get(Player, 11)).is_fake


async def test_game_row_uniqueness_is_enforced(ctx):
    t = await make_tournament(ctx, 2)
    (m,) = await open_matches(ctx, t.id)
    with pytest.raises(IntegrityError):
        async with ctx.db.transaction() as s:
            s.add(Game(match_id=m.id, number=1, attempt=1, status="voided"))
            s.add(Game(match_id=m.id, number=1, attempt=1, status="voided"))
    with pytest.raises(IntegrityError):  # two live attempts of the same game number
        async with ctx.db.transaction() as s:
            s.add(Game(match_id=m.id, number=2, attempt=1, status="playing"))
            s.add(Game(match_id=m.id, number=2, attempt=2, status="playing"))


async def test_permissions(ctx):
    t = await make_tournament(ctx, 2)
    (m,) = await open_matches(ctx, t.id)
    with pytest.raises(NotAllowed):
        await ctx.matches.ready(m.id, 12345)
    await ctx.matches.ready(m.id, m.p1_id)
    await ctx.matches.ready(m.id, m.p2_id)
    m = await ctx.matches.get(m.id)
    other = m.p2_id if m.side_chooser_id == m.p1_id else m.p1_id
    with pytest.raises(NotAllowed):
        await ctx.matches.choose_side(m.id, other, "A")
    with pytest.raises(UserError):
        await ctx.matches.report(m.id, m.p1_id, m.p1_id)  # not playing yet
