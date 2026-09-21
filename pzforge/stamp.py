"""Painted-stamp sprites: cut a kit of painted parts out of a reference sheet, then compose
new sprites from those parts along a computed structure.

Why this exists. The forge's render path (geometry -> toon ramp -> style passes) can be
made structurally right -- measured stems, leaf counts, crown sizes -- and it still reads
as CG next to a painted sheet: painted leaves are 8-12 px flat shapes with a dark rim and
thousands of tones, and no ramp over a mesh produces that. Six measured rounds on a fruit
tree closed every statistic and not the look. What DOES carry the look is the paint
itself. So this module splits the job the way a painter's studio would: the reference
sheet supplies the vocabulary (a leaf, a fruit pair, a blossom patch, the trunk) and the
pipeline supplies the composition (where each goes, in what depth layer, at what tone,
per growth stage), all of it measured off the sheet.

Kit extraction (``extract_kit``):
* **leaves** are cut as LIT FACES. In a painted crown every leaf is a light patch bounded
  by a dark rim, so thresholding value at the crown's median separates them, and growing
  each patch by one pixel (inside the green mask) brings its rim back. 60-220 stamps per
  sheet, 4-18 px.
* **fruit** are cut by the crop's own hue band (a table, because a pear is not a cherry),
  grown by two pixels so the stem and outline come along.
* **blossom** patches likewise, from the blossom stage.
* **bark**: the largest brown components of the sapling stages -- the painted trunk with
  its limbs, reused at every later stage by scaling.

Composition (``compose_tree``): per stage a crown ellipse, trunk size, leaf count, fruit
and blossom counts, all measured on the painted sheets (green-hue bbox and centroid,
brown-hue trunk width, fruit blob count). Leaves are laid in depth layers -- back ones
darker and duller, front ones full -- with a crown-to-skirt value ramp on top (painted
crowns measure +0.09..+0.23 top-third minus bottom-third); twelve dark twigs go in under
the front third; fruit sits on the front over the lower crown. Stamps are flipped and
scaled by nearest-neighbour only, never rotated, so their pixel edges stay drawn.
"""
from __future__ import annotations

import colorsys
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

CELL_W, CELL_H = 128, 256
BASE_Y = 246                 # painted trunks stand on rows 239-250; vanilla plants on 250


# --------------------------------------------------------------------------- #
# colour classification
# --------------------------------------------------------------------------- #

@dataclass
class Bands:
    """Hue bands (degrees) and value limits that pick a sheet's parts out."""
    leaf: tuple[float, float] = (55.0, 170.0)
    leaf_sat: float = 0.15
    fruit: tuple[float, float] = (330.0, 20.0)     # wraps when lo > hi
    fruit_sat: float = 0.35
    fruit_val: tuple[float, float] = (0.22, 1.0)
    bloom: tuple[float, float] = (280.0, 20.0)
    bloom_sat: tuple[float, float] = (0.10, 0.75)
    bloom_val: float = 0.45
    bark: tuple[float, float] = (0.0, 55.0)
    bark_sat: tuple[float, float] = (0.12, 0.95)
    bark_val: tuple[float, float] = (0.06, 0.62)


def _in_band(deg: float, band: tuple[float, float]) -> bool:
    lo, hi = band
    return (deg >= lo or deg <= hi) if lo > hi else (lo <= deg <= hi)


def _hsv(p):
    r, g, b = p[:3]
    return colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)


def classify(p, bands: Bands) -> str | None:
    if p[3] < 96:
        return None
    h, s, v = _hsv(p)
    d = h * 360.0
    if _in_band(d, bands.leaf) and s > bands.leaf_sat:
        return "leaf"
    if _in_band(d, bands.fruit) and s > bands.fruit_sat and bands.fruit_val[0] <= v <= bands.fruit_val[1]:
        return "fruit"
    if _in_band(d, bands.bark) and bands.bark_sat[0] <= s <= bands.bark_sat[1] \
            and bands.bark_val[0] <= v <= bands.bark_val[1]:
        return "bark"
    if _in_band(d, bands.bloom) and bands.bloom_sat[0] < s < bands.bloom_sat[1] and v > bands.bloom_val:
        return "bloom"
    return "other"


