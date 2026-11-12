"""Aniimo roster loaded from data/roster.csv plus thumbnails from assets/aniimo/."""

from __future__ import annotations

import csv
import difflib
import re
from dataclasses import dataclass
from pathlib import Path

ELEMENTS = ("Fire", "Water", "Grass", "Ice", "Electric", "Rock", "Wind", "Holy", "Dark")
ELEMENT_COLORS = {  # the colours the official wiki uses for each element
    "Fire": "#E26161",
    "Water": "#529DE7",
    "Grass": "#51B17A",
    "Ice": "#45C1D6",
    "Electric": "#E0C21A",
    "Rock": "#BEA77B",
    "Wind": "#2A52BE",
    "Holy": "#F6A93C",
    "Dark": "#A373D2",
}
ELEMENT_EMOJI = {
    "Fire": "🔥",
    "Water": "💧",
    "Grass": "🌿",
    "Ice": "❄️",
    "Electric": "⚡",
    "Rock": "🪨",
    "Wind": "🌪️",
    "Holy": "✨",
    "Dark": "🌑",
}
DEFAULT_COLOR = "#9AA1C0"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class RosterError(ValueError):
    pass


def slugify(name: str) -> str:
    return _SLUG_RE.sub("_", name.strip().lower()).strip("_")


@dataclass(frozen=True)
class Aniimo:
    slug: str
    name: str
    elements: tuple[str, ...]  # one or two, e.g. ("Fire", "Dark")
    role: str
    image_path: Path | None

    @property
    def element(self) -> str:
        """The primary (first) element."""
        return self.elements[0]

    @property
    def element_label(self) -> str:
        return " / ".join(self.elements)

    @property
    def color(self) -> str:
        return ELEMENT_COLORS.get(self.element, DEFAULT_COLOR)

    @property
    def initials(self) -> str:
        return self.name[:2].upper()


class Roster:
    def __init__(self, aniimo: list[Aniimo]):
        self.items = aniimo
        self.by_slug = {a.slug: a for a in aniimo}

    def __len__(self) -> int:
        return len(self.items)

    def __contains__(self, slug: object) -> bool:
        return slug in self.by_slug

    @property
    def slugs(self) -> list[str]:
        return [a.slug for a in self.items]

    def get(self, slug: str | None) -> Aniimo | None:
        return self.by_slug.get(slug) if slug else None

    def name(self, slug: str | None) -> str:
        a = self.get(slug)
        return a.name if a else "—"

    def elements(self) -> list[str]:
        present = {e for a in self.items for e in a.elements}
        known = [e for e in ELEMENTS if e in present]
        return known + sorted(present - set(ELEMENTS))

    def in_element(self, element: str) -> list[Aniimo]:
        """Everything with this element, including dual-element Aniimo."""
        return [a for a in self.items if element in a.elements]

    def search(self, query: str, limit: int = 25) -> list[Aniimo]:
        q = query.strip().lower()
        if not q:
            return []
        starts = [a for a in self.items if a.name.lower().startswith(q)]
        contains = [a for a in self.items if q in a.name.lower() and a not in starts]
        found = starts + contains
        if len(found) < limit:
            names = {a.name.lower(): a for a in self.items if a not in found}
            for n in difflib.get_close_matches(q, list(names), n=limit - len(found), cutoff=0.6):
                found.append(names[n])
        return found[:limit]


_SPLIT_RE = re.compile(r"\s*[/,+&|]\s*")


def _norm_element(value: str) -> str:
    v = value.strip()
    for e in ELEMENTS:
        if e.lower() == v.lower():
            return e
    return v.title()


def parse_elements(cell: str) -> tuple[str, ...]:
    """'Fire/Dark' -> ('Fire', 'Dark'). Duplicates are dropped; at most two elements are kept."""
    out: list[str] = []
    for part in _SPLIT_RE.split(cell.strip()):
        el = _norm_element(part) if part else ""
        if el and el not in out:
            out.append(el)
    return tuple(out[:2])


def load_roster(csv_path: str | Path, images_dir: str | Path) -> tuple[Roster, list[str]]:
    """Load the roster. Fatal problems raise RosterError; soft problems are returned as warnings."""
    csv_path, images_dir = Path(csv_path), Path(images_dir)
    if not csv_path.exists():
        raise RosterError(f"Roster file not found: {csv_path}")
    warnings: list[str] = []
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise RosterError(f"{csv_path} is empty")
        fields = {f.strip().lower() for f in reader.fieldnames if f}
        if not {"name", "element"} <= fields:
            raise RosterError(f"{csv_path} needs at least the columns: name, element (optional: slug, role, image)")
        items: list[Aniimo] = []
        seen: dict[str, int] = {}
        for line_no, raw in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items() if k is not None}
            first = next(iter(row.values()), "")
            if not any(row.values()) or first.startswith("#") or row.get("name", "").startswith("#"):
                continue  # blank line or comment
            name = row.get("name", "")
            if not name:
                raise RosterError(f"{csv_path}:{line_no}: name is empty")
            slug = slugify(row.get("slug") or name)
            if not slug:
                raise RosterError(f"{csv_path}:{line_no}: can't build a slug from {name!r}")
            if slug in seen:
                raise RosterError(f"{csv_path}:{line_no}: duplicate slug {slug!r} (first on line {seen[slug]})")
            seen[slug] = line_no
            elements = parse_elements(row.get("element", ""))
            if not elements:
                raise RosterError(f"{csv_path}:{line_no}: element is empty for {name}")
            for el in elements:
                if el not in ELEMENTS:
                    warnings.append(f"line {line_no}: unknown element {el!r} for {name} (shown in grey)")
            image_name = Path(row.get("image") or f"{slug}.png").name  # a plain file name inside images_dir
            image_path = images_dir / image_name
            if not image_path.is_file():
                if row.get("image"):
                    warnings.append(f"line {line_no}: image {image_name} not found, using a placeholder")
                image_path = None
            items.append(Aniimo(slug, name, elements, row.get("role", ""), image_path))
    if not items:
        raise RosterError(f"{csv_path} has no Aniimo rows")
    return Roster(items), warnings
