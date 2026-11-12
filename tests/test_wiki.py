import io
import json

import pytest
from PIL import Image

from aniimo_pvp import wiki
from aniimo_pvp.domain.roster import ELEMENT_COLORS, load_roster
from aniimo_pvp.render.elements import ElementIcons
from aniimo_pvp.render.fonts import Fonts
from aniimo_pvp.render.thumbs import ThumbCache

C = "https://worldx-website-cdn.aniimo.com/official-website/worldx/wiki_stage/init"


def devalue(obj):
    """Encode like Nuxt's payload: a flat array where objects and lists hold indexes."""
    arr = []

    def add(v):
        idx = len(arr)
        arr.append(None)
        if isinstance(v, dict):
            arr[idx] = {k: add(x) for k, x in v.items()}
        elif isinstance(v, list):
            arr[idx] = [add(x) for x in v]
        else:
            arr[idx] = v
        return idx

    add(obj)
    return arr


def rec(name, art_id, elements, role="dps", stage="1", form="Basic Form", sort=1, url=None, eid="001"):
    return {
        "type": "ANIIMO", "entryId": eid, "sortOrder": sort, "name": name,
        "imageUrl": url or f"{C}/Wiki_Aniimo_{art_id}.png", "countryId": 0, "description": "x",
        "currentMorphology": form, "currentStage": stage, "position": [f"position-{role}"],
        "attributes": [f"attributes-{e}" for e in elements],
    }  # fmt: skip


def page(records):
    data = json.dumps(
        devalue({"data": {"list": records}, "again": records})
    )  # records appear twice, like the real page
    return (
        f'<html><body><script type="application/json" id="__NUXT_DATA__" data-ssr="true">{data}</script></body></html>'
    )


def png(size=(64, 64), color=(200, 40, 160, 255), mode="RGBA"):
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, "PNG")
    return buf.getvalue()


def creature(size, box):
    """A transparent image with an opaque 'creature' rectangle."""
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    im.paste((200, 40, 160, 255), box)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


RECORDS = [
    rec("Emberpup", "10051", ["fire"], sort=1005100),
    rec("Inferlupa", "10055", ["fire", "dark"], role="break", stage="3", sort=1005500),
    rec("Inferlupa", "1005503", ["fire", "dark"], role="break", stage="3", form="Prismana Form", sort=1005503),
    rec("Irisalis", "10900", ["holy"], role="heal", form="Prismana Form", sort=1090000),  # no basic form
    rec("Evil", "10999", ["fire"], url="https://evil.example/Wiki_Aniimo_10999.png", sort=1099900),
    rec("Weird", "10998", ["cosmic"], sort=1099800),
    rec("Bad;Name\nX", "10997", ["fire"], sort=1099700),
]


def test_parse_roster_groups_names_and_skips_bad_entries():
    items, warnings = wiki.parse_roster(page(RECORDS))
    assert [a.name for a in items] == ["Emberpup", "Inferlupa", "Irisalis"]  # in the wiki's order, each name once
    inf = items[1]
    assert inf.elements == ("Fire", "Dark") and inf.role == "Break" and inf.slug == "inferlupa"
    assert inf.art_url.endswith("Wiki_Aniimo_10055.png") and inf.head_url.endswith("Wiki_PetHead_10055.png")
    assert items[2].art_url.endswith("Wiki_Aniimo_10900.png") and items[2].role == "Healer"  # prismana-only kept
    assert len(warnings) == 3 and any("image address" in w for w in warnings)
    assert any("cosmic" in w.lower() for w in warnings)


def test_parse_roster_errors():
    with pytest.raises(wiki.WikiError, match="data block"):
        wiki.parse_roster("<html></html>")
    with pytest.raises(wiki.WikiError, match="No Aniimo"):
        wiki.parse_roster(page([]))
    with pytest.raises(wiki.WikiError, match="valid JSON"):
        wiki.parse_roster('<script id="__NUXT_DATA__">{oops</script>')


@pytest.mark.parametrize("url", [
    "http://wiki.aniimo.com/", "https://evil.example/x.png", "https://wiki.aniimo.com.evil.example/",
    "https://user:pw@wiki.aniimo.com/", "ftp://wiki.aniimo.com/", "file:///etc/passwd",
])  # fmt: skip
def test_only_official_https_hosts_are_allowed(url):
    with pytest.raises(wiki.WikiError, match="Refusing"):
        wiki.ensure_allowed(url)
    with pytest.raises(wiki.WikiError, match="Refusing"):
        wiki.http_get(url)


def test_official_hosts_pass():
    for u in (wiki.WIKI_URL, f"{C}/Wiki_Aniimo_10051.png", wiki.SPRITE_URL):
        wiki.ensure_allowed(u)


