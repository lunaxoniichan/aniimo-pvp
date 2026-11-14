import pytest

from aniimo_pvp.__main__ import main
from aniimo_pvp.config import ConfigError, load_settings


def test_load_settings_and_problems(tmp_path, monkeypatch):
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "guild_id: 1\nadmin_role_id: 2\nchannels: {admin: 3, announce: 4, matches: 5}\n"
        "timers: {draft_step_seconds: 30}\npaths: {database: data/x.db}\n"
    )
    (tmp_path / ".env").write_text("DISCORD_TOKEN=abc\n")
    s = load_settings(cfg, tmp_path / ".env")
    assert s.token == "abc" and s.timers.draft_step_seconds == 30 and not s.problems()
    assert s.paths.database.endswith("data/x.db") and str(tmp_path) in s.paths.database


def test_missing_and_invalid_config(tmp_path, monkeypatch):
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "nope.yaml", tmp_path / ".env")
    bad = tmp_path / "bad.yaml"
    bad.write_text("guild_id: [1\n")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_settings(bad, tmp_path / ".env")
    bad.write_text("guild_id: abc\n")
    with pytest.raises(ConfigError, match="guild_id"):
        load_settings(bad, tmp_path / ".env")
    bad.write_text("draft_order: [[A, ban], [A, pick], [B, pick]]\n")
    with pytest.raises(ConfigError, match="draft_order"):
        load_settings(bad, tmp_path / ".env")
    empty = tmp_path / "empty.yaml"
    empty.write_text("{}\n")
    s = load_settings(empty, tmp_path / ".env")
    assert len(s.problems()) >= 5


def test_cli_check_and_preview(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "roster.csv").write_text(
        "name,element\n" + "\n".join(f"Mon{i:02d},{['Fire', 'Water', 'Grass'][i % 3]}" for i in range(20)) + "\n"
    )
    (tmp_path / "config.yaml").write_text(
        "guild_id: 1\nadmin_role_id: 2\nchannels: {admin: 3, announce: 4, matches: 5}\n"
    )
    assert main(["check"]) == 1  # token missing
    assert "DISCORD_TOKEN is missing" in capsys.readouterr().out
    (tmp_path / ".env").write_text("DISCORD_TOKEN=abc\n")
    monkeypatch.setenv("DISCORD_TOKEN", "abc")
    assert main(["check"]) == 0
    assert "Friendly duels: off (channels.friendly is 0)" in capsys.readouterr().out
    (tmp_path / "config.yaml").write_text(
        "guild_id: 1\nadmin_role_id: 2\nchannels: {admin: 3, announce: 4, matches: 5, friendly: 6}\n"
    )
    assert main(["check"]) == 0
    assert "Friendly duels: on (/duel in channel 6)" in capsys.readouterr().out
    assert main(["preview", "--out", str(tmp_path / "pv")]) == 0
    assert {p.name for p in (tmp_path / "pv").iterdir()} == {"board_mid_draft.png", "board_complete.png", "bracket.png"}


def test_cli_fetch_assets_reports_and_exit_codes(tmp_path, monkeypatch, capsys):
    from aniimo_pvp import wiki

    monkeypatch.chdir(tmp_path)
    seen = {}

    def fake_ok(roster_csv, images_dir, elements_dir, **kw):
        seen.update(kw, roster=str(roster_csv), images=str(images_dir), elements=str(elements_dir))
        return wiki.FetchReport(
            aniimo=120,
            downloaded=118,
            already_had=2,
            from_full_art=3,
            element_icons=9,
            roster_path=tmp_path / "data" / "roster.csv",
            roster_note="written",
        )

    monkeypatch.setattr(wiki, "fetch_assets", fake_ok)
    assert main(["fetch-assets", "--refresh"]) == 0
    out = capsys.readouterr().out
    assert (
        "120 Aniimo: 118 thumbnails downloaded, 2 already had (3 from full art)" in out and "Element icons: 9/9" in out
    )
    assert seen["refresh"] is True and seen["overwrite_roster"] is False
    assert seen["images"].endswith("assets/aniimo") and seen["elements"].endswith("assets/elements")

    def fake_fail(*a, **kw):
        raise wiki.WikiError("no network")

    monkeypatch.setattr(wiki, "fetch_assets", fake_fail)
    assert main(["fetch-assets"]) == 1
    assert "no network" in capsys.readouterr().out

    def fake_partial(*a, **kw):
        return wiki.FetchReport(aniimo=2, downloaded=1, failed=["zed (no usable image)"])

    monkeypatch.setattr(wiki, "fetch_assets", fake_partial)
    assert main(["fetch-assets"]) == 1  # some thumbnails failed: non-zero so scripts notice
    assert "zed (no usable image)" in capsys.readouterr().out


def test_cli_init_downloads_assets_but_survives_a_failure(tmp_path, monkeypatch, capsys):
    from aniimo_pvp import wiki

    monkeypatch.chdir(tmp_path)
    for name in ("config.example.yaml", ".env.example"):
        (tmp_path / name).write_text("x: 1\n")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "roster.example.csv").write_text("name,element\nA,Fire\n")
    calls = []

    def fake_fail(*a, **kw):
        calls.append(1)
        raise wiki.WikiError("offline")

    monkeypatch.setattr(wiki, "fetch_assets", fake_fail)
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert calls and "Run `aniimo-pvp fetch-assets` later" in out
    assert (tmp_path / "config.yaml").exists() and (tmp_path / "data" / "roster.csv").exists()
    calls.clear()
    assert main(["init", "--no-assets"]) == 0
    assert not calls


@pytest.mark.parametrize(
    ("error", "expected"),
    [("intent", "Message Content Intent is off"), ("token", "Discord rejected the bot token")],
)
def test_run_explains_portal_mistakes_instead_of_a_traceback(tmp_path, monkeypatch, capsys, error, expected):
    import discord

    from aniimo_pvp.bot.client import AniimoBot

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "roster.csv").write_text("name,element\nMon,Fire\n")
    (tmp_path / "config.yaml").write_text(
        "guild_id: 1\nadmin_role_id: 2\nchannels: {admin: 3, announce: 4, matches: 5}\n"
    )
    monkeypatch.setenv("DISCORD_TOKEN", "abc")

    def refuse(self, token, **kw):
        raise discord.PrivilegedIntentsRequired(None) if error == "intent" else discord.LoginFailure("bad")

    monkeypatch.setattr(AniimoBot, "run", refuse)
    assert main(["run"]) == 1
    out = capsys.readouterr().out
    assert expected in out and "Traceback" not in out
