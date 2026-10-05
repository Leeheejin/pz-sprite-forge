"""Workshop images built from the packs a mod ships: the promo scene and the key image.

A Workshop page needs two kinds of picture, and both used to be one-off scripts:

- the **promo**, the mod's tiles on a vanilla floor, composed the way the game draws
  them. A layered column (a rack, its decks and the barrels on its tiers) is its
  layers in draw order, each lifted by its render y offset -- the `stack` command's
  rule, so the promo cannot show an order or a height the game would not.
- the **thumbnail** (Steam's ``preview.png``), the key image in the house style the
  Food Preservation and Home Brewing thumbnails share: a warm radial background,
  a Georgia Bold title over a soft shadow, a Georgia subtitle, the mod's stations
  as game sprites at whole-number scale, and a column of item icons drawn the way
  the game draws a full one (the base icon, then its fluid mask multiplied by the
  fluid's colour).

Both read the sprites from the mod's own ``.pack`` files (and vanilla's), so a picture
can never show art the mod does not ship, and both are driven by the asset spec's
``"workshop"`` section, so they are rebuilt with the tile set instead of drifting from
it. ``python -m pzforge.cli workshop <spec.json> [--only promo,thumbnail] [--publish]``
writes them; ``--publish`` also copies each one into the mod's Workshop folder under the
name the uploader wants (``preview.png`` for the thumbnail).
"""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .preview import CELL_H, CELL_W, DEFAULT_GAME_MEDIA, SpriteSource, compose

