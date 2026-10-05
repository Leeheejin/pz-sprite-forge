"""Home Brewing Workshop thumbnail (preview.png, 512x512): the mod's key image.

The description's images already show the mod at work (promo, in-game shots); the
thumbnail is the badge: the title, the two stations as the game draws them and the
drinks they make. Same family as the Food Preservation thumbnail: its warm radial
background (sampled: (75,51,50) at the centre to (22,17,15) in the corners), Georgia
Bold title over a soft shadow, Georgia subtitle, game sprites at whole-number scale,
a column of item icons on the right.

- Stations: the Still and the upright Brewing Barrel from the mod's own texture packs,
  2x sprites at x2 nearest. The barrel's baked contact shadow (faint near-black pixels)
  is dropped; scaled up it reads as a dark tile, so one soft floor shadow is drawn
  under both instead.
- Drinks: beer, wine, makgeolli, whiskey -- what the barrel and the still give. Icons
  come from the vanilla UI packs and are drawn the way the game draws a full one: the
  base icon, then its fluid mask multiplied by the fluid's ColorReference (Wine: Maroon;
  the mod's HBMakgeolli: OldLace; values from zombie.core.Colors). 32 px icons at x2.5.

usage: uv run --python 3.12 --with pillow python examples/hb_thumbnail.py [out.png ...]
       (default docs/hb_thumbnail.png; every path given gets the same image, e.g. the
       Workshop folder's preview.png)
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pzforge.preview import DEFAULT_GAME_MEDIA, SpriteSource  # noqa: E402

MOD_MEDIA = Path(r"C:\Users\leina\Zomboid\Workshop\HomeBrewing\Contents\mods\HomeBrewing\common\media")
FONT_BOLD = Path(r"C:\Windows\Fonts\georgiab.ttf")
FONT_REGULAR = Path(r"C:\Windows\Fonts\georgia.ttf")

SIZE = 512
TITLE, SUBTITLE = "Home Brewing", "Ferment - Distill - Age  (B42)"
CREAM, CREAM_SOFT = (240, 228, 205, 255), (232, 222, 200, 255)
BG_INNER, BG_OUTER = (75, 51, 50), (22, 17, 15)

#: (icon, fluid mask, fluid colour) -- colours are zombie.core.Colors entries
DRINKS = [
    ("Item_BeerBottle", None, None),
    ("Item_Wine", "Item_Wine_Mask", (128, 0, 0)),                    # vanilla Wine: Maroon
    ("Item_GlassBottle", "Item_GlassBottle_Mask", (253, 245, 230)),  # HBMakgeolli: OldLace
    ("Item_WhiskeyFull", None, None),
]

stations = SpriteSource.from_packs([MOD_MEDIA / "texturepacks" / n for n in ("hb_barrel_01.pack", "hb_still_01.pack")])
icons = SpriteSource.from_packs([DEFAULT_GAME_MEDIA / "texturepacks" / n for n in ("UI.pack", "UI2.pack")])


def sprite(name: str) -> Image.Image:
    im = stations.get(name)
    assert im is not None, name
    out = im.copy()
    px = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, a = px[x, y]
            if 0 < a < 64 and r + g + b < 96:      # the baked contact shadow
                px[x, y] = (0, 0, 0, 0)
    return out.crop(out.getbbox())


def icon(base: str, mask: str | None, rgb: tuple[int, int, int] | None) -> Image.Image:
    im = icons.get(base)
    assert im is not None, base
    im = im.copy()
    if mask and rgb:
        m = icons.get(mask)
        assert m is not None, mask
        tint = Image.new("RGBA", m.size)
        mp, tp = m.load(), tint.load()
        for y in range(m.height):
            for x in range(m.width):
                r, g, b, a = mp[x, y]
                if a:
                    tp[x, y] = (r * rgb[0] // 255, g * rgb[1] // 255, b * rgb[2] // 255, a)
        im.alpha_composite(tint)
    return im.crop(im.getbbox())


def scaled(im: Image.Image, k: float) -> Image.Image:
    return im.resize((round(im.width * k), round(im.height * k)), Image.NEAREST)


def background() -> Image.Image:
    bg = Image.new("RGBA", (SIZE, SIZE))
    px = bg.load()
    cx, cy, rmax = SIZE * 0.5, SIZE * 0.55, SIZE * 0.78
    for y in range(SIZE):
        for x in range(SIZE):
            t = min(1.0, math.hypot(x - cx, y - cy) / rmax)
            t = t * t * (3 - 2 * t)
            px[x, y] = tuple(int(BG_INNER[i] + (BG_OUTER[i] - BG_INNER[i]) * t) for i in range(3)) + (255,)
    return bg


def shadowed_text(img, xy, text, font, fill, shadow, off):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((xy[0] + off[0], xy[1] + off[1]), text, font=font, fill=shadow)
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(1.2)))
    ImageDraw.Draw(img).text(xy, text, font=font, fill=fill)


def floor_shadow(img, box, alpha=110):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(box, fill=(0, 0, 0, alpha))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(10)))


def thumbnail() -> Image.Image:
    img = background()
    shadowed_text(img, (26, 18), TITLE, ImageFont.truetype(str(FONT_BOLD), 50), CREAM, (0, 0, 0, 170), (2, 3))
    shadowed_text(img, (30, 80), SUBTITLE, ImageFont.truetype(str(FONT_REGULAR), 21), CREAM_SOFT, (0, 0, 0, 140), (1, 2))

    still = scaled(sprite("hb_still_01_0"), 2)
    barrel = scaled(sprite("hb_barrel_01_0"), 2)
    still_xy = (16, 446 - still.height)                          # behind
    barrel_xy = (still_xy[0] + still.width - 14, 494 - barrel.height)   # in front, just overlapping
    floor_shadow(img, (still_xy[0] - 6, 432, barrel_xy[0] + barrel.width + 10, 506))
    img.alpha_composite(still, still_xy)
    img.alpha_composite(barrel, barrel_xy)

    for n, spec in enumerate(DRINKS):
        ic = scaled(icon(*spec), 2.5)
        cx, cy = 452, 166 + n * 92
        img.alpha_composite(ic, (cx - ic.width // 2, cy - ic.height // 2))
    return img


if __name__ == "__main__":
    outs = [Path(a) for a in sys.argv[1:]] or [ROOT / "docs" / "hb_thumbnail.png"]
    img = thumbnail().convert("RGB")
    for out in outs:
        img.save(out, optimize=True)
        print(f"wrote {out}  ({img.width}x{img.height}, {out.stat().st_size} bytes)")
