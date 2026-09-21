"""HomeBrewing's barrel rack -- one bay column of the cellar row, three tiers.

Read off the reference at 2x and 6x (theindiestone forum shot):

* a plank shelf unit, NOT a crib: continuous shelf boards run along the wall
  across every bay; the bays are divided by one thin upright standing at the
  front edge of the shelves; there is no back rail -- the wall is the back;
* two enclosed bays of barrels (lower shelf, middle shelf) and an open top deck
  a third barrel can lie on: 1 x 1 x 3;
* the barrels lie head-on; the bay upright is drawn OVER the barrel's edge, and
  so is the shelf above a barrel -- the body recedes toward the wall, which in
  this projection climbs the screen, and the next shelf's boards cover it.

So one tile is one bay column: three decks the full tile width (so the boards
of neighbouring bays meet), and a HALF-width upright on each side edge -- two
bays side by side make one full post at the seam, exactly like the reference's
single divider per boundary.

Draw order forces the rack into FOUR sprites per facing, because objects on a
square draw in list order and each barrel must sit between the deck it lies on
and the deck above it:

    base   -- the FAR pair of half-posts + deck 1 (+ its front edge)
    deck2  -- deck 2 + its front edge          (drawn over the tier-1 barrel)
    deck3  -- top deck + its front edge        (drawn over the tier-2 barrel)
    front  -- the NEAR pair of half-posts      (drawn over everything)

Near and far are camera terms, not rack terms: the rack's front pair is the
near pair for the S and E facings, but for N and W the front turns away and
the BACK pair is what stands between the camera and the barrels. Both parts
therefore carry both pairs, gated per facing with F.tag_facings (the
first build put the front pair in the front sprite for every facing, and on N/W a
post at the far corner was drawn over the barrels while the near post
vanished behind them).

The mod keeps each racked barrel right after its deck in the square's list.

Heights, on the measured 43 px/m surface scale (vanilla table: 0.79 m <->
Surface 34): deck 1 top 0.12 m -> Surface 5 (the moveable system lifts a
placed barrel by it); deck 2 top 1.04 m -> render offset 45; top deck 1.96 m
-> 84. Tier pitch 0.92 m clears the lying barrel's 0.75 m diameter plus a
board, with a little air above the head as the reference has.

Run (once per part):
    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" -b \
        -P examples/hb_rack.py -- base      (also: deck2 | deck3 | front)
Build (tiledef ids 2002, 2004, 2005, 2006; sheets hb_rack_01, hb_rackdeck2_01,
hb_rackdeck3_01, hb_rackfront_01):
    uv run --python 3.12 --with pillow python -m pzforge.cli build \
        build/hb_rack_cells --mod-id HomeBrewingRack --preset furniture \
        --tileset-id 1 --tiledef-id 2002 --out dist \
        --prop IsTable= --prop Surface=5 --prop Material=Wood \
        --prop CanScrap= --prop IsMoveAble= --prop PickUpWeight=90 \
        --prop "CustomName=Barrel Rack" --prop "GroupName=Home Brewing" \
        --prop PickUpTool=Hammer --prop PlaceTool=Hammer
    (the three helper sheets build with --preset furniture --prop Material=Wood only)
"""
from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "blender"))
sys.path.insert(0, str(ROOT / "examples"))

import pz_sprite_forge as F  # noqa: E402
import wood_table  # noqa: E402

GRAIN_PATH = ROOT / "build" / "wood_grain.png"

PARTS = {
    "base": ("hb_rack_01", "hb_rack_cells"),
    "deck2": ("hb_rackdeck2_01", "hb_rackdeck2_cells"),
    "deck3": ("hb_rackdeck3_01", "hb_rackdeck3_cells"),
    "front": ("hb_rackfront_01", "hb_rackfront_cells"),
}
PART = next((a for a in sys.argv if a in PARTS), "base")
SHEET, OUT_DIR = PARTS[PART]
OUT = ROOT / "build" / OUT_DIR