def _components(mask: set) -> list[list]:
    seen, out = set(), []
    for p in mask:
        if p in seen:
            continue
        stack, pts = [p], []
        seen.add(p)
        while stack:
            q = stack.pop()
            pts.append(q)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (q[0] + dx, q[1] + dy)
                    if n in mask and n not in seen:
                        seen.add(n)
                        stack.append(n)
        out.append(pts)
    return out


def _cut(im: Image.Image, pts, pad=1, grow=0, within=None) -> Image.Image:
    keep = set(pts)
    if grow:
        for (x, y) in list(keep):
            for dx in range(-grow, grow + 1):
                for dy in range(-grow, grow + 1):
                    q = (x + dx, y + dy)
                    if within is None or q in within:
                        keep.add(q)
    xs = [p[0] for p in keep]
    ys = [p[1] for p in keep]
    x0, y0 = max(0, min(xs) - pad), max(0, min(ys) - pad)
    x1, y1 = min(im.width, max(xs) + pad + 1), min(im.height, max(ys) + pad + 1)
    out = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    px, op = im.load(), out.load()
    for y in range(y0, y1):
        for x in range(x0, x1):
            if (x, y) in keep and px[x, y][3] > 0:
                op[x - x0, y - y0] = px[x, y]
    return out


# --------------------------------------------------------------------------- #
# kit extraction
# --------------------------------------------------------------------------- #

def extract_kit(sheet_path: Path, out_dir: Path, bands: Bands | None = None,
                leaf_cells=(3, 4, 6, 7), fruit_cell=7, bloom_cell=5,
                bark_cells=(3, 4)) -> dict[str, int]:
    """Cut a stamp kit out of an 8-cell growth sheet. Returns stamp counts per kind."""
    bands = bands or Bands()
    sheet = Image.open(sheet_path).convert("RGBA")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()

    def cell(i):
        return sheet.crop((i * CELL_W, 0, (i + 1) * CELL_W, CELL_H))

    kit: dict[str, list[Image.Image]] = {"leaf": [], "fruit": [], "bloom": [], "bark": []}
    for i in leaf_cells:
        im = cell(i)
        px = im.load()
        green = {(x, y) for y in range(CELL_H) for x in range(CELL_W)
                 if classify(px[x, y], bands) == "leaf"}
        if not green:
            continue
        vals = sorted(_hsv(px[p])[2] for p in green)
        med = vals[len(vals) // 2]
        lit = {p for p in green if _hsv(px[p])[2] > med * 1.02}
        for pts in _components(lit):
            if 10 <= len(pts) <= 110:
                st = _cut(im, pts, pad=1, grow=1, within=green)
                if 4 <= st.width <= 18 and 4 <= st.height <= 18:
                    kit["leaf"].append(st)
    for kind, i, lo, hi in (("fruit", fruit_cell, 12, 260), ("bloom", bloom_cell, 10, 160)):
        im = cell(i)
        px = im.load()
        mask = {(x, y) for y in range(CELL_H) for x in range(CELL_W)
                if classify(px[x, y], bands) == kind}
        for pts in _components(mask):
            if lo <= len(pts) <= hi:
                kit[kind].append(_cut(im, pts, pad=2, grow=2))
    for i in bark_cells:
        im = cell(i)
        px = im.load()
        mask = {(x, y) for y in range(CELL_H) for x in range(CELL_W)
                if classify(px[x, y], bands) == "bark"}
        for pts in _components(mask):
            if len(pts) >= 60:
                kit["bark"].append(_cut(im, pts, pad=1))
    counts = {}
    for kind, stamps in kit.items():
        stamps.sort(key=lambda s: -(s.width * s.height))
        for n, st in enumerate(stamps):
            st.save(out_dir / f"{kind}_{n:03d}.png")
        counts[kind] = len(stamps)
    return counts


def load_kit(kit_dir: Path) -> dict[str, list[Image.Image]]:
    kit_dir = Path(kit_dir)
    out: dict[str, list[Image.Image]] = {}
    for kind in ("leaf", "fruit", "bloom", "bark"):
        stamps = []
        for p in sorted(kit_dir.glob(f"{kind}_*.png")):
            im = Image.open(p).convert("RGBA")
            if kind == "leaf" and _carries_fruit(im):
                continue
            stamps.append(im)
        out[kind] = stamps
    return out


def _carries_fruit(im: Image.Image) -> bool:
    """Leaf stamps cut from the fruiting cells sometimes bring a fruit along."""
    bad = n = 0
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            if a < 96:
                continue
            n += 1
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            if s > 0.4 and (h * 360.0 < 75.0 or h * 360.0 > 330.0):
                bad += 1
    return bool(n) and bad / n > 0.06


# --------------------------------------------------------------------------- #
# stamp painting helpers
# --------------------------------------------------------------------------- #

def tint(im: Image.Image, k: float, desat: float = 0.0) -> Image.Image:
    out = im.copy()
    px = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            r2, g2, b2 = colorsys.hsv_to_rgb(h, s * (1.0 - desat), max(0.0, min(1.0, v * k)))
            px[x, y] = (int(r2 * 255 + 0.5), int(g2 * 255 + 0.5), int(b2 * 255 + 0.5), a)
    return out


def recolour(im: Image.Image, band: tuple[float, float], target_hue: float,
             sat: float = 1.0, val: float = 1.0, min_sat: float = 0.35) -> Image.Image:
    """Rotate one hue band of a stamp (a fruit's) to another hue; everything else stays."""
    out = im.copy()
    px = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            if not _in_band(h * 360.0, band) or s < min_sat:
                continue
            r2, g2, b2 = colorsys.hsv_to_rgb(target_hue / 360.0, min(1.0, s * sat), min(1.0, v * val))
            px[x, y] = (int(r2 * 255 + 0.5), int(g2 * 255 + 0.5), int(b2 * 255 + 0.5), a)
    return out


def _stamp(canvas: Image.Image, im: Image.Image, cx: float, cy: float, rng: random.Random,
           scale: float = 1.0, flip: bool = True) -> None:
    if scale != 1.0:
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                       Image.NEAREST)
    if flip and rng.random() < 0.5:
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
    canvas.alpha_composite(im, (int(cx - im.width / 2), int(cy - im.height / 2)))


