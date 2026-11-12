"""Fetch the roster, thumbnails and element icons from the official Aniimo wiki (https://wiki.aniimo.com/).

Run on your own PC with ``aniimo-pvp fetch-assets``. Nothing is redistributed with this project: the files are
downloaded straight from the publisher's site for your own tournament use, are re-encoded as plain PNGs, and are
git-ignored.
"""

from __future__ import annotations

import concurrent.futures as cf
import io
import json
import logging
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from .domain.roster import DEFAULT_COLOR, ELEMENT_COLORS, ELEMENTS, slugify

log = logging.getLogger(__name__)

WIKI_URL = "https://wiki.aniimo.com/"
IMAGE_HOST = "worldx-website-cdn.aniimo.com"
FRONT_HOST = "kg-web-cdn.akamaized.net"
ALLOWED_HOSTS = {"wiki.aniimo.com", IMAGE_HOST, FRONT_HOST}
SPRITE_URL = f"https://{FRONT_HOST}/master/worldx/wiki-frontend/images/sprite/attributes.png"
SPRITE_ORDER = ("Grass", "Fire", "Electric", "Rock", "Wind", "Water", "Holy", "Dark", "Ice")  # top to bottom
SPRITE_CELL = 64
USER_AGENT = "aniimo-pvp-local-bot (personal tournament tool)"

THUMB_SIZE = 256
ICON_SIZE = 128
MAX_IMAGE_BYTES = 3_000_000
MAX_PAGE_BYTES = 8_000_000
MAX_PIXELS = 25_000_000

ROLES = {"dps": "DPS", "break": "Break", "sup": "Support", "energy": "Energy", "heal": "Healer"}
_IMAGE_RE = re.compile(
    rf"^https://{re.escape(IMAGE_HOST)}/official-website/worldx/wiki_stage/init/Wiki_Aniimo_(\d{{3,8}})\.png$"
)
_NAME_RE = re.compile(r"^[^\W_][\w .'’:-]{0,39}$")
PLACEHOLDER_MARK = "EXAMPLE ONLY"


class WikiError(RuntimeError):
    pass


class WikiNotFound(WikiError):
    pass