#: Deck tops, in metres.
#: Measured on the reference cellar row (2560x1080 screenshot at zoom 2.6, 43 px/m):
#: deck front edges 75 px apart at the corner post = 0.67 m per tier, top deck
#: 170 px above the floor = 1.52 m, bottom deck about 0.15 m up. The first build
#: (0.12 / 1.04 / 1.96, posts 2.0 m) was a head taller with 0.92 m tiers, and a
#: 0.6 m cask filled only two thirds of its bay; the reference cask fills its bay
#: almost to the deck above.
DECK_TOPS = (0.15, 0.82, 1.49)
DECK_THICK = 0.055
DECK_DEPTH = 0.92          # along Y; the open side is -Y
FRONT_EDGE = 0.030         # the deck's front edge strip, kept with its deck
POST_W = 0.040             # a half post: two bays make an 0.08 m divider at the seam
POST_D = 0.085
POST_TOP = 1.55
PLANKS = 3


def build_rack(part: str) -> list[bpy.types.Object]:
    plank_paints = [
        (0.356, 0.193, 0.066), (0.405, 0.223, 0.075), (0.370, 0.200, 0.066),
    ]
    plank_mats = [
        F.forge_material(f"rack_plank_{k}", "wood", p, texture_path=GRAIN_PATH,
                         swing=(0.62, 1.32))
        for k, p in enumerate(plank_paints)
    ]
    edge_mat = F.forge_material("rack_edge", "wood", (0.300, 0.165, 0.058))
    post_mat = F.forge_material("rack_post", "wood", (0.218, 0.119, 0.041))
    seam_mat = F.forge_material("rack_seam", "wood", (0.105, 0.072, 0.042))

    parts: list[bpy.types.Object] = []

    def box(name, centre, size, material):
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=centre)
        obj = bpy.context.active_object
        obj.name = name
        obj.scale = size
        obj.data.materials.append(material)
        parts.append(obj)
        return obj

    y_front = -DECK_DEPTH / 2
    y_back = DECK_DEPTH / 2
    post_x = 0.5 - POST_W / 2

    def deck(t: int) -> None:
        top = DECK_TOPS[t]
        z = top - DECK_THICK / 2
        plank_w = (DECK_DEPTH - FRONT_EDGE) / PLANKS
        y0 = y_front + FRONT_EDGE
        box(f"deck{t}_backing", (0, (y0 + y_back) / 2, z - 0.018),
            (0.98, DECK_DEPTH - FRONT_EDGE - 0.02, DECK_THICK), seam_mat)
        for k in range(PLANKS):
            y = y0 + plank_w * (k + 0.5)
            jitter = (0.002, -0.0015, 0.001)[k]
            box(f"deck{t}_plank_{k}", (0, y, z + jitter),
                (1.0, plank_w - 0.026, DECK_THICK), plank_mats[(k + t) % PLANKS])
        box(f"deck{t}_edge", (0, y_front + FRONT_EDGE / 2, z),
            (1.0, FRONT_EDGE, DECK_THICK), edge_mat)

    def posts(side: str, facings: str) -> None:
        """The two half-posts of one side of the bay, rendered for ``facings`` only."""
        y = y_back - POST_D / 2 if side == "back" else y_front + POST_D / 2
        for sx in (-1, 1):
            F.tag_facings(box(f"post_{side}_{sx}_{facings}",
                              (sx * post_x, y, POST_TOP / 2),
                              (POST_W, POST_D, POST_TOP), post_mat), facings)

    if part == "base":
        deck(0)
        posts("back", "SE")     # far pair when the front faces the camera
        posts("front", "NW")    # far pair when the front turns away
    elif part == "deck2":
        deck(1)
    elif part == "deck3":
        deck(2)
    else:
        posts("front", "SE")    # near pair, drawn over the barrels
        posts("back", "NW")

    return parts


def main() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    wood_table.make_grain()
    F.register()
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    props = scene.pz_forge
    props.sheet_name = SHEET
    props.output_dir = str(OUT)
    props.footprint_x = props.footprint_y = 1
    props.facings = "4"
    props.show_guide = False
    props.contrast_boost = 1.0
    props.toon_shading = True

    F.build_rig(bpy.context)
    scene.cycles.samples = 512
    scene.cycles.use_denoising = True

    subject = bpy.data.objects[F.SUBJECT_NAME]
    for part in build_rack(PART):
        part.parent = subject

    manifest = F.render_cells(bpy.context)
    print(f"rendered {len(manifest['cells'])} cell(s) [{PART}] to {OUT}")


if __name__ == "__main__":
    main()
