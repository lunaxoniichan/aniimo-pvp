"""Command line: aniimo-pvp [run|check|preview|init]  (or: python -m aniimo_pvp ...)"""

from __future__ import annotations

import argparse
import asyncio
import random
import shutil
import sys
from pathlib import Path

from .config import ConfigError, Settings, load_settings
from .domain.roster import RosterError, load_roster

ROOT = Path.cwd()


def cmd_init(_args) -> int:
    created = []
    for src, dest in (
        ("config.example.yaml", "config.yaml"),
        (".env.example", ".env"),
        ("data/roster.example.csv", "data/roster.csv"),
    ):
        s, d = ROOT / src, ROOT / dest
        if s.exists() and not d.exists():
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(s, d)
            created.append(dest)
    print("Created: " + ", ".join(created) if created else "Nothing to create (files already exist).")
    print("Next: put your bot token in .env, fill the IDs in config.yaml, then run: aniimo-pvp check")
    return 0


def _load(args) -> Settings:
    return load_settings(args.config, args.env)


def cmd_check(args) -> int:
    ok = True
    try:
        settings = _load(args)
    except ConfigError as e:
        print(f"✗ {e}")
        return 1
    problems = settings.problems()
    for p in problems:
        print(f"✗ {p}")
    ok &= not problems
    try:
        roster, warnings = load_roster(settings.paths.roster_csv, settings.paths.images_dir)
        with_img = sum(1 for a in roster.items if a.image_path)
        print(f"✓ Roster: {len(roster)} Aniimo, {with_img} with thumbnails, elements: {', '.join(roster.elements())}")
        for w in warnings:
            print(f"  ! {w}")
    except RosterError as e:
        print(f"✗ Roster: {e}")
        ok = False
    fonts = Path(settings.paths.fonts_dir)
    print(("✓" if (fonts / "Inter-Regular.otf").exists() else "!") + f" Fonts in {fonts}")
    print(f"✓ Draft order: {len(settings.draft_order)} steps" if settings.draft_order else "")
    print(f"  Dev mode: {'ON (test players allowed, admins can act for them)' if settings.dev_mode else 'off'}")
    print("All good. Start the bot with: aniimo-pvp run" if ok else "Fix the items marked ✗ and run check again.")
    return 0 if ok else 1


async def _preview(settings: Settings, out: Path) -> list[Path]:
    from .clock import FakeClock
    from .db.database import Database
    from .render.board import BoardRenderer
    from .render.bracket import BracketRenderer
    from .render.fonts import Fonts
    from .render.thumbs import ThumbCache
    from .services.common import current_game, draft_rows, to_state
    from .services.context import build_context
    from .services.notifier import RecordingNotifier
    from .services.views import load_bracket, load_match_view

    roster, _ = load_roster(settings.paths.roster_csv, settings.paths.images_dir)
    s = settings.model_copy(update={"dev_mode": True})
    db = Database(":memory:")
    await db.create_all()
    ctx = build_context(s, db, roster, RecordingNotifier(), clock=FakeClock(), rng=random.Random(3))
    fonts = Fonts(settings.paths.fonts_dir)
    board, bracket = BoardRenderer(fonts, ThumbCache(fonts)), BracketRenderer(fonts)
    t = await ctx.tournaments.create(1, "Preview Cup", 1)
    names = ["Luna", "Kai", "Mio", "Rex", "Aya", "Tomo", "Zed"]
    csv = "\n".join(f"{i + 1},{100000 + i},{n}" for i, n in enumerate(names))
    await ctx.tournaments.preview_players(t.id, 1, csv)
    await ctx.tournaments.confirm_players(t.id, 1)
    await ctx.tournaments.start(t.id, 1, seed="PREVIEW")
    m = next(m for m in await ctx.tournaments.matches(t.id, 1) if m.state == "waiting")
    await ctx.matches.ready(m.id, m.p1_id)
    await ctx.matches.ready(m.id, m.p2_id)
    m = await ctx.matches.get(m.id)
    await ctx.matches.choose_side(m.id, m.side_chooser_id, "A")
    out.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 - one-shot CLI
    files = []
    rng = random.Random(5)
    for step in range(len(s.draft_order)):
        async with db.session() as sess:
            g = await current_game(sess, m.id)
            st = to_state(s.draft_order, await draft_rows(sess, g.id))
        if step == 6:
            p = out / "board_mid_draft.png"
            p.write_bytes(board.render(await load_match_view(ctx, m.id)))
            files.append(p)
        side = st.current[0]
        actor = g.side_a_id if side == "A" else g.side_b_id
        opts = await ctx.drafts.options(m.id, g.id, step, actor)
        await ctx.drafts.submit(m.id, g.id, step, actor, rng.choice(opts.available).slug)
    p = out / "board_complete.png"
    p.write_bytes(board.render(await load_match_view(ctx, m.id)))
    files.append(p)
    tt, rounds = await load_bracket(ctx, t.id)
    p = out / "bracket.png"
    p.write_bytes(bracket.render(tt.name, tt.format, rounds))
    files.append(p)
    await db.dispose()
    return files


def cmd_preview(args) -> int:
    try:
        settings = _load(args)
    except ConfigError:
        settings = Settings().resolve(ROOT)
    try:
        files = asyncio.run(_preview(settings, Path(args.out)))
    except RosterError as e:
        print(f"✗ Roster: {e}")
        return 1
    print("Rendered with your roster:\n" + "\n".join(f"  {f}" for f in files))
    return 0


def cmd_run(args) -> int:
    from .bot.client import AniimoBot
    from .logging_setup import setup_logging

    try:
        settings = _load(args)
    except ConfigError as e:
        print(f"✗ {e}")
        return 1
    problems = settings.problems()
    if problems:
        print("The bot can't start yet:\n" + "\n".join(f"  ✗ {p}" for p in problems))
        return 1
    try:
        load_roster(settings.paths.roster_csv, settings.paths.images_dir)
    except RosterError as e:
        print(f"✗ Roster: {e}")
        return 1
    setup_logging(settings.paths.logs_dir)
    bot = AniimoBot(settings)
    bot.run(settings.token, log_handler=None)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aniimo-pvp", description="Aniimo 1v1 draft tournament bot")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--env", default=".env")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run", help="start the bot (default)")
    sub.add_parser("check", help="validate config.yaml, .env and the roster without connecting")
    pv = sub.add_parser("preview", help="render sample board + bracket images with your roster")
    pv.add_argument("--out", default="preview")
    sub.add_parser("init", help="create config.yaml, .env and data/roster.csv from the examples")
    args = parser.parse_args(argv)
    handler = {"check": cmd_check, "preview": cmd_preview, "init": cmd_init}.get(args.cmd or "run", cmd_run)
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