#: The thumbnail's house style, measured on the Food Preservation thumbnail
#: (background sampled at its centre and corners; title width matched to Georgia
#: Bold 50). A spec's ``"style"`` overrides any of these.
HOUSE_STYLE = {
    "size": 512,
    "background": {"inner": [75, 51, 50], "outer": [22, 17, 15], "centre": [0.5, 0.55], "radius": 0.78},
    "title": {"font": "georgiab.ttf", "size": 50, "xy": [26, 18], "fill": [240, 228, 205, 255],
              "shadow": [0, 0, 0, 170], "offset": [2, 3]},
    "subtitle": {"font": "georgia.ttf", "size": 21, "xy": [30, 80], "fill": [232, 222, 200, 255],
                 "shadow": [0, 0, 0, 140], "offset": [1, 2]},
    "floor_shadow": {"alpha": 110, "blur": 10},
    "icons": {"scale": 2.5, "x": 452, "top": 166, "step": 92},
}
FONT_DIRS = (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/msttcorefonts"), Path("/Library/Fonts"))
#: Steam rejects a preview image over 1 MB.
PREVIEW_LIMIT = 1024 * 1024


# --------------------------------------------------------------------------- #
# sprite sources
# --------------------------------------------------------------------------- #

class DirSource:
    """Sprites from a folder of PNGs named ``<sprite>.png`` (an ``extract`` output, a test)."""

    def __init__(self, folder: Path | str):
        self.folder = Path(folder)

    def get(self, name: str) -> Image.Image | None:
        path = self.folder / f"{name}.png"
        return Image.open(path).convert("RGBA") if path.exists() else None


class Sources:
    """Several sprite sources; the first one that has a name wins."""

    def __init__(self, *sources):
        self.sources = [s for s in sources if s is not None]

    def get(self, name: str) -> Image.Image | None:
        for source in self.sources:
            image = source.get(name)
            if image is not None:
                return image
        return None

    def need(self, name: str) -> Image.Image:
        image = self.get(name)
        if image is None:
            raise KeyError(f"no sprite {name!r} in this picture's packs")
        return image


def sources_for(cfg: dict, media: Path | None, game_media: Path) -> Sources:
    """``packs`` are the mod's (``<media>/texturepacks``), ``game_packs`` vanilla's,
    ``sprite_dirs`` folders of PNGs; looked up in that order."""
    mod = [Path(media) / "texturepacks" / n for n in cfg.get("packs", [])] if media else []
    game = [Path(game_media) / "texturepacks" / n for n in cfg.get("game_packs", [])]
    return Sources(SpriteSource.from_packs(mod) if mod else None,
                   SpriteSource.from_packs(game) if game else None,
                   *[DirSource(d) for d in cfg.get("sprite_dirs", [])])


# --------------------------------------------------------------------------- #
# promo
# --------------------------------------------------------------------------- #

def column(src: Sources, layers: list) -> Image.Image:
    """One 2x cell holding a layered column. ``layers`` are ``[sprite, offset]`` in the
    game's draw order; each is lifted by its render y offset (1x px, so twice that on
    the 2x sheet). Anything lifted past the top of the cell is cut, as it would be off
    the tile's sprite in the game."""
    top = max((int(off) for _, off in layers), default=0)
    tall = Image.new("RGBA", (CELL_W, CELL_H + 2 * top), (0, 0, 0, 0))
    for name, off in layers:
        image = src.need(name)
        tall.alpha_composite(image, (0, tall.height - image.height - int(off) * 2))
    return tall.crop((0, tall.height - CELL_H, CELL_W, tall.height))


def promo(cfg: dict, src: Sources) -> Image.Image:
    """``grid`` [cols, rows] of ``floor``, the ``place`` list [i, j, sprite-or-column] on
    it, the scene centred at 1:1 in a ``frame`` of ``background``."""
    cols, rows = cfg["grid"]
    background = tuple(cfg.get("background", (26, 28, 32))) + (255,)
    placements = []
    if cfg.get("floor"):
        floor = src.need(cfg["floor"])
        placements = [(i, j, floor) for i in range(cols) for j in range(rows)]
    columns = {name: column(src, layers) for name, layers in cfg.get("columns", {}).items()}
    for i, j, what in cfg.get("place", []):
        placements.append((i, j, columns[what] if what in columns else src.need(what)))
    scene = compose(placements, cols, rows, background=background)
    fw, fh = cfg.get("frame", scene.size)
    frame = Image.new("RGBA", (fw, fh), background)
    frame.alpha_composite(scene, ((fw - scene.width) // 2, (fh - scene.height) // 2))
    return frame


# --------------------------------------------------------------------------- #
# thumbnail
# --------------------------------------------------------------------------- #

def strip_contact_shadow(image: Image.Image) -> Image.Image:
    """Drop a build's baked contact shadow (``--ground-shadow``: faint, near-black
    pixels). At game size it seats the object; scaled up for a thumbnail it reads as a
    dark tile, so the thumbnail draws one soft shadow under the group instead."""
    out = image.copy()
    px = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, a = px[x, y]
            if 0 < a < 64 and r + g + b < 96:
                px[x, y] = (0, 0, 0, 0)
    return out


def tinted(base: Image.Image, mask: Image.Image | None, rgb) -> Image.Image:
    """An item icon as the game draws a full one: the base, then the fluid mask
    multiplied by the fluid colour (a script's ColorReference, zombie.core.Colors)."""
    out = base.copy()
    if mask is not None and rgb:
        tint = Image.new("RGBA", mask.size)
        mp, tp = mask.load(), tint.load()
        for y in range(mask.height):
            for x in range(mask.width):
                r, g, b, a = mp[x, y]
                if a:
                    tp[x, y] = (r * rgb[0] // 255, g * rgb[1] // 255, b * rgb[2] // 255, a)
        out.alpha_composite(tint)
    return out


def trimmed(image: Image.Image) -> Image.Image:
    return image.crop(image.getbbox())


def scaled(image: Image.Image, k: float) -> Image.Image:
    """Nearest-neighbour: the game's pixels stay pixels."""
    return image.resize((round(image.width * k), round(image.height * k)), Image.NEAREST)


def font(name: str, size: int):
    """The named TrueType font (a bare name is looked up in the system font folders);
    Pillow's built-in face when it is not installed, so a picture still builds."""
    path = Path(name)
    if not path.is_absolute():
        for folder in FONT_DIRS:
            if (folder / name).exists():
                path = folder / name
                break
    if path.exists():
        return ImageFont.truetype(str(path), size)
    print(f"      font {name} not found; using Pillow's default face")
    try:
        return ImageFont.load_default(size)
    except TypeError:          # Pillow < 10.1 has no sized default
        return ImageFont.load_default()


def background(size: int, bg: dict) -> Image.Image:
    image = Image.new("RGBA", (size, size))
    px = image.load()
    inner, outer = bg["inner"], bg["outer"]
    cx, cy, rmax = size * bg["centre"][0], size * bg["centre"][1], size * bg["radius"]
    for y in range(size):
        for x in range(size):
            t = min(1.0, math.hypot(x - cx, y - cy) / rmax)
            t = t * t * (3 - 2 * t)
            px[x, y] = tuple(int(inner[i] + (outer[i] - inner[i]) * t) for i in range(3)) + (255,)
    return image


def shadowed_text(image: Image.Image, text: str, st: dict) -> None:
    face = font(st["font"], st["size"])
    x, y = st["xy"]
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((x + st["offset"][0], y + st["offset"][1]), text, font=face,
                               fill=tuple(st["shadow"]))
    image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(1.2)))
    ImageDraw.Draw(image).text((x, y), text, font=face, fill=tuple(st["fill"]))


def _merged(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merged(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def thumbnail(cfg: dict, src: Sources) -> Image.Image:
    """``title``/``subtitle``; ``stations`` [{sprite, scale, bottom, x | after+overlap}]
    left to right, each placed at its own ``x`` or ``overlap`` px into the station
    ``after`` it; ``floor_shadow`` {top, bottom, pad: [left, right]} under them all;
    ``icons`` {items: [{icon, mask?, color?}]} down the right-hand column."""
    style = _merged(HOUSE_STYLE, cfg.get("style", {}))
    size = style["size"]
    image = background(size, style["background"])
    shadowed_text(image, cfg["title"], style["title"])
    if cfg.get("subtitle"):
        shadowed_text(image, cfg["subtitle"], style["subtitle"])

    placed = []      # (image, (x, y))
    for st in cfg.get("stations", []):
        sprite = scaled(trimmed(strip_contact_shadow(src.need(st["sprite"]))), st.get("scale", 2))
        if "after" in st:
            prev, (px, _) = placed[st["after"]]
            x = px + prev.width - st.get("overlap", 0)
        else:
            x = st.get("x", 0)
        placed.append((sprite, (x, st["bottom"] - sprite.height)))
    if placed and cfg.get("floor_shadow"):
        fs = _merged(style["floor_shadow"], cfg["floor_shadow"])
        left = min(xy[0] for _, xy in placed) - fs["pad"][0]
        right = max(xy[0] + im.width for im, xy in placed) + fs["pad"][1]
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).ellipse((left, fs["top"], right, fs["bottom"]), fill=(0, 0, 0, fs["alpha"]))
        image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(fs["blur"])))
    for sprite, xy in placed:
        image.alpha_composite(sprite, xy)

    ic = _merged(style["icons"], {k: v for k, v in cfg.get("icons", {}).items() if k != "items"})
    for n, item in enumerate(cfg.get("icons", {}).get("items", [])):
        mask = src.need(item["mask"]) if item.get("mask") else None
        icon = scaled(trimmed(tinted(src.need(item["icon"]), mask, item.get("color"))), ic["scale"])
        cx, cy = ic["x"], ic["top"] + n * ic["step"]
        image.alpha_composite(icon, (cx - icon.width // 2, cy - icon.height // 2))
    return image


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #

KINDS = {
    # kind: (composer, how it is saved)
    "promo": (promo, {"mode": "RGBA", "optimize": False}),
    "thumbnail": (thumbnail, {"mode": "RGB", "optimize": True}),
}


def save(image: Image.Image, path: Path, how: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = image.convert(how["mode"]) if image.mode != how["mode"] else image
    if how["optimize"]:
        out.save(path, optimize=True)
    else:
        out.save(path)


def run(spec: Path | str | dict, *, only: set[str] | None = None, publish: bool = False,
        root: Path | None = None, log=print) -> int:
    """Write every picture the spec's ``workshop`` section names. Returns the number
    of failures (a missing sprite, a preview over Steam's 1 MB)."""
    data = spec if isinstance(spec, dict) else json.loads(Path(spec).read_text(encoding="utf-8"))
    ws = data.get("workshop")
    if not ws:
        log("FAIL  the spec has no \"workshop\" section")
        return 1
    root = Path(root) if root else Path.cwd()
    media = Path(ws["media"]) if ws.get("media") else None
    game_media = Path(ws.get("game_media") or DEFAULT_GAME_MEDIA)
    failures = 0
    for kind, (compose_fn, how) in KINDS.items():
        cfg = ws.get(kind)
        if not cfg or (only and kind not in only):
            continue
        try:
            image = compose_fn(cfg, sources_for(cfg, media, game_media))
        except KeyError as err:
            log(f"FAIL  {kind}: {err.args[0]}")
            failures += 1
            continue
        out = root / cfg["out"]
        save(image, out, {**how, **cfg.get("save", {})})
        size = out.stat().st_size
        ok = not (kind == "thumbnail" and size > PREVIEW_LIMIT)
        log(f"{'PASS' if ok else 'FAIL'}  {kind}: {out}  ({image.width}x{image.height}, {size} bytes)"
            + ("" if ok else "  -- over Steam's 1 MB preview limit"))
        failures += 0 if ok else 1
        if ok and publish and ws.get("publish") and cfg.get("publish_as"):
            dest = Path(ws["publish"]) / cfg["publish_as"]
            shutil.copyfile(out, dest)
            log(f"      published {dest}")
    return failures
