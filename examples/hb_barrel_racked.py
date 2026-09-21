"""HomeBrewing's fermentation barrel LYING DOWN -- the racked state.

The reference cellar row shows barrels on their sides, head-on, slotted into a
plank-and-post rack. This is ``hb_barrel.py``'s barrel (same lathed staves,
hoops, bung and spigot, same wood materials) turned onto its side and rested
on the ground plane; the rack tile's ``Surface`` lifts it into the bay
in-game, so nothing here anticipates the rack's height.

The head is the face the player looks at now, and the upright barrel's head
furniture -- a 0.014 m torus rim, a 0.008 m recessed lid, a 0.034 m bung -- is
1-2 px at 1x seen square-on and vanished under the toon ramp. Read off the
reference head (6x crop): a dark iron ring at the very edge, a lighter rim band
inside it, then three or four head boards with drawn seams. So each head gets
that as real geometry: a chime hoop proud of the stave ends, a wide rim ring,
three boards with seam gaps, and a bung big enough to survive the ramp.

Four facings: the rig turns the subject about Z per facing (S, E, N, W), so a
barrel laid along Y comes out with its head toward each side -- the head faces
the open side of whichever way the rack is built.

Run:
    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" -b \
        -P examples/hb_barrel_racked.py
Build (internal tileset 1, global tiledef 2003):
    uv run --python 3.12 --with pillow python -m pzforge.cli build \
        build/hb_barrel_racked_cells --mod-id HomeBrewingBarrelRacked \
        --preset appliance --tileset-id 1 --tiledef-id 2003 --out dist \
        --stroke-amplitude 0.05 --grounding 2.0 --contour-top 0.77 --contour 1.8 \
        --prop IsTableTop= --prop IsMoveAble= --prop CanScrap= --prop Material=Wood \
        --prop "CustomName=Brewing Barrel" --prop "GroupName=Home Brewing"
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "blender"))
sys.path.insert(0, str(ROOT / "examples"))

import pz_sprite_forge as F  # noqa: E402
import hb_barrel  # noqa: E402
import metal_drum as drum  # noqa: E402
import wood_drum  # noqa: E402

OUT = ROOT / "build" / "hb_barrel_racked_cells"

#: The head furniture lives in hb_barrel now (both poses share it).

#: Bay lighting, measured on the reference cellar row (right-row 3x crop,
#: head boards / flank / deck plank luminance 75 / 42 / 73): the head is as
#: bright as the deck it sits on and the flank, tucked under the deck above,
#: is 0.56 of the head. The first build painted the flank BRIGHTER than the
#: head (70 vs 64, deck 78) and read as a body with the lid tucked away, so
#: the racked barrel paints its staves down and its head boards up.
#: Second round (measured on the rebuilt sheet): 0.55 gave flank/head 0.66,
#: still a step too bright, and the iron read as lit steel (top of the ring
#: 60 against the reference's 35), so the staves go to 0.45 and the iron,
#: hoops and chime alike, is painted at 0.6 of the drum's iron.
STAVE_SHADE = 0.45
HEAD_BOARD_LIFT = 1.20
IRON_SHADE = 0.60

#: Fit. The upright barrel (0.88 x 0.75 m) laid in the 0.92 x 1.0 m bay had
#: its heads flush with the deck edges and its sprite across 52 of the tile's
#: 64 px: it read as hanging out of the rack. The reference cask is about
#: 0.6 m across in a 1 m bay, with a strip of deck showing in front of the
#: head -- so the racked cask is the barrel at 0.80 (0.70 x 0.60 m) and lies
#: 0.05 m back from the bay's centre (head 0.16 m behind the front edge, far
#: head 0.06 m inside the back one).
#: Second round: a straighter cask (head 0.95 of the bilge, like the
#: reference's) put a 0.60 m head in the bay but no longer read as THIS
#: mod's barrel, and the 0.05 m set-back read as off-centre. So the racked
#: cask keeps the vat's own belly, sits dead centre in the bay, and takes the
#: largest scale the 0.65 m clear bay allows: 0.86 -> bilge 0.645, head 0.49,
#: length 0.76 m (0.08 m to each deck edge, 0.14 m to each post).
RACK_SCALE = 0.86
SET_BACK = 0.0


def lay_down(parts: list[bpy.types.Object]) -> None:
    """Turn the upright barrel onto its side (axis along Y), shrink it to the
    bay (RACK_SCALE), set it back (SET_BACK) and rest it on z=0.

    Pivoting about mid-height and dropping the bilge radius onto the floor keeps
    the widest stave tangent to the ground, which is where a real barrel touches.
    """
    bpy.context.view_layer.update()
    pivot_up = Matrix.Translation((0.0, 0.0, -hb_barrel.BARREL_H / 2))
    shrink = Matrix.Scale(RACK_SCALE, 4)
    roll = Matrix.Rotation(math.radians(90.0), 4, "X")
    rest = Matrix.Translation((0.0, SET_BACK, hb_barrel.BILGE_R * RACK_SCALE))
    xform = rest @ roll @ shrink @ pivot_up
    for part in parts:
        part.matrix_world = xform @ part.matrix_world


def main() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    wood_drum.make_grain()
    drum.make_texture()
    F.register()
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    props = scene.pz_forge
    props.sheet_name = "hb_barrel_racked_01"
    props.output_dir = str(OUT)
    props.footprint_x = props.footprint_y = 1
    props.facings = "4"
    props.show_guide = False
    props.contrast_boost = 1.0
    props.toon_shading = True

    F.build_rig(bpy.context)
    scene.cycles.samples = 512
    scene.cycles.use_denoising = True

    # The cask's own paints: dimmer iron, lifted head boards (measured on the
    # reference bay; see the constants above), handed to the shared builder so
    # hoops and the top head come out in them; the bottom head is added here.
    mats = wood_drum.wood_drum_materials()
    iron_paint = (0.150, 0.152, 0.146)  # wood_drum "dark" (the drum's iron)
    mats["dark"] = F.forge_material("hbracked_iron", "metal",
                                    tuple(c * IRON_SHADE for c in iron_paint))
    lid_paint = (0.356, 0.193, 0.066)   # wood_drum "lid"
    mats["lid"] = F.forge_material("hbracked_head_board", "wood",
                                   tuple(min(1.0, c * HEAD_BOARD_LIFT) for c in lid_paint))
    parts = hb_barrel.build_barrel(stave_scale=STAVE_SHADE, mats=mats)
    hb_barrel.head_furniture(parts, mats, 0.0, -1.0)

    lay_down(parts)
    # Tile geometry: one block round the whole cask (depth is only sampled
    # where the sprite has pixels, so a box is as good as a cylinder).
    F.tag_geometry(parts, "cask")
    # The spigot points down once the vat lies on its side; it must not push
    # the depth block 7 cm under the deck.
    F.tag_geometry([p for p in parts if "spigot" in p.name], "-")
    subject = bpy.data.objects[F.SUBJECT_NAME]
    for part in parts:
        part.parent = subject

    manifest = F.render_cells(bpy.context)
    print(f"rendered {len(manifest['cells'])} cell(s) to {OUT}")


if __name__ == "__main__":
    main()
