"""Diff a recreated sprite against the vanilla one it is copying.

This is the workflow that actually finds problems. The aggregate scores in
:mod:`pzforge.check` tell you whether a sprite sits inside vanilla's tonal range;
they cannot tell you that your barrel has three fat bright rings where the original
has two thin dark grooves. Only looking at the reference does that -- so this puts
the two side by side, marks where the silhouettes disagree, and reports the numbers
that are worth arguing with.
"""

from __future__ import annotations

import colorsys
import io
import os
from pathlib import Path

from PIL import Image

from .packfile import TexturePack
from .style import measure

DEFAULT_GAME_MEDIA = Path(
    os.environ.get("PZ_MEDIA")
    or r"C:\Program Files (x86)\Steam\steamapps\common\ProjectZomboid\media")

#: Low on purpose: near-grey sprites sit around 0.03 saturation, and a 0.08 cut-off
#: reports them as having no hue at all.
HUE_SATURATION_FLOOR = 0.015


def vanilla_sprite(name: str, game_media: Path = DEFAULT_GAME_MEDIA) -> Image.Image:
    for pack_name in ("Tiles2x.pack", "Tiles2x.floor.pack"):
        path = game_media / "texturepacks" / pack_name
        if not path.exists():
            continue
        pack = TexturePack.read(path)
        for page in pack.pages:
            for e in page.entries:
                if e.name != name:
                    continue
                atlas = Image.open(io.BytesIO(page.png)).convert("RGBA")
                cell = Image.new("RGBA", (e.ow, e.oh), (0, 0, 0, 0))
                cell.paste(atlas.crop((e.x, e.y, e.x + e.w, e.y + e.h)), (e.ox, e.oy))
                return cell
    raise ValueError(f"sprite {name!r} not found under {game_media}")


def _mask(img: Image.Image, threshold: int = 128) -> list[bool]:
    return [p[3] > threshold for p in img.convert("RGBA").getdata()]


def silhouette(a: Image.Image, b: Image.Image) -> dict:
    ma, mb = _mask(a), _mask(b)
    inter = sum(1 for x, y in zip(ma, mb) if x and y)
    union = sum(1 for x, y in zip(ma, mb) if x or y)
    return {"iou": inter / union if union else 0.0,
            "vanilla_pixels": sum(ma), "mine_pixels": sum(mb),
            "vanilla_box": a.getbbox(), "mine_box": b.getbbox()}


def light_balance(img: Image.Image) -> dict:
    """Mean luminance of the left half, right half and upper region of the sprite."""
    img = img.convert("RGBA")
    box = img.getbbox()
    if box is None:
        return {}
    left, upper, right, lower = box
    px = img.load()
    mid_x = (left + right) / 2
    upper_cut = upper + (lower - upper) * 0.35

    buckets: dict[str, list[float]] = {"left": [], "right": [], "top": []}
    for y in range(upper, lower):
        for x in range(left, right):
            r, g, b, a = px[x, y]
            if a < 200:
                continue
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            buckets["top" if y < upper_cut else
                    ("left" if x < mid_x else "right")].append(lum)
    out = {k: (sum(v) / len(v) if v else float("nan")) for k, v in buckets.items()}
    out["left_over_right"] = (out["left"] / out["right"]
                              if out["right"] else float("nan"))
    return out


def edge_softness(img: Image.Image) -> float:
    alphas = [p[3] for p in img.convert("RGBA").getdata() if p[3]]
    return sum(1 for a in alphas if a < 250) / len(alphas) if alphas else 0.0


def median_hue(img: Image.Image) -> float:
    hues = []
    for r, g, b, a in img.convert("RGBA").getdata():
        if a < 200:
            continue
        h, s, _v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s > HUE_SATURATION_FLOOR:
            hues.append(h * 360)
    if not hues:
        return float("nan")
    hues.sort()
    return hues[len(hues) // 2]


def difference_image(vanilla: Image.Image, mine: Image.Image) -> Image.Image:
    w, h = vanilla.size
    diff = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = diff.load()
    for i, (a, b) in enumerate(zip(_mask(vanilla), _mask(mine))):
        x, y = i % w, i // w
        if a and b:
            px[x, y] = (70, 70, 78, 255)
        elif a:
            px[x, y] = (232, 96, 96, 255)
        elif b:
            px[x, y] = (96, 176, 232, 255)
    return diff


def contact_strip(vanilla: Image.Image, mine: Image.Image, scale: int = 3,
                  crop: tuple[int, int, int, int] | None = None) -> Image.Image:
    panels = [vanilla, mine, difference_image(vanilla, mine)]
    if crop:
        panels = [p.crop(crop) for p in panels]
    pad = 8
    pw, ph = panels[0].size
    canvas = Image.new("RGBA",
                       (len(panels) * (pw * scale + pad) + pad, ph * scale + 2 * pad),
                       (28, 30, 34, 255))
    for i, panel in enumerate(panels):
        big = panel.resize((pw * scale, ph * scale), Image.Resampling.NEAREST)
        canvas.alpha_composite(big, (pad + i * (pw * scale + pad), pad))
    return canvas


def report(name: str, vanilla: Image.Image, mine: Image.Image) -> str:
    sil = silhouette(vanilla, mine)
    sv, sm = measure(vanilla), measure(mine)
    lv, lm = light_balance(vanilla), light_balance(mine)
    vb, mb = sil["vanilla_box"], sil["mine_box"]

    lines = [f"== {name} vs recreation ==", "",
             f"silhouette IoU        {sil['iou'] * 100:5.1f}%",
             f"  opaque pixels       vanilla {sil['vanilla_pixels']:5d}   "
             f"mine {sil['mine_pixels']:5d}",
             f"  box delta (l,t,r,b) {tuple(m - v for v, m in zip(vb, mb))}",
             "", f"{'':<22}{'vanilla':>8}{'mine':>8}"]
    for key in ("median_value", "value_spread", "median_saturation"):
        lines.append(f"  {key:<20}{sv[key]:8.3f}{sm[key]:8.3f}")
    lines.append(f"  {'median hue (deg)':<20}{median_hue(vanilla):8.1f}"
                 f"{median_hue(mine):8.1f}")
    for key in ("left", "right", "top", "left_over_right"):
        lines.append(f"  {key:<20}{lv[key]:8.3f}{lm[key]:8.3f}")
    lines.append(f"  {'soft edge share':<20}{edge_softness(vanilla):8.3f}"
                 f"{edge_softness(mine):8.3f}")
    return "\n".join(lines)
