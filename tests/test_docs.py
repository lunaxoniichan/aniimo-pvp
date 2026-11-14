"""The docs are part of the product: their commands, options, config and links must match the code."""

import re
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel

from aniimo_pvp.__main__ import build_parser
from aniimo_pvp.bot.client import AniimoBot
from aniimo_pvp.bot.cogs.admin import AdminCog
from aniimo_pvp.bot.cogs.friendly import FriendlyCog
from aniimo_pvp.bot.cogs.roster import RosterCog
from aniimo_pvp.bot.cogs.stats import StatsCog
from aniimo_pvp.bot.cogs.tournament import TournamentCog
from aniimo_pvp.config import Channels, Settings
from aniimo_pvp.domain.players_csv import parse_players_csv

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
FOLLOW = [ROOT / "README.md", ROOT / "docs" / "TESTING.md"]  # the docs a person follows step by step
FENCE = re.compile(r"^(\s*)```(\S*)\s*$")


def fenced_blocks(text: str) -> list[tuple[str, str]]:
    """(language, content) of every fenced block."""
    blocks, lang, buf = [], None, []
    for line in text.splitlines():
        m = FENCE.match(line)
        if m and lang is None:
            lang, buf = m.group(2), []
        elif m:
            blocks.append((lang, "\n".join(buf)))
            lang = None
        elif lang is not None:
            buf.append(line.strip() if lang == "" else line)
    assert lang is None, "a code block is never closed"
    return blocks


def code_text(text: str) -> str:
    """All code in a doc: fenced blocks plus inline `spans`."""
    without_blocks = re.sub(r"^\s*```.*?^\s*```", "", text, flags=re.S | re.M)
    spans = re.findall(r"`([^`\n]+)`", without_blocks)
    return "\n".join([c for _, c in fenced_blocks(text)] + spans)


def slug(heading: str) -> str:
    s = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return s.replace(" ", "-")


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.name)
def test_code_blocks_are_balanced_and_links_resolve(path):
    text = path.read_text(encoding="utf-8")
    fenced_blocks(text)  # raises if a fence is left open
    body = re.sub(r"^\s*```.*?^\s*```", "", text, flags=re.S | re.M)
    anchors = {slug(h) for h in re.findall(r"^#{1,6}\s+(.*?)\s*$", body, flags=re.M)}
    for target in re.findall(r"\]\(([^)\s]+)\)", body):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        if target.startswith("#"):
            assert target[1:] in anchors, f"{path.name}: no heading for link {target}"
        else:
            file = (path.parent / target.split("#")[0]).resolve()
            assert file.exists(), f"{path.name}: broken link {target}"


@pytest.fixture
async def command_paths():
    bot = AniimoBot(Settings(token="x", guild_id=1, admin_role_id=2, channels=Channels(admin=3, announce=4, matches=5)))
    for cog in (TournamentCog(bot), AdminCog(bot), StatsCog(bot), RosterCog(bot), FriendlyCog(bot)):
        await bot.add_cog(cog)

    def walk(p, path):
        yield path
        for o in p.get("options", []):
            if o.get("type") in (1, 2):
                yield from walk(o, f"{path} {o['name']}")

    paths, options = set(), {}
    for c in bot.tree.get_commands():
        d = c.to_dict(bot.tree)
        for path in walk(d, d["name"]):
            paths.add(path)
    for c in bot.tree.walk_commands():
        if hasattr(c, "parameters"):  # commands, not groups
            options[c.qualified_name] = {p.name for p in c.parameters}
    await bot.close()
    return paths, options


@pytest.mark.parametrize("path", FOLLOW, ids=lambda p: p.name)
async def test_slash_commands_and_options_in_the_docs_exist(path, command_paths):
    paths, options = command_paths
    text = code_text(path.read_text(encoding="utf-8"))
    found = re.findall(r"(?<![\w/.])/(tournament|admin|stats|roster)\s+([a-z][a-z-]+)", text)
    found += [(m, "") for m in re.findall(r"(?<![\w/.])/(leaderboard|profile|duel-test|duel|register)\b(?!-)", text)]
    assert found, "expected slash commands in the doc"
    for group, sub in found:
        name = f"{group} {sub}".strip()
        assert name in paths, f"{path.name}: /{name} is not a command"
    known = set().union(*options.values())
    for opt in re.findall(r"[Oo]ptions?\s+`(\w+)`", path.read_text(encoding="utf-8")):  # "Option `name`: ..."
        assert opt in known, f"{path.name}: no command has an option called {opt}"


@pytest.mark.parametrize("path", FOLLOW, ids=lambda p: p.name)
def test_terminal_commands_in_the_docs_are_valid(path):
    text = code_text(path.read_text(encoding="utf-8"))
    seen = 0
    for line in text.splitlines():
        m = re.match(r"^\s*(?:\.\\run\.bat|\./run\.sh|aniimo-pvp|python -m aniimo_pvp)((?:\s+[\w-]+)*)\s*$", line)
        if m is None:
            continue
        build_parser().parse_args(m.group(1).split())  # argparse exits on an unknown command or option
        seen += 1
    assert seen, "expected run commands in the doc"


def test_testing_guide_names_every_run_command_it_relies_on():
    text = (ROOT / "docs" / "TESTING.md").read_text(encoding="utf-8")
    for cmd in ("check", "preview", "fetch-assets"):
        assert re.search(rf"\.\\run\.bat {cmd}\b", text) and re.search(rf"\./run\.sh {cmd}\b", text), cmd


def check_keys(model: type[BaseModel], data: dict, where: str = ""):
    for key, value in data.items():
        assert key in model.model_fields, f"{where}{key} is not a setting in config.yaml"
        ann = model.model_fields[key].annotation
        if isinstance(value, dict) and isinstance(ann, type) and issubclass(ann, BaseModel):
            check_keys(ann, value, f"{key}.")


@pytest.mark.parametrize("path", FOLLOW, ids=lambda p: p.name)
def test_yaml_examples_are_valid_settings(path):
    blocks = [c for lang, c in fenced_blocks(path.read_text(encoding="utf-8")) if lang == "yaml"]
    assert blocks
    for block in blocks:
        data = yaml.safe_load(block)
        check_keys(Settings, data)
        Settings(**data)


@pytest.mark.parametrize("path", FOLLOW, ids=lambda p: p.name)
def test_csv_examples_parse(path):
    blocks = [c for lang, c in fenced_blocks(path.read_text(encoding="utf-8")) if lang == "csv"]
    assert blocks
    for block in blocks:
        if re.search(r"YOUR_ID|OTHER_ID", block):
            continue  # placeholders the reader replaces
        rows, issues = parse_players_csv(block, strict_ids=False)
        assert rows and not issues, (block, issues)


def test_invite_link_matches_the_permissions_the_bot_needs():
    import discord

    perms = discord.Permissions(
        view_channel=True, send_messages=True, embed_links=True, attach_files=True, read_message_history=True,
        mention_everyone=True, use_external_emojis=True, add_reactions=True, manage_threads=True,
        create_public_threads=True, create_private_threads=True, send_messages_in_threads=True,
    )  # fmt: skip
    for path in FOLLOW:
        text = path.read_text(encoding="utf-8")
        assert f"permissions={perms.value}" in text, path.name
