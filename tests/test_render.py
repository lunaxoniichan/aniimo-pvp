import io
from pathlib import Path

from PIL import Image
from tests.conftest import ADMIN, game_state, make_tournament, open_matches, play_draft, play_match

from aniimo_pvp.render.board import BoardRenderer
from aniimo_pvp.render.bracket import BracketRenderer
from aniimo_pvp.services.views import load_bracket, load_match_view

OUT = Path(__file__).parent / "output"


def save(name: str, data: bytes) -> Image.Image:
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_bytes(data)
    return Image.open(io.BytesIO(data))


async def test_board_renders_every_stage(ctx):
    r = BoardRenderer()
    t = await make_tournament(ctx, 2, fmt="standard", cap=8)
    (m,) = await open_matches(ctx, t.id)
    await ctx.matches.ready(m.id, m.p1_id)
    await ctx.matches.ready(m.id, m.p2_id)
    mm = await ctx.matches.get(m.id)
    await ctx.matches.choose_side(m.id, mm.side_chooser_id, "A")
    img = save("board_empty.png", r.render(await load_match_view(ctx, m.id)))
    assert img.size == (1280, 720)
    g, _ = await game_state(ctx, m.id)
    for step, slug in enumerate(["a00", "a01", "a02", "a03"]):
        actor = g.side_a_id if ctx.order[step][0] == "A" else g.side_b_id
        await ctx.drafts.submit(m.id, g.id, step, actor, slug)
    ctx.clock.advance(46)
    await ctx.scheduler.run_due()  # step 5 auto pick
    await ctx.drafts.submit(m.id, g.id, 5, g.side_a_id, "a05")
    view = await load_match_view(ctx, m.id)
    assert view.turn == ("B", "ban") and view.steps()[4].auto
    save("board_mid.png", r.render(view))
    await play_draft(ctx, m.id)
    view = await load_match_view(ctx, m.id)
    assert view.turn is None
    save("board_complete.png", r.render(view))
    await ctx.matches.request_rematch(m.id, m.p1_id, "new_draft")
    await ctx.matches.answer_rematch(m.id, m.p2_id, True)
    await ctx.matches.admin_pause(m.id, ADMIN)
    save("board_paused_replay.png", r.render(await load_match_view(ctx, m.id)))


async def test_board_uses_real_thumbnails(make_ctx, tmp_path):
    ctx = await make_ctx()
    from aniimo_pvp.domain.roster import load_roster
    img_dir = tmp_path / "imgs"
    img_dir.mkdir()
    Image.new("RGB", (300, 200), (200, 40, 160)).save(img_dir / "a00.png")
    csv = tmp_path / "r.csv"
    csv.write_text("name,element\n" + "\n".join(f"A{i:02d},Fire" for i in range(20)) + "\n")
    ctx.roster, _ = load_roster(csv, img_dir)
    assert ctx.roster.get("a00").image_path is not None
    t = await make_tournament(ctx, 2)
    (m,) = await open_matches(ctx, t.id)
    await ctx.matches.ready(m.id, m.p1_id)
    await ctx.matches.ready(m.id, m.p2_id)
    mm = await ctx.matches.get(m.id)
    await ctx.matches.choose_side(m.id, mm.side_chooser_id, "A")
    g, _ = await game_state(ctx, m.id)
    await ctx.drafts.submit(m.id, g.id, 0, g.side_a_id, "a01")
    await ctx.drafts.submit(m.id, g.id, 1, g.side_b_id, "a02")
    await ctx.drafts.submit(m.id, g.id, 2, g.side_a_id, "a00")
    img = save("board_real_thumb.png", BoardRenderer().render(await load_match_view(ctx, m.id))).convert("RGB")
    # the first pick card (left panel, top-left card) shows the magenta test image
    px = [img.getpixel((x, y)) for x in range(80, 120) for y in range(390, 420)]
    assert any(abs(p[0] - 200) < 30 and abs(p[2] - 160) < 30 for p in px)


async def test_bracket_small_and_full_size(make_ctx):
    br = BracketRenderer()
    ctx = await make_ctx()
    t = await make_tournament(ctx, 7, cap=8)
    for m in (await open_matches(ctx, t.id))[:1]:
        await play_match(ctx, m.id)
    tt, rounds = await load_bracket(ctx, t.id)
    img = save("bracket_7.png", br.render(tt.name, tt.format, rounds, highlight_match_id=rounds[0][2].match_id))
    assert img.width >= 560 and len(rounds) == 3

    ctx2 = await make_ctx()
    t2 = await make_tournament(ctx2, 128, cap=128, seed="BIG")
    tt2, rounds2 = await load_bracket(ctx2, t2.id)
    img2 = save("bracket_128.png", br.render(tt2.name, tt2.format, rounds2))
    assert len(rounds2) == 7 and img2.height > 2000
