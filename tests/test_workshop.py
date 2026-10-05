"""Workshop images without the game: layered columns, the promo frame, the thumbnail's
layout, the fluid tint, the contact-shadow strip, the driver and the Home Brewing spec.

Run with:  uv run --python 3.12 --with pillow python tests/test_workshop.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pzforge import workshop as ws  # noqa: E402
from pzforge.cli import main as cli_main  # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  -- ' + detail if detail else ''}")
    if not ok:
        FAILURES.append(label)


def block(w: int, h: int, colour: tuple, box: tuple) -> Image.Image:
    """A w x h transparent image with ``box`` (x0, y0, x1, y1) filled with ``colour``."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for y in range(box[1], box[3]):
        for x in range(box[0], box[2]):
            im.putpixel((x, y), colour)
    return im


def sprites(folder: Path) -> ws.Sources:
    folder.mkdir(parents=True, exist_ok=True)
    # 2x cells: a floor diamond-ish block, a post, a cask with a faint dark contact shadow
    block(128, 256, (90, 80, 70, 255), (0, 224, 128, 256)).save(folder / "floor_0.png")
    block(128, 256, (200, 40, 40, 255), (60, 200, 68, 256)).save(folder / "post_0.png")
    cask = block(128, 256, (40, 200, 40, 255), (40, 216, 88, 248))
    for x in range(30, 98):
        cask.putpixel((x, 250), (5, 5, 5, 40))          # the baked contact shadow
    cask.save(folder / "cask_0.png")
    # 32 px icons: a solid one, and a bottle with a grey mask
    block(32, 32, (30, 30, 200, 255), (8, 2, 24, 30)).save(folder / "Item_Solid.png")
    block(32, 32, (220, 220, 220, 255), (12, 0, 20, 32)).save(folder / "Item_Bottle.png")
    block(32, 32, (200, 200, 200, 255), (13, 6, 19, 31)).save(folder / "Item_Bottle_Mask.png")
    return ws.Sources(ws.DirSource(folder))


def test_column_and_promo(src: ws.Sources) -> None:
    print("\n== column + promo ==")
    col = ws.column(src, [["post_0", 0], ["cask_0", 10]])
    check("a column is one 2x cell", col.size == (128, 256), str(col.size))
    check("a layer lifted 10 px (1x) sits 20 px higher on the 2x cell",
          col.getpixel((64, 216 - 20))[:3] == (40, 200, 40) and col.getpixel((64, 247 - 20))[:3] == (40, 200, 40)
          and col.getpixel((64, 249))[:3] == (200, 40, 40), str(col.getpixel((64, 196))))
    frame = ws.promo({"grid": [2, 2], "floor": "floor_0", "frame": [600, 500], "background": [10, 20, 30],
                      "columns": {"c": [["post_0", 0], ["cask_0", 10]]}, "place": [[0, 0, "c"]]}, src)
    check("the promo frame has the asked size", frame.size == (600, 500))
    check("its background is the asked colour", frame.getpixel((2, 2)) == (10, 20, 30, 255))


def test_thumbnail_parts(src: ws.Sources) -> None:
    print("\n== thumbnail parts ==")
    stripped = ws.strip_contact_shadow(src.need("cask_0"))
    check("the faint near-black contact shadow is dropped", stripped.getpixel((31, 250))[3] == 0)
    check("the body is kept", stripped.getpixel((64, 230)) == (40, 200, 40, 255))
    icon = ws.tinted(src.need("Item_Bottle"), src.need("Item_Bottle_Mask"), (128, 0, 0))
    check("the mask is multiplied by the fluid colour (200 x 128/255 = 100)",
          icon.getpixel((15, 10)) == (100, 0, 0, 255), str(icon.getpixel((15, 10))))
    check("outside the mask the base icon shows", icon.getpixel((12, 1)) == (220, 220, 220, 255))
    check("x2.5 nearest rounds the size", ws.scaled(Image.new("RGBA", (11, 31)), 2.5).size == (28, 78))