def _in_ellipse(rng, cx, cy, rx, ry, edge_bias=0.0):
    a = rng.uniform(0.0, math.tau)
    r = math.sqrt(rng.random()) if edge_bias == 0 else rng.random() ** (0.5 - edge_bias * 0.3)
    return cx + math.cos(a) * rx * r, cy + math.sin(a) * ry * r


# --------------------------------------------------------------------------- #
# tree composition
# --------------------------------------------------------------------------- #

#: Per stage, measured on Tree_Cherry/Peach/Pear/Olive: crown (cx, cy, rx, ry) px, leaf
#: stamp count (sized to the sheets' foliage pixel count: ~12.7k at full crown), trunk
#: (width, height) px, and what the crown carries.
TREE_STAGES = {
    2: dict(crown=(60, 219, 15, 15), leaves=14, trunk=(4, 34)),
    3: dict(crown=(59, 198, 26, 31), leaves=48, trunk=(7, 62)),
    4: dict(crown=(60, 150, 58, 56), leaves=270, trunk=(20, 122)),
    5: dict(crown=(62, 106, 62, 78), leaves=430, trunk=(25, 165), bloom=True),
    6: dict(crown=(62, 106, 62, 78), leaves=430, trunk=(25, 165), fruit="unripe"),
    7: dict(crown=(62, 106, 62, 78), leaves=430, trunk=(25, 165), fruit="ripe"),
}


@dataclass
class TreeSpec:
    """What a crop needs beyond the kit: how many fruit, and any recolouring."""
    fruit_count: int = 22
    fruit_scale: float = 1.0
    #: (band, hue, sat, val) applied to fruit stamps for the RIPE stage, e.g. to make an
    #: apple out of a pear kit; None keeps the kit's paint
    ripe_recolour: tuple | None = None
    #: same for the unripe stage; default turns the ripe band yellow-green
    unripe_recolour: tuple | None = None
    fruit_band: tuple[float, float] = (330.0, 20.0)
    canopy_ramp: float = 0.30
    seed: int = 7