# ---------------------------------------------------------------- safe downloading
def ensure_allowed(url: str) -> None:
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or (u.hostname or "") not in ALLOWED_HOSTS or u.username or u.password:
        raise WikiError(f"Refusing to fetch {url}: only https://{', '.join(sorted(ALLOWED_HOSTS))} are allowed")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        ensure_allowed(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


Fetch = Callable[[str, int], bytes]


def http_get(url: str, max_bytes: int = MAX_IMAGE_BYTES, *, timeout: float = 30, retries: int = 3) -> bytes:
    ensure_allowed(url)
    opener = urllib.request.build_opener(_SafeRedirect)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with opener.open(req, timeout=timeout) as resp:
                data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise WikiError(f"{url} is larger than {max_bytes} bytes")
            return data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise WikiNotFound(url) from e
            if e.code < 500:
                raise WikiError(f"HTTP {e.code} for {url}") from e
            last = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise WikiError(f"Couldn't download {url}: {last}")


# ---------------------------------------------------------------- parsing the wiki page
@dataclass(frozen=True)
class WikiAniimo:
    name: str
    slug: str
    elements: tuple[str, ...]
    role: str
    art_url: str
    head_url: str
    sort_order: int = 0


def _nuxt_payload(html: str) -> list[Any]:
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise WikiError("The wiki page has no data block (its layout may have changed)")
    try:
        data = json.loads(m.group(1))
    except ValueError as e:
        raise WikiError(f"The wiki data block isn't valid JSON: {e}") from e
    if not isinstance(data, list):
        raise WikiError("Unexpected wiki data layout")
    return data


def _resolve(data: list[Any], i: int, depth: int = 0) -> Any:
    """Nuxt payloads are flat arrays; objects and lists hold indexes into that array."""
    if depth > 8 or not 0 <= i < len(data):
        return None
    v = data[i]
    if isinstance(v, dict):
        return {k: (_resolve(data, x, depth + 1) if isinstance(x, int) else x) for k, x in v.items()}
    if isinstance(v, list):
        if len(v) == 2 and v[0] in ("Reactive", "ShallowReactive", "Ref", "ShallowRef") and isinstance(v[1], int):
            return _resolve(data, v[1], depth + 1)
        return [_resolve(data, x, depth + 1) if isinstance(x, int) else x for x in v]
    return v


def parse_roster(html: str) -> tuple[list[WikiAniimo], list[str]]:
    """One entry per Aniimo name (basic form preferred), in the wiki's own order."""
    data = _nuxt_payload(html)
    groups: dict[str, list[dict[str, Any]]] = {}
    for i, v in enumerate(data):
        if isinstance(v, dict) and "entryId" in v and "imageUrl" in v and "name" in v:
            rec = _resolve(data, i)
            if isinstance(rec, dict) and rec.get("type") == "ANIIMO" and isinstance(rec.get("name"), str):
                groups.setdefault(rec["name"], []).append(rec)
    warnings: list[str] = []
    out: list[WikiAniimo] = []
    used: set[str] = set()
    for name, rows in sorted(groups.items(), key=lambda kv: min(int(r.get("sortOrder") or 0) for r in kv[1])):
        pick = next((r for r in rows if r.get("currentMorphology") == "Basic Form"), rows[0])
        url = str(pick.get("imageUrl", ""))
        m = _IMAGE_RE.match(url)
        elements = tuple(str(a).removeprefix("attributes-").title() for a in (pick.get("attributes") or []))
        positions = [str(p).removeprefix("position-") for p in (pick.get("position") or [])]
        if not _NAME_RE.match(name):
            warnings.append(f"skipped {name!r}: unexpected characters in the name")
            continue
        if not m:
            warnings.append(f"skipped {name}: unexpected image address")
            continue
        if not elements or any(e not in ELEMENTS for e in elements):
            warnings.append(f"skipped {name}: unknown element {elements}")
            continue
        slug = slugify(name)
        if not slug:
            warnings.append(f"skipped {name}: can't make an id from the name")
            continue
        base, n = slug, 2
        while slug in used:
            slug, n = f"{base}_{n}", n + 1
        used.add(slug)
        out.append(
            WikiAniimo(
                name,
                slug,
                elements[:2],
                ROLES.get(positions[0], "") if positions else "",
                url,
                url.replace("Wiki_Aniimo_", "Wiki_PetHead_"),
                int(pick.get("sortOrder") or 0),
            )
        )
    if not out:
        raise WikiError("No Aniimo found on the wiki page (its layout may have changed)")
    return out, warnings


# ---------------------------------------------------------------- images
def _open_png(raw: bytes) -> Image.Image:
    try:
        im = Image.open(io.BytesIO(raw))
        if im.format != "PNG":
            raise WikiError(f"expected a PNG image, got {im.format}")
        w, h = im.size
        if w < 16 or h < 16 or w * h > MAX_PIXELS:
            raise WikiError(f"unexpected image size {w}x{h}")
        return im.convert("RGBA")
    except WikiError:
        raise
    except Exception as e:
        raise WikiError(f"not a valid image: {e}") from e


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def process_thumbnail(raw: bytes, *, trim: bool) -> bytes:
    """Validate and re-encode as a square transparent PNG. ``trim`` crops full-body art to the creature first."""
    im = _open_png(raw)
    if trim:
        bbox = im.getchannel("A").getbbox()
        if bbox:
            im = im.crop(bbox)
    im = ImageOps.contain(im, (THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (THUMB_SIZE, THUMB_SIZE), (0, 0, 0, 0))
    canvas.paste(im, ((THUMB_SIZE - im.width) // 2, (THUMB_SIZE - im.height) // 2))
    return _png_bytes(canvas)


def element_icons_from_sprite(raw: bytes) -> dict[str, bytes]:
    """Slice the wiki's white element glyphs and put each on a disc in the wiki's colour for that element."""
    sheet = _open_png(raw)
    expected = (SPRITE_CELL, SPRITE_CELL * len(SPRITE_ORDER))
    if sheet.size != expected:
        raise WikiError(f"element sprite is {sheet.size}, expected {expected} (the wiki may have changed)")
    out: dict[str, bytes] = {}
    ss = 4
    for i, element in enumerate(SPRITE_ORDER):
        glyph = sheet.crop((0, i * SPRITE_CELL, SPRITE_CELL, (i + 1) * SPRITE_CELL))
        big = ICON_SIZE * ss
        disc = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        color = ELEMENT_COLORS.get(element, DEFAULT_COLOR).lstrip("#")
        ImageDraw.Draw(disc).ellipse((0, 0, big - 1, big - 1), fill=tuple(int(color[j : j + 2], 16) for j in (0, 2, 4)))
        disc = disc.resize((ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS)
        g = glyph.resize((round(ICON_SIZE * 0.62),) * 2, Image.Resampling.LANCZOS)
        disc.alpha_composite(g, ((ICON_SIZE - g.width) // 2, (ICON_SIZE - g.height) // 2))
        out[element] = _png_bytes(disc)
    return out


# ---------------------------------------------------------------- roster file
def roster_csv_text(items: list[WikiAniimo]) -> str:
    import csv

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["slug", "name", "element", "role", "image"])
    w.writerow(
        [
            f"# Generated by `aniimo-pvp fetch-assets` from the official wiki ({WIKI_URL}). "
            "Delete a row to take that Aniimo out of the draft pool."
        ]
    )
    w.writerow(["# Dual elements are written like Fire/Dark. The wiki lists every evolution stage as its own Aniimo."])
    for a in items:
        w.writerow([a.slug, a.name, "/".join(a.elements), a.role, f"{a.slug}.png"])
    return buf.getvalue()


def _is_placeholder(path: Path) -> bool:
    try:
        return PLACEHOLDER_MARK in path.read_text(encoding="utf-8")
    except OSError:
        return False


def _same_text(path: Path, text: str) -> bool:
    try:
        return path.read_text(encoding="utf-8") == text
    except OSError:
        return False


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


# ---------------------------------------------------------------- the whole job
@dataclass
class FetchReport:
    aniimo: int = 0
    downloaded: int = 0
    already_had: int = 0
    from_full_art: int = 0
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    element_icons: int = 0
    roster_path: Path | None = None
    roster_note: str = ""


def fetch_assets(
    roster_csv: str | Path,
    images_dir: str | Path,
    elements_dir: str | Path,
    *,
    fetch: Fetch = http_get,
    progress: Callable[[str], None] = print,
    refresh: bool = False,
    overwrite_roster: bool = False,
    workers: int = 3,
) -> FetchReport:
    roster_csv, images_dir, elements_dir = Path(roster_csv), Path(images_dir), Path(elements_dir)
    report = FetchReport()
    progress(f"Reading the roster from {WIKI_URL} ...")
    html = fetch(WIKI_URL, MAX_PAGE_BYTES).decode("utf-8", errors="replace")
    items, report.warnings = parse_roster(html)
    report.aniimo = len(items)
    progress(f"Found {len(items)} Aniimo. Downloading thumbnails (3 at a time, existing files are skipped)...")

    def work(a: WikiAniimo) -> tuple[str, str]:
        dest = images_dir / f"{a.slug}.png"
        if dest.is_file() and dest.stat().st_size > 0 and not refresh:
            return a.slug, "had"
        time.sleep(0.05)
        try:
            return a.slug, _download_thumb(a, dest, fetch)
        except WikiError as e:
            return a.slug, f"failed: {e}"

    images_dir.mkdir(parents=True, exist_ok=True)
    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for i, (slug, status) in enumerate(pool.map(work, items), 1):
            if status == "had":
                report.already_had += 1
            elif status == "head":
                report.downloaded += 1
            elif status == "art":
                report.downloaded += 1
                report.from_full_art += 1
            else:
                report.failed.append(f"{slug} ({status})")
            if i % 20 == 0 or i == len(items):
                progress(f"  {i}/{len(items)}")

    try:
        for element, png in element_icons_from_sprite(fetch(SPRITE_URL, MAX_IMAGE_BYTES)).items():
            _write_atomic(elements_dir / f"{element.lower()}.png", png)
            report.element_icons += 1
    except WikiError as e:
        report.warnings.append(f"element icons not updated ({e}); the bot draws simple badges instead")

    # the roster file: never overwrite hand edits by accident
    csv_text = roster_csv_text(items)
    if not roster_csv.exists() or _is_placeholder(roster_csv):
        _write_atomic(roster_csv, csv_text.encode("utf-8"))
        report.roster_path, report.roster_note = roster_csv, "written"
    elif _same_text(roster_csv, csv_text):
        report.roster_path, report.roster_note = roster_csv, "already up to date"
    elif overwrite_roster:
        backup = roster_csv.with_name(f"{roster_csv.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(roster_csv, backup)
        _write_atomic(roster_csv, csv_text.encode("utf-8"))
        report.roster_path, report.roster_note = roster_csv, f"replaced (old one saved as {backup.name})"
    else:
        alt = roster_csv.with_name("roster.wiki.csv")
        _write_atomic(alt, csv_text.encode("utf-8"))
        report.roster_path = alt
        report.roster_note = f"{roster_csv.name} already exists, so the new list was saved as {alt.name}"
    return report


def _download_thumb(a: WikiAniimo, dest: Path, fetch: Fetch) -> str:
    """Head icon first (a small square), the full art as a fallback."""
    last: WikiError | None = None
    for url, trim, kind in ((a.head_url, False, "head"), (a.art_url, True, "art")):
        try:
            png = process_thumbnail(fetch(url, MAX_IMAGE_BYTES), trim=trim)
        except WikiNotFound as e:
            last = e
            continue
        except WikiError as e:  # unusable file: try the other source
            last = e
            continue
        _write_atomic(dest, png)
        return kind
    raise WikiError(f"no usable image ({last})")
