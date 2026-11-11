import random

import pytest

from aniimo_pvp.clock import FakeClock
from aniimo_pvp.config import Settings
from aniimo_pvp.db.database import Database
from aniimo_pvp.domain.roster import ELEMENTS, load_roster
from aniimo_pvp.services.common import current_game, draft_rows, to_state
from aniimo_pvp.services.context import build_context
from aniimo_pvp.services.notifier import RecordingNotifier

GUILD = 999
ADMIN = 1


def player_id(i: int) -> int:
    return 100000000000000000 + i


@pytest.fixture
def roster_file(tmp_path):
    lines = ["slug,name,element,role,image"]
    for i in range(36):
        el = ELEMENTS[i % len(ELEMENTS)]
        lines.append(f"a{i:02d},Aniimo {i:02d},{el},,")
    p = tmp_path / "roster.csv"
    p.write_text("\n".join(lines) + "\n")
    return p


@pytest.fixture
async def make_ctx(tmp_path, roster_file):
    created = []

    async def factory(**overrides):
        settings = Settings(token="x", guild_id=GUILD, admin_role_id=5, **overrides)
        db = Database(":memory:")
        await db.create_all()
        roster, _ = load_roster(roster_file, tmp_path)
        ctx = build_context(settings, db, roster, RecordingNotifier(), clock=FakeClock(), rng=random.Random(7))
        created.append(ctx)
        return ctx

    yield factory
    for ctx in created:
        await ctx.db.dispose()


@pytest.fixture
async def ctx(make_ctx):
    return await make_ctx()


async def lookup_all(discord_id: int) -> str | None:
    return f"P{discord_id % 1000}"


async def make_tournament(ctx, n: int, fmt: str = "standard", cap: int = 32, fearless: bool = False,
                          seed: str = "TEST"):
    t = await ctx.tournaments.create(GUILD, "Test Cup", ADMIN)
    await ctx.tournaments.set_options(t.id, ADMIN, cap=cap, fmt=fmt, fearless=fearless)
    await ctx.tournaments.to_registration(t.id, ADMIN)
    csv_text = "discord_id,aniimo_uid,display_name\n" + "\n".join(
        f"{player_id(i)},{200000 + i},P{i}" for i in range(1, n + 1))
    preview = await ctx.tournaments.preview_players(t.id, ADMIN, csv_text, lookup_all)
    assert preview.can_confirm, preview.blocking
    await ctx.tournaments.confirm_players(t.id, ADMIN)
    await ctx.tournaments.start(t.id, ADMIN, seed=seed)
    return await ctx.tournaments.get(t.id)


async def open_matches(ctx, tid: int):
    return [m for m in await ctx.tournaments.matches(tid) if m.state not in ("pending", "done")]


async def game_state(ctx, mid: int):
    async with ctx.db.session() as s:
        g = await current_game(s, mid)
        rows = await draft_rows(s, g.id)
        return g, to_state(ctx.order, rows)


async def play_draft(ctx, mid: int):
    while True:
        m = await ctx.matches.get(mid)
        if m.state != "drafting":
            return
        g, st = await game_state(ctx, mid)
        side = st.current[0]
        actor = g.side_a_id if side == "A" else g.side_b_id
        opts = await ctx.drafts.options(mid, g.id, st.step, actor)
        await ctx.drafts.submit(mid, g.id, st.step, actor, opts.available[0].slug)


async def start_game(ctx, mid: int, chooser_side: str = "A"):
    m = await ctx.matches.get(mid)
    if m.state in ("waiting", "noshow"):
        await ctx.matches.ready(mid, m.p1_id)
        await ctx.matches.ready(mid, m.p2_id)
        m = await ctx.matches.get(mid)
    assert m.state == "side_choice"
    await ctx.matches.choose_side(mid, m.side_chooser_id, chooser_side)
    await play_draft(ctx, mid)


async def play_game(ctx, mid: int, winner: int):
    m = await ctx.matches.get(mid)
    if m.state == "between_games":
        await ctx.matches.next_game(mid, m.p1_id)
    await start_game(ctx, mid)
    m = await ctx.matches.get(mid)
    loser = m.p2_id if winner == m.p1_id else m.p1_id
    await ctx.matches.report(mid, winner, winner)
    await ctx.matches.confirm(mid, loser)


async def play_match(ctx, mid: int, winner_slot: int = 1):
    while True:
        m = await ctx.matches.get(mid)
        if m.state == "done":
            return m
        await play_game(ctx, mid, m.p1_id if winner_slot == 1 else m.p2_id)
