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

#: Head furniture, sized to read at 1x when the head faces the camera.
CHIME_PROUD = 0.014      # iron ring standing proud of the stave ends
CHIME_DEPTH = 0.045
RIM_MINOR = 0.024        # the lighter wooden rim band inside the iron ring
BOARDS = 3
BOARD_GAP = 0.016        # drawn seam between head boards
BUNG_R = 0.052

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
RACK_SCALE = 0.80
SET_BACK = 0.05


def head_furniture(parts, mats, z_head: float, outward: float) -> None:
    """Dress one head at height ``z_head``; ``outward`` is +1 for the top head,
    -1 for the bottom, so proud parts stand off the barrel, not into it."""
    r_edge = hb_barrel._r_at(z_head) if 0.0 < z_head < hb_barrel.BARREL_H else hb_barrel.HEAD_R

    def add(name, material, do_smooth=False):
        obj = bpy.context.active_object
        obj.name = name
        obj.data.materials.append(material)
        if do_smooth:
            bpy.ops.object.shade_smooth()
        parts.append(obj)
        return obj

    # Iron chime hoop at the very edge, half proud of the head plane.
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=48, radius=r_edge + CHIME_PROUD, depth=CHIME_DEPTH,
        location=(0, 0, z_head + outward * (CHIME_DEPTH / 2 - 0.012)),
        end_fill_type="NOTHING")
    add("chime_hoop", mats["dark"])

    # Wooden rim band just inside the iron, standing a little proud.
    bpy.ops.mesh.primitive_torus_add(
        major_radius=r_edge - RIM_MINOR - 0.006, minor_radius=RIM_MINOR,
        major_segments=48, minor_segments=8,
        location=(0, 0, z_head + outward * 0.006))
    add("head_rim", mats["chime"])

    # Head boards: three slabs across the head with seam gaps between them.
    inner = r_edge - 2 * RIM_MINOR - 0.004
    span = 2 * inner
    board_w = (span - BOARD_GAP * (BOARDS - 1)) / BOARDS
    board_z = z_head + outward * 0.004
    for k in range(BOARDS):
        x = -inner + board_w / 2 + k * (board_w + BOARD_GAP)
        # chord length of the disc at this x keeps the slab inside the rim
        half_len = math.sqrt(max(inner * inner - x * x, 0.0)) * 0.98
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(x, 0, board_z))
        obj = bpy.context.active_object
        obj.scale = (board_w, half_len * 2, 0.012)
        obj.name = f"head_board_{k}"
        obj.data.materials.append(mats["lid"])
        parts.append(obj)
    # Dark backing behind the boards so the seams read as drawn lines.
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=inner + 0.004, depth=0.010,
                                        location=(0, 0, z_head - outward * 0.004))
    add("head_backing", mats["dark"])

    # Bung, off-centre on the top head only.
    if outward > 0:
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=16, radius=BUNG_R, depth=0.026,
            location=(0.09, -0.11, z_head + 0.012))
        add("head_bung", mats["bung"])


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

    parts = hb_barrel.build_barrel(stave_scale=STAVE_SHADE)
    # The upright recipe's thin head furniture is replaced, not stacked.
    for name in ("barrel_rim", "barrel_lid", "barrel_bung"):
        for part in list(parts):
            if part.name == name:
                parts.remove(part)
                bpy.data.objects.remove(part, do_unlink=True)
    mats = wood_drum.wood_drum_materials()
    iron_paint = (0.150, 0.152, 0.146)  # wood_drum "dark" (the drum's iron)
    mats["dark"] = F.forge_material("hbracked_iron", "metal",
                                    tuple(c * IRON_SHADE for c in iron_paint))
    for part in parts:
        if part.name.startswith("hoop_"):
            part.data.materials[0] = mats["dark"]
    lid_paint = (0.356, 0.193, 0.066)   # wood_drum "lid"
    mats["lid"] = F.forge_material("hbracked_head_board", "wood",
                                   tuple(min(1.0, c * HEAD_BOARD_LIFT) for c in lid_paint))
    head_furniture(parts, mats, hb_barrel.BARREL_H, +1.0)
    head_furniture(parts, mats, 0.0, -1.0)

    lay_down(parts)
    subject = bpy.data.objects[F.SUBJECT_NAME]
    for part in parts:
        part.parent = subject

    manifest = F.render_cells(bpy.context)
    print(f"rendered {len(manifest['cells'])} cell(s) to {OUT}")


if __name__ == "__main__":
    main()