def test_process_thumbnail_squares_trims_and_validates():
    tall = creature((512, 840), (150, 100, 350, 700))  # a tall creature in the middle
    out = Image.open(io.BytesIO(wiki.process_thumbnail(tall, trim=True)))
    assert out.size == (256, 256) and out.mode == "RGBA"
    bbox = out.getchannel("A").getbbox()
    assert bbox[1] <= 2 and bbox[3] >= 254  # trimmed art fills the height
    head = Image.open(io.BytesIO(wiki.process_thumbnail(png((256, 256)), trim=False)))
    assert head.size == (256, 256)
    with pytest.raises(wiki.WikiError, match="not a valid image"):
        wiki.process_thumbnail(b"<html>not an image</html>", trim=False)
    with pytest.raises(wiki.WikiError, match="unexpected image size"):
        wiki.process_thumbnail(png((5000, 6000), (0,), "L"), trim=False)  # 30M pixels
    jpg = io.BytesIO()
    Image.new("RGB", (64, 64)).save(jpg, "JPEG")
    with pytest.raises(wiki.WikiError, match="expected a PNG"):
        wiki.process_thumbnail(jpg.getvalue(), trim=False)


def make_sprite():
    sheet = Image.new("RGBA", (64, 64 * 9), (0, 0, 0, 0))
    for i in range(9):
        sheet.paste((255, 255, 255, 255), (16, i * 64 + 16, 48, i * 64 + 48))
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    return buf.getvalue()


def test_element_icons_from_sprite_use_the_wiki_colours():
    icons = wiki.element_icons_from_sprite(make_sprite())
    assert set(icons) == set(wiki.SPRITE_ORDER) and len(icons) == 9
    fire = Image.open(io.BytesIO(icons["Fire"])).convert("RGBA")
    assert fire.size == (128, 128) and fire.getpixel((64, 64)) == (255, 255, 255, 255)  # white glyph in the middle
    r, g, b, a = fire.getpixel((14, 64))
    assert (r, g, b) == tuple(int(ELEMENT_COLORS["Fire"][i : i + 2], 16) for i in (1, 3, 5)) and a == 255
    assert fire.getpixel((0, 0))[3] == 0  # round badge, transparent corners
    with pytest.raises(wiki.WikiError, match="element sprite"):
        wiki.element_icons_from_sprite(png((64, 64)))


class FakeNet:
    def __init__(self, records, missing_heads=(), bad_heads=()):
        self.html = page(records).encode()
        self.calls = []
        self.missing_heads, self.bad_heads = set(missing_heads), set(bad_heads)

    def __call__(self, url, max_bytes):
        self.calls.append(url)
        wiki.ensure_allowed(url)
        if url == wiki.WIKI_URL:
            return self.html
        if url == wiki.SPRITE_URL:
            return make_sprite()
        stem = url.rsplit("/", 1)[1]
        if "PetHead" in stem:
            if stem in self.missing_heads:
                raise wiki.WikiNotFound(url)
            if stem in self.bad_heads:
                return b"garbage"
            return png((256, 256))
        return creature((512, 840), (100, 100, 400, 800))


GOOD = [r for r in RECORDS if r["name"] in ("Emberpup", "Inferlupa", "Irisalis")]


def dirs(tmp_path):
    return tmp_path / "data" / "roster.csv", tmp_path / "assets" / "aniimo", tmp_path / "assets" / "elements"


def test_fetch_assets_end_to_end_and_rerun(tmp_path):
    csv_path, images, elements = dirs(tmp_path)
    net = FakeNet(GOOD, missing_heads={"Wiki_PetHead_10900.png"}, bad_heads={"Wiki_PetHead_10055.png"})
    log = []
    rep = wiki.fetch_assets(csv_path, images, elements, fetch=net, progress=log.append, workers=2)
    assert (rep.aniimo, rep.downloaded, rep.already_had, rep.from_full_art, rep.failed) == (3, 3, 0, 2, [])
    assert rep.element_icons == 9 and rep.roster_note == "written"
    assert sorted(p.name for p in images.iterdir()) == ["emberpup.png", "inferlupa.png", "irisalis.png"]
    assert len(list(elements.iterdir())) == 9 and not list(images.glob("*.tmp"))
    for p in images.iterdir():
        assert Image.open(p).size == (256, 256)
    roster, warnings = load_roster(csv_path, images)  # the generated file loads through the real loader
    assert [a.name for a in roster.items] == ["Emberpup", "Inferlupa", "Irisalis"] and not warnings
    assert roster.get("inferlupa").elements == ("Fire", "Dark") and all(a.image_path for a in roster.items)
    assert roster.get("irisalis").role == "Healer"
    # second run: nothing to download again
    net.calls.clear()
    rep2 = wiki.fetch_assets(csv_path, images, elements, fetch=net, progress=log.append)
    assert (rep2.downloaded, rep2.already_had) == (0, 3)
    assert rep2.roster_note == "already up to date" and not (csv_path.parent / "roster.wiki.csv").exists()
    assert not [c for c in net.calls if "Wiki_" in c]
    rep3 = wiki.fetch_assets(csv_path, images, elements, fetch=net, progress=log.append, refresh=True)
    assert rep3.downloaded == 3


