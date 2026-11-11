from aniimo_pvp.bot.client import AniimoBot
from aniimo_pvp.bot.cogs.admin import AdminCog, parse_id
from aniimo_pvp.bot.cogs.roster import RosterCog
from aniimo_pvp.bot.cogs.stats import StatsCog
from aniimo_pvp.bot.cogs.tournament import TournamentCog
from aniimo_pvp.config import Channels, Settings


def walk(payload, path=""):
    yield path or payload["name"], payload
    for opt in payload.get("options", []):
        if opt.get("type") in (1, 2):  # subcommand / group
            yield from walk(opt, f"{path or payload['name']} {opt['name']}")


async def test_all_slash_commands_build_valid_payloads():
    bot = AniimoBot(Settings(token="x", guild_id=1, admin_role_id=2, channels=Channels(admin=3, announce=4, matches=5)))
    for cog in (TournamentCog(bot), AdminCog(bot), StatsCog(bot), RosterCog(bot)):
        await bot.add_cog(cog)
    payloads = [c.to_dict(bot.tree) for c in bot.tree.get_commands()]
    names = {p["name"] for p in payloads}
    assert {"tournament", "admin", "stats", "roster", "leaderboard", "profile"} <= names
    seen = set()
    for top in payloads:
        for path, p in walk(top):
            seen.add(path)
            assert 1 <= len(p["description"]) <= 100, path
            assert p["name"] == p["name"].lower() and len(p["name"]) <= 32, path
            for opt in p.get("options", []):
                if opt.get("type") not in (1, 2):
                    assert 1 <= len(opt["description"]) <= 100, f"{path} {opt['name']}"
    for expected in (
        "tournament create",
        "tournament players",
        "tournament start",
        "tournament status",
        "tournament bracket",
        "tournament cancel",
        "admin undo",
        "admin pause",
        "admin resume",
        "admin set-result",
        "admin award",
        "admin replay",
        "admin extend",
        "admin dq",
        "admin swap",
        "stats aniimo",
        "stats player",
        "roster reload",
        "roster info",
    ):
        assert expected in seen, expected
    await bot.close()


def test_parse_id():
    assert parse_id("<@!412806339218145281>") == 412806339218145281
    assert parse_id("11") == 11
