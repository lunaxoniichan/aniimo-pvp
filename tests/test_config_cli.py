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
    assert main(["preview", "--out", str(tmp_path / "pv")]) == 0
    assert {p.name for p in (tmp_path / "pv").iterdir()} == {"board_mid_draft.png", "board_complete.png", "bracket.png"}