def test_failed_thumbnail_is_reported_not_fatal(tmp_path):
    csv_path, images, elements = dirs(tmp_path)

    class Net(FakeNet):
        def __call__(self, url, max_bytes):
            if "10051" in url:
                raise wiki.WikiNotFound(url)
            return super().__call__(url, max_bytes)

    rep = wiki.fetch_assets(csv_path, images, elements, fetch=Net(GOOD), progress=lambda _: None)
    assert len(rep.failed) == 1 and "emberpup" in rep.failed[0] and rep.downloaded == 2


def test_roster_file_is_never_overwritten_by_accident(tmp_path):
    csv_path, images, elements = dirs(tmp_path)
    csv_path.parent.mkdir(parents=True)
    csv_path.write_text("slug,name,element,role,image\nmine,My Own,Fire,,\n")
    net = FakeNet(GOOD)
    rep = wiki.fetch_assets(csv_path, images, elements, fetch=net, progress=lambda _: None)
    assert "roster.wiki.csv" in rep.roster_note and "My Own" in csv_path.read_text()
    assert (csv_path.parent / "roster.wiki.csv").exists()
    rep = wiki.fetch_assets(csv_path, images, elements, fetch=net, progress=lambda _: None, overwrite_roster=True)
    assert "replaced" in rep.roster_note and "Emberpup" in csv_path.read_text()
    assert len(list(csv_path.parent.glob("roster.csv.bak-*"))) == 1


def test_example_placeholder_roster_is_replaced(tmp_path):
    csv_path, images, elements = dirs(tmp_path)
    csv_path.parent.mkdir(parents=True)
    csv_path.write_text("slug,name,element,role,image\n# EXAMPLE ONLY: placeholders,,,,\nfoo,Foo,Fire,,\n")
    rep = wiki.fetch_assets(csv_path, images, elements, fetch=FakeNet(GOOD), progress=lambda _: None)
    assert rep.roster_note == "written" and "Foo" not in csv_path.read_text()


def test_element_icon_problem_is_a_warning_and_roster_still_works(tmp_path):
    csv_path, images, elements = dirs(tmp_path)

    class Net(FakeNet):
        def __call__(self, url, max_bytes):
            if url == wiki.SPRITE_URL:
                return png((10, 10))
            return super().__call__(url, max_bytes)

    rep = wiki.fetch_assets(csv_path, images, elements, fetch=Net(GOOD), progress=lambda _: None)
    assert rep.element_icons == 0 and any("element icons" in w for w in rep.warnings)
    assert rep.downloaded == 3


def test_element_icons_prefer_files_and_fall_back_to_generated(tmp_path):
    icons = ElementIcons(tmp_path, Fonts())
    gen = icons.get("Fire", 64)
    assert gen.size == (64, 64) and not icons.has_file("Fire")
    r, g, b, a = gen.getpixel((6, 32))
    assert (r, g, b) == (0xE2, 0x61, 0x61) and a == 255  # the wiki's fire colour
    (tmp_path / "fire.png").write_bytes(png((128, 128), (10, 20, 30, 255)))
    icons.clear()
    assert icons.has_file("Fire") and icons.get("Fire", 64).getpixel((32, 32)) == (10, 20, 30, 255)
    (tmp_path / "ice.png").write_bytes(b"not a png")
    assert icons.get("Ice", 32).size == (32, 32)  # unreadable file: generated badge instead of an error


def test_thumbnail_has_tinted_backdrop_and_whole_creature_visible(tmp_path):
    from aniimo_pvp.domain.roster import Aniimo

    img = tmp_path / "wind.png"
    img.write_bytes(creature((256, 256), (96, 40, 160, 216)))
    a = Aniimo("windy", "Windy", ("Wind",), "", img)
    t = ThumbCache(Fonts()).get(a, 128)
    assert t.size == (128, 128) and t.getpixel((0, 0))[3] == 0  # circular
    backdrop = t.getpixel((20, 64))
    assert backdrop[3] == 255 and backdrop[:3] != (0, 0, 0)  # tinted, not transparent, behind the art
    assert t.getpixel((64, 64))[:3] == (200, 40, 160)  # the creature itself
    banned = ThumbCache(Fonts()).get(a, 128, banned=True)
    assert banned.getpixel((64, 64))[0] == banned.getpixel((64, 64))[2]  # greyscale
