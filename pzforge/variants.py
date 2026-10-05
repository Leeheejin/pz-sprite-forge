"""Health variants of a crop sprite, derived the way vanilla derives its own.

Vanilla ships four sheets per farming crop -- healthy, unhealthy, dying, dead -- and the
last three are hue-keyed colour transforms of the first: the alpha bytes are identical
(silhouette IoU 1.0000 for unhealthy) and a pixel's new colour depends only on its own
source hue. ``reference/health_lut.json`` holds that transform, measured by pairing pixels
across BellPepper, Tomato, Cabbages, Barley and Strawberry: per 15-degree hue bin, the
median hue shift, saturation ratio and value ratio. Greens (45-135 deg) rotate toward
yellow-brown and darken -- dead foliage lands near hue 35 at 0.73-0.88x value -- while the
soil browns (15-45 deg) are left byte-identical, which is what keeps the bed the same
under a dying plant.
"""
from __future__ import annotations

import colorsys
import json
from pathlib import Path

from PIL import Image

VARIANTS = ("unhealthy", "dying", "dead")
_LUT_PATH = Path(__file__).resolve().parents[1] / "reference" / "health_lut.json"
_BIN = 15.0


def load(path: Path | None = None) -> dict:
    return json.loads((path or _LUT_PATH).read_text(encoding="utf-8"))


def factors(table: dict, hue_deg: float) -> tuple[float, float, float]:
    """(hue shift, sat ratio, val ratio) at ``hue_deg``, interpolated between the bin
    centres. Hues past the last measured bin (blue/purple fruit, which vanilla never
    paints) take the last bin's value ratio with no hue shift, so a dead vine still
    darkens its grapes without turning them green."""
    keys = sorted(int(k) for k in table)
    centres = [k + _BIN / 2.0 for k in keys]
    if hue_deg <= centres[0]:
        e = table[str(keys[0])]
        return e["dhue"], e["sat"], e["val"]
    if hue_deg >= centres[-1]:
        e = table[str(keys[-1])]
        return 0.0, min(e["sat"], 1.0), e["val"]
    for k0, k1, c0, c1 in zip(keys, keys[1:], centres, centres[1:]):
        if c0 <= hue_deg <= c1:
            a, b = table[str(k0)], table[str(k1)]
            t = (hue_deg - c0) / (c1 - c0)
            return (a["dhue"] + (b["dhue"] - a["dhue"]) * t,
                    a["sat"] + (b["sat"] - a["sat"]) * t,
                    a["val"] + (b["val"] - a["val"]) * t)
    return 0.0, 1.0, 1.0


def derive(img: Image.Image, variant: str, lut: dict | None = None) -> Image.Image:
    """The ``variant`` sheet of a healthy sprite. Alpha is untouched."""
    lut = lut or load()
    table = lut[variant]
    out = img.convert("RGBA").copy()
    px = out.load()
    w, h = out.size
    cache: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            key = (r, g, b)
            if key not in cache:
                hh, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
                if s < 0.12 or v < 0.08:
                    cache[key] = key
                else:
                    dh, sx, vx = factors(table, hh * 360.0)
                    h2 = ((hh * 360.0 + dh) % 360.0) / 360.0
                    r2, g2, b2 = colorsys.hsv_to_rgb(h2, min(1.0, s * sx), min(1.0, v * vx))
                    cache[key] = (int(r2 * 255 + 0.5), int(g2 * 255 + 0.5),
                                  int(b2 * 255 + 0.5))
            px[x, y] = (*cache[key], a)
    return out
