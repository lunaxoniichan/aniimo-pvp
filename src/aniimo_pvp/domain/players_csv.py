"""Parse the admin's player list: discord_id, aniimo_uid[, display_name]."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*$")
_UID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
HEADER_ALIASES = {
    "discord_id": "discord_id", "discord": "discord_id", "id": "discord_id", "user_id": "discord_id",
    "aniimo_uid": "aniimo_uid", "uid": "aniimo_uid", "aniimo_id": "aniimo_uid", "aniimo": "aniimo_uid",
    "display_name": "display_name", "name": "display_name", "nickname": "display_name",
}


@dataclass(frozen=True)
class PlayerRow:
    line: int
    discord_id: int
    aniimo_uid: str
    display_name: str | None


@dataclass(frozen=True)
class CsvIssue:
    line: int
    message: str
    discord_id: int | None = None


def looks_like_players_csv(text: str) -> bool:
    lines = [ln for ln in (x.strip() for x in text.splitlines()) if ln and not _FENCE.match(ln)]
    if not lines:
        return False
    data = [ln for ln in lines if re.match(r"^<?@?!?\d{5,}", ln)]
    return len(data) >= 1 and all("," in ln or "\t" in ln or ";" in ln for ln in data)


def parse_players_csv(text: str, *, strict_ids: bool = True) -> tuple[list[PlayerRow], list[CsvIssue]]:
    lines = [ln for ln in text.splitlines() if not _FENCE.match(ln)]
    body = "\n".join(lines).strip("\n")
    delimiter = ","
    if "\t" in body and "," not in body:
        delimiter = "\t"
    elif ";" in body and "," not in body:
        delimiter = ";"
    reader = csv.reader(io.StringIO(body), delimiter=delimiter)
    rows: list[PlayerRow] = []
    issues: list[CsvIssue] = []
    columns = {"discord_id": 0, "aniimo_uid": 1, "display_name": 2}
    seen_ids: dict[int, int] = {}
    seen_uids: dict[str, int] = {}
    first = True
    for line_no, cells in enumerate(reader, start=1):
        cells = [c.strip() for c in cells]
        if not any(cells):
            continue
        if first:
            first = False
            head = [HEADER_ALIASES.get(c.lower().replace(" ", "_")) for c in cells]
            if not cells[0].lstrip("<@!").rstrip(">").isdigit() and any(head):
                columns = {name: i for i, name in enumerate(head) if name}
                if "discord_id" not in columns or "aniimo_uid" not in columns:
                    issues.append(CsvIssue(line_no, "Header must include discord_id and aniimo_uid"))
                    return [], issues
                continue

        def cell(name: str, cells: list[str] = cells, columns: dict[str, int] = columns) -> str:
            idx = columns.get(name)
            return cells[idx] if idx is not None and idx < len(cells) else ""

        raw_id = cell("discord_id").strip()
        raw_id = raw_id.removeprefix("<@").removeprefix("!").removesuffix(">")
        uid = cell("aniimo_uid")
        name = cell("display_name") or None
        if not raw_id.isdigit():
            issues.append(CsvIssue(line_no, f"discord_id {cell('discord_id')!r} is not a number"))
            continue
        did = int(raw_id)
        if strict_ids and not 17 <= len(raw_id) <= 20:
            issues.append(CsvIssue(line_no, f"discord_id {raw_id} doesn't look like a Discord ID (17-20 digits)", did))
            continue
        if not uid:
            issues.append(CsvIssue(line_no, "aniimo_uid is empty", did))
            continue
        if not _UID_RE.match(uid):
            issues.append(CsvIssue(line_no, f"aniimo_uid {uid!r} has unexpected characters", did))
            continue
        if did in seen_ids:
            issues.append(CsvIssue(line_no, f"duplicate discord_id (also on line {seen_ids[did]})", did))
            continue
        if uid in seen_uids:
            issues.append(CsvIssue(line_no, f"duplicate aniimo_uid {uid} (also on line {seen_uids[uid]})", did))
            continue
        if name and len(name) > 32:
            name = name[:32]
        seen_ids[did] = line_no
        seen_uids[uid] = line_no
        rows.append(PlayerRow(line_no, did, uid, name))
    return rows, issues
