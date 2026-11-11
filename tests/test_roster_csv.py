from pathlib import Path

import pytest

from aniimo_pvp.domain.players_csv import looks_like_players_csv, parse_players_csv
from aniimo_pvp.domain.roster import RosterError, load_roster, slugify


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "roster.csv"
    p.write_text(text, encoding="utf-8")
    return p


def test_roster_valid_with_placeholders(tmp_path):
    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "emberlynx.png").write_bytes(b"x")
    p = write(tmp_path, "slug,name,element,role,image\n"
                        "emberlynx,Emberlynx,fire,Attacker,\n"
                        ",Frost Paw,Ice,,missing.png\n")
    roster, warnings = load_roster(p, tmp_path / "img")
    assert len(roster) == 2
    assert roster.get("emberlynx").element == "Fire" and roster.get("emberlynx").image_path is not None
    assert roster.get("frost_paw").image_path is None
    assert any("missing.png" in w for w in warnings)
    assert roster.elements() == ["Fire", "Ice"]


def test_roster_errors(tmp_path):
    with pytest.raises(RosterError):
        load_roster(write(tmp_path, "name,element\nA,Fire\nA,Water\n"), tmp_path)  # duplicate slug
    with pytest.raises(RosterError):
        load_roster(write(tmp_path, "name\nA\n"), tmp_path)  # missing column
    with pytest.raises(RosterError):
        load_roster(write(tmp_path, "name,element\n"), tmp_path)  # no rows
    with pytest.raises(RosterError):
        load_roster(tmp_path / "nope.csv", tmp_path)


def test_roster_unknown_element_warns(tmp_path):
    roster, warnings = load_roster(write(tmp_path, "name,element\nZed,Cosmic\n"), tmp_path)
    assert roster.get("zed").color == "#9AA1C0" and warnings


def test_roster_search(tmp_path):
    csv_text = "name,element\nEmberlynx,Fire\nEmbermoth,Fire\nNemblossom,Grass\nTidefin,Water\n"
    roster, _ = load_roster(write(tmp_path, csv_text), tmp_path)
    names = [a.name for a in roster.search("emb")]
    assert names[:2] == ["Emberlynx", "Embermoth"] and "Nemblossom" in names
    assert [a.name for a in roster.search("tidefn")] == ["Tidefin"]  # fuzzy
    assert slugify(" Frost Paw! ") == "frost_paw"


VALID = """discord_id,aniimo_uid,display_name
412806339218145281,100234567,Luna
523917440329256392,100345678,Kai
"""


def test_players_csv_with_header():
    rows, issues = parse_players_csv(VALID)
    assert not issues and [r.display_name for r in rows] == ["Luna", "Kai"]


def test_players_csv_no_header_spaces_fences_blank_lines():
    text = "```csv\n 412806339218145281 , 100234567 \n\n523917440329256392,100345678\n```"
    rows, issues = parse_players_csv(text)
    assert not issues and rows[1].display_name is None and rows[0].aniimo_uid == "100234567"
    assert looks_like_players_csv(text)
    assert not looks_like_players_csv("hello everyone, ready?")


def test_players_csv_issues():
    text = (
        "discord_id,aniimo_uid\n"
        "412806339218145281,100234567\n"
        "412806339218145281,100999999\n"  # dup id
        "523917440329256392,100234567\n"  # dup uid
        "abc,1\n"  # not numeric
        "123,5\n"  # too short
        "634028551430367403,\n"  # empty uid
        "<@745139662541478514>,100567890,Rex\n"  # mention form is accepted
    )
    rows, issues = parse_players_csv(text)
    assert [r.discord_id for r in rows] == [412806339218145281, 745139662541478514]
    assert len(issues) == 5
    assert any("duplicate discord_id" in i.message for i in issues)
    rows, issues = parse_players_csv("123,5\n", strict_ids=False)  # dev mode allows short fake IDs
    assert rows and not issues


def test_players_csv_column_order_from_header():
    rows, issues = parse_players_csv("name,uid,discord\nLuna,100234567,412806339218145281\n")
    assert not issues and rows[0].discord_id == 412806339218145281 and rows[0].display_name == "Luna"
