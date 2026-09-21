"""Home Brewing promo (1.2.0): the shipped sheets on a vanilla floor.

Three rack columns (3 / 2 / 1 casks) along the back, the still and the vat in
front, composed the way the game draws them (racks: base, tier casks at their
render offsets, decks, front posts). Reads the mod's own texture packs, so
the picture is exactly what ships.

usage: uv run --python 3.12 --with pillow python examples/hb_promo.py [out.png]
"""
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pzforge.preview import DEFAULT_GAME_MEDIA, SpriteSource, compose, CELL_W, CELL_H  # noqa: E402

MOD_MEDIA = Path(r"C:\Users\leina\Zomboid\Workshop\HomeBrewing\Contents\mods\HomeBrewing\common\media")
TIER_OFFSET = {1: 3, 2: 34, 3: 64}   # HB_Brewing.TIER_OFFSET, 1x px

vanilla = SpriteSource.from_packs([
    DEFAULT_GAME_MEDIA / "texturepacks" / "Tiles2x.floor.pack",
    DEFAULT_GAME_MEDIA / "texturepacks" / "Tiles2x.pack",
])
mine = SpriteSource.from_packs([MOD_MEDIA / "texturepacks" / n for n in (
    "hb_rack_01.pack", "hb_rackdeck2_01.pack", "hb_rackdeck3_01.pack", "hb_rackfront_01.pack",
    "hb_barrel_racked_01.pack", "hb_barrel_racked_02.pack", "hb_barrel_01.pack", "hb_still_01.pack")])


def cell(name: str) -> Image.Image:
    im = mine.get(name)
    assert im is not None, name
    return im


def rack_column(casks: int, facing: int = 0) -> Image.Image:
    """One 128x256 cell with the rack and ``casks`` lying casks, in draw order."""
    k = facing
    layers = [(cell(f"hb_rack_01_{k}"), 0)]
    if casks >= 1:
        layers.append((cell(f"hb_barrel_racked_01_{k}"), TIER_OFFSET[1]))
    layers.append((cell(f"hb_rackdeck2_01_{k}"), 0))
    if casks >= 2:
        layers.append((cell(f"hb_barrel_racked_02_{k}"), TIER_OFFSET[2]))
    layers.append((cell(f"hb_rackdeck3_01_{k}"), 0))
    if casks >= 3:
        layers.append((cell(f"hb_barrel_racked_02_{k}"), TIER_OFFSET[3]))
    layers.append((cell(f"hb_rackfront_01_{k}"), 0))
    tall = Image.new("RGBA", (CELL_W, CELL_H + 2 * 64), (0, 0, 0, 0))
    for im, off in layers:
        tall.alpha_composite(im, (0, tall.height - im.height - off * 2))
    return tall.crop((0, tall.height - CELL_H, CELL_W, tall.height))


floor = vanilla.get("floors_interior_tilesandwood_01_6")   # the cork the old promo stood on
assert floor is not None
COLS, ROWS = 7, 6
placements = [(i, j, floor) for i in range(COLS) for j in range(ROWS)]
# racks along the back row (j = 1), the still and the vat two rows in front
placements += [(2, 1, rack_column(3)), (3, 1, rack_column(2)), (4, 1, rack_column(1))]
placements += [(2, 4, cell("hb_still_01_0")), (4, 4, cell("hb_barrel_01_0"))]

scene = compose(placements, COLS, ROWS)
# the old promo's 1152x760 frame, the 7x6 scene (960x672) centred in it at 1:1
frame = Image.new("RGBA", (1152, 760), (26, 28, 32, 255))
frame.alpha_composite(scene, ((1152 - scene.width) // 2, (760 - scene.height) // 2))
out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "hb_promo.png"
frame.save(out)
print(f"wrote {out}  ({frame.width}x{frame.height}), scene {scene.size}")