def compose_tree(kit: dict, stage: int, spec: TreeSpec, rng: random.Random) -> Image.Image:
    leaf, fruit, bloom, bark = kit["leaf"], kit["fruit"], kit["bloom"], kit["bark"]
    c = Image.new("RGBA", (CELL_W, CELL_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(c)
    if stage == 0:
        src = bark[-1] if bark else leaf[0]
        seed_im = tint(src.resize((13, 16), Image.NEAREST), 0.85)
        c.alpha_composite(seed_im, (64 - 6, BASE_Y - 15))
        return c
    if stage == 1:
        d.line([(64, BASE_Y - 1), (64, BASE_Y - 12)], fill=(64, 92, 40, 255), width=1)
        for dx, dy in ((-7, -12), (6, -14), (0, -19)):
            _stamp(c, rng.choice(leaf), 64 + dx, BASE_Y + dy, rng, scale=0.8)
        return c
    st = TREE_STAGES[stage]
    cx, cy, rx, ry = st["crown"]
    tw, th = st["trunk"]
    d.ellipse([64 - 23, BASE_Y - 6, 64 + 23, BASE_Y + 6], fill=(10, 8, 6, 120))
    if bark:
        trunk = bark[0]
        sx, sy = tw / 18.0, th / trunk.height
        trunk = trunk.resize((max(2, round(trunk.width * sx)), max(4, round(trunk.height * sy))),
                             Image.NEAREST if sx >= 1 else Image.LANCZOS)
        c.alpha_composite(trunk, (64 - trunk.width // 2 + 2, BASE_Y - trunk.height))
    else:
        d.rectangle([64 - tw // 2, BASE_Y - th, 64 + tw // 2, BASE_Y], fill=(52, 34, 18, 255))
    n = st["leaves"]
    sites = sorted((rng.random(),) + _in_ellipse(rng, cx, cy, rx, ry, edge_bias=0.12)
                   for _ in range(n))
    bloom_share = 0.72 if st.get("bloom") and bloom else 0.0
    for i, (depth, x, y) in enumerate(sites):
        if i == int(n * 0.66):
            for _ in range(12):
                ex, ey = _in_ellipse(rng, cx, cy, rx * 0.85, ry * 0.85)
                sx0, sy0 = 64 + rng.uniform(-tw * 0.3, tw * 0.3), BASE_Y - th * 0.55
                mx, my = (sx0 + ex) / 2 + rng.uniform(-6, 6), (sy0 + ey) / 2
                d.line([(sx0, sy0), (mx, my), (ex, ey)], fill=(46, 30, 16, 255),
                       width=2 if rng.random() < 0.4 else 1)
        ty = (y - (cy - ry)) / (2 * ry)
        k = (0.60 + 0.40 * depth) * (1.0 + spec.canopy_ramp * (0.5 - ty))
        desat = 0.25 * (1.0 - depth)
        if bloom_share and rng.random() < bloom_share:
            im, sc = rng.choice(bloom), rng.choice((0.7, 0.8, 0.9))
        else:
            im, sc = rng.choice(leaf), rng.choice((0.85, 1.0, 1.0, 1.0, 1.1))
        _stamp(c, tint(im, k, desat), x, y, rng, scale=sc)
    if st.get("fruit") and fruit:
        for _ in range(spec.fruit_count):
            x, y = _in_ellipse(rng, cx, cy + ry * 0.12, rx * 0.86, ry * 0.84)
            im = rng.choice(fruit)
            if st["fruit"] == "unripe":
                rc = spec.unripe_recolour or (spec.fruit_band, 62.0, 0.85, 0.95)
                im = recolour(im, *rc)
            elif spec.ripe_recolour:
                im = recolour(im, *spec.ripe_recolour)
            _stamp(c, im, x, y, rng, scale=spec.fruit_scale)
    return c


def compose_sheet(kit: dict, spec: TreeSpec, out_dir: Path, sheet_name: str) -> Path:
    """Write the 8 cells and a build manifest into ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cells = []
    for stage in range(8):
        rng = random.Random(spec.seed * 100 + stage)
        im = compose_tree(kit, stage, spec, rng)
        fn = f"{sheet_name}_S_x{stage}_y0.png"
        im.save(out_dir / fn)
        cells.append({"file": fn, "facing": "S", "x": stage, "y": 0})
    manifest = {"sheet": sheet_name, "cell": [CELL_W, CELL_H], "scale": "2x",
                "footprint": [8, 1], "isolate_tiles": False, "facings": ["S"],
                "painted": True, "cells": cells}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return out_dir
