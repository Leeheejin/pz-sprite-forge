"""Blow up one vanilla sprite and report the measurements needed to rebuild it.

Pixel sizes are converted to metres with the rig's own scale (78.384 px/m at 2x),
so the numbers can be typed straight into a Blender model.

Run with:
    uv run --python 3.12 --with pillow python tools/show_sprite.py <sprite-name> [scale]
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pzforge.packfile import TexturePack

PZ = Path(os.environ.get("PZ_MEDIA", r"C:\Program Files (x86)\Steam\steamapps\common\ProjectZomboid\media")) / "texturepacks"
PX_PER_M = 78.38367176906169   # cos(30) * 128 / sqrt(2)
CELL_W, CELL_H = 128, 256


def find(name: str) -> Image.Image:
    for pack_name in ("Tiles2x.pack", "Tiles2x.floor.pack"):
        pack = TexturePack.read(PZ / pack_name)
        for page in pack.pages:
            for e in page.entries:
                if e.name != name:
                    continue
                atlas = Image.open(io.BytesIO(page.png)).convert("RGBA")
                cell = Image.new("RGBA", (e.ow, e.oh), (0, 0, 0, 0))
                cell.paste(atlas.crop((e.x, e.y, e.x + e.w, e.y + e.h)), (e.ox, e.oy))
                return cell
    raise SystemExit(f"sprite {name!r} not found")


def report(name: str, cell: Image.Image) -> None:
    box = cell.getbbox()
    left, upper, right, lower = box
    w, h = right - left, lower - upper
    # The tile centre sits 32px above the cell bottom; the ground plane there.
    base_above_floor = (CELL_H - lower)
    print(f"{name}: opaque box x {left}..{right - 1} ({w}px), "
          f"y {upper}..{lower - 1} ({h}px) of {cell.width}x{cell.height}")
    print(f"   height above the tile centre : {(CELL_H - 32 - upper) / PX_PER_M:.3f} m "
          f"({CELL_H - 32 - upper}px)")
    print(f"   footprint width across screen: {w / (CELL_W / 2 ** 0.5) / 2 ** 0.5:.3f} "
          f"tiles wide equivalent ({w}px of {CELL_W})")
    print(f"   bottom sits {base_above_floor}px above the cell bottom "
          f"({'on the floor' if base_above_floor <= 2 else 'raised'})")

    # Rows through the object, to read a silhouette profile.
    print("   silhouette profile (row from bottom -> opaque span):")
    px = cell.load()
    for row_from_bottom in range(0, min(h + (CELL_H - lower), CELL_H), 8):
        y = CELL_H - 1 - row_from_bottom
        spans = [x for x in range(cell.width) if px[x, y][3] > 128]
        if spans:
            print(f"      {row_from_bottom:3d}: x {min(spans):3d}..{max(spans):3d} "
                  f"({len(spans)}px wide)")


def main() -> None:
    name = sys.argv[1]
    scale = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    cell = find(name)
    report(name, cell)

    box = cell.getbbox()
    crop = cell.crop(box)
    big = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.NEAREST)
    out = Image.new("RGBA", big.size, (30, 30, 34, 255))
    out.alpha_composite(big)
    path = Path("build") / f"vanilla_{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