def test_thumbnail_layout(src: ws.Sources) -> None:
    print("\n== thumbnail layout ==")
    cfg = {"title": "Test Mod", "subtitle": "One - Two (B42)",
           "stations": [{"sprite": "post_0", "scale": 2, "x": 16, "bottom": 446},
                        {"sprite": "cask_0", "scale": 2, "after": 0, "overlap": 4, "bottom": 494}],
           "floor_shadow": {"top": 432, "bottom": 506, "pad": [6, 10]},
           "icons": {"items": [{"icon": "Item_Solid"}, {"icon": "Item_Bottle", "mask": "Item_Bottle_Mask",
                                                          "color": [128, 0, 0]}]}}
    image = ws.thumbnail(cfg, src)
    check("the house size", image.size == (512, 512))
    # post: 8 x 56 at x2 = 16 x 112, x 16..32, bottom 446; cask after it, 4 px in
    check("the first station stands at its x and bottom", image.getpixel((20, 440))[:3] == (200, 40, 40))
    cask_x = 16 + 16 - 4
    check("the next one starts 'overlap' px into the one before it",
          image.getpixel((cask_x + 2, 480))[:3] == (40, 200, 40) and image.getpixel((cask_x - 2, 480))[:3] != (40, 200, 40))
    check("icons are centred on the icon column, the first at its top",
          image.getpixel((452, 166))[:3] == (30, 30, 200))
    check("the second icon is a step below, its mask tinted",
          image.getpixel((452, 166 + 92))[:3] == (100, 0, 0), str(image.getpixel((452, 258))))
    title_px = [image.getpixel((x, 45)) for x in range(30, 200)]
    check("the title is drawn", any(p[0] > 200 for p in title_px))


def test_run(src_dir: Path, tmp: Path) -> None:
    print("\n== driver + command ==")
    publish = tmp / "workshop_folder"
    publish.mkdir()
    spec = {"workshop": {"publish": str(publish),
                         "promo": {"out": "out/promo.png", "publish_as": "promo.png", "sprite_dirs": [str(src_dir)],
                                   "grid": [2, 2], "floor": "floor_0", "place": [[1, 1, "cask_0"]]},
                         "thumbnail": {"out": "out/thumb.png", "publish_as": "preview.png", "sprite_dirs": [str(src_dir)],
                                       "title": "Test", "stations": [{"sprite": "cask_0", "x": 20, "bottom": 480}]}}}
    lines: list[str] = []
    failures = ws.run(spec, root=tmp, log=lines.append)
    check("both pictures written", failures == 0 and (tmp / "out/promo.png").exists() and (tmp / "out/thumb.png").exists(),
          "; ".join(lines))
    check("the promo keeps alpha, the thumbnail is RGB (Steam's preview)",
          Image.open(tmp / "out/promo.png").mode == "RGBA" and Image.open(tmp / "out/thumb.png").mode == "RGB")
    check("nothing published without --publish", not any(publish.iterdir()))
    ws.run(spec, root=tmp, publish=True, only={"thumbnail"}, log=lines.append)
    check("--publish copies under the uploader's name, --only limits it",
          sorted(p.name for p in publish.iterdir()) == ["preview.png"])
    spec["workshop"]["thumbnail"]["stations"][0]["sprite"] = "missing_0"
    check("a sprite the packs do not have is a failure", ws.run(spec, root=tmp, log=lines.append) == 1)
    path = tmp / "spec.json"
    spec["workshop"]["thumbnail"]["stations"][0]["sprite"] = "cask_0"
    path.write_text(json.dumps(spec), encoding="utf-8")
    import os
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        check("the workshop command exits 0", cli_main(["workshop", str(path), "--only", "promo"]) == 0)
    finally:
        os.chdir(cwd)


def test_homebrewing_spec() -> None:
    print("\n== homebrewing spec ==")
    data = json.loads((ROOT / "examples" / "homebrewing_assets.json").read_text(encoding="utf-8"))
    w = data.get("workshop", {})
    check("a workshop section with a promo and a thumbnail", "promo" in w and "thumbnail" in w)
    sheets = {a["sheet"] for a in data["assets"]}
    for kind in ("promo", "thumbnail"):
        packs = {p[: -len(".pack")] for p in w.get(kind, {}).get("packs", [])}
        check(f"the {kind} reads only sheets the spec builds", packs <= sheets, str(packs - sheets))
    names = [n for layers in w["promo"]["columns"].values() for n, _ in layers]
    names += [p[2] for p in w["promo"]["place"] if p[2] not in w["promo"]["columns"]]
    names += [s["sprite"] for s in w["thumbnail"]["stations"]]
    check("every sprite named belongs to one of those sheets",
          all(any(n.startswith(s + "_") for s in sheets) for n in names), str(names))
    check("the thumbnail publishes as preview.png", w["thumbnail"].get("publish_as") == "preview.png")


if __name__ == "__main__":
    tmp = Path(tempfile.mkdtemp(prefix="pzforge_workshop_"))
    try:
        src_dir = tmp / "sprites"
        src = sprites(src_dir)
        test_column_and_promo(src)
        test_thumbnail_parts(src)
        test_thumbnail_layout(src)
        test_run(src_dir, tmp)
        test_homebrewing_spec()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'ALL PASS' if not FAILURES else 'FAILED: ' + ', '.join(FAILURES)}")
    raise SystemExit(1 if FAILURES else 0)
