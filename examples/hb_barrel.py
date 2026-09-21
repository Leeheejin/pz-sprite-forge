"""HomeBrewing's wooden fermentation barrel -- the wood drum, put to work.

Composition through the two-stage workflow: the steel drum's geometry in the
wood class (stave shell, iron binding hoops, wooden rim and bung -- exactly
wood_drum's material set), plus the two parts a FERMENTER needs to read as
one: an iron spigot low on the front for drawing off, and the bung riding the
lid as the airlock. Rendered as the single S-facing sprite the mod's
`face SINGLE` entity uses.

Run:
    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" -b \
        -P examples/hb_barrel.py
Build (tileset id 2001 -- next free after the still's 2000):
    uv run --python 3.12 --with pillow python -m pzforge.cli build \
        build/hb_barrel_cells --mod-id HomeBrewingBarrel --preset appliance \
        --tileset-id 2001 --out dist \
        --stroke-amplitude 0.05 --grounding 2.0 --contour-top 0.77 --contour 1.8
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "blender"))
sys.path.insert(0, str(ROOT / "examples"))

import pz_sprite_forge as F  # noqa: E402
import metal_drum as drum  # noqa: E402
import wood_drum  # noqa: E402

OUT = ROOT / "build" / "hb_barrel_cells"


#: Barrel proportions: a BELLY, not a drum -- staves bow out to the bilge at
#: mid-height and narrow to the heads. Two truncated cones give the bulge in
#: vanilla's low-poly form language.
BARREL_H = 0.88
HEAD_R = 0.285
BILGE_R = 0.375


def _r_at(z: float) -> float:
    """Stave radius at height z -- the SAME parabola the lathed body uses.
    (A linear two-cone formula left the hoops buried inside the bow.)"""
    u = 2.0 * z / BARREL_H - 1.0
    return BILGE_R - (BILGE_R - HEAD_R) * (u * u)


def build_barrel(stave_scale: float = 1.0) -> list[bpy.types.Object]:
    """Build the barrel; stave_scale multiplies the stave paint (the racked
    variant lies in the shadow of the deck above it and paints its body darker)."""
    mats = wood_drum.wood_drum_materials()
    # STAVES, not one log: the body carries eight vertical planks, each with
    # its own tone (the table's alternating-plank formula bent around the
    # bow) and a hand-built cylindrical UV so the grain runs down every
    # stave. A single BOX-projected material read as one carved trunk.
    base = (0.405, 0.223, 0.075)
    stave_tones = [1.0, 0.86, 1.08, 0.94, 1.02, 0.90, 1.12, 0.97]
    # Swing narrowed from the wood default (0.40-1.60): at full swing the
    # grain drowned the stave tones and the bow read as mottle. A stave is
    # NEAR-FLAT tone with grain as a whisper; the plank read comes from the
    # tone steps and the dark seam columns between them.
    stave_mats = [F.forge_material(f"hbbarrel_stave_{k}", "wood",
                                   tuple(c * t * stave_scale for c in base),
                                   texture_path=str(wood_drum.GRAIN_PATH),
                                   projection="UV", swing=(0.70, 1.30))
                  for k, t in enumerate(stave_tones)]
    seam_mat = F.forge_material("hbbarrel_seam", "wood", (0.150, 0.095, 0.045))
    spigot_mat = F.forge_material("hbbarrel_spigot", "metal",
                                  (0.150, 0.152, 0.146))
    parts = []

    def smooth():
        try:
            bpy.ops.object.shade_auto_smooth(angle=math.radians(40))
        except (AttributeError, TypeError, RuntimeError):
            bpy.ops.object.shade_smooth()

    def add(name, material, do_smooth=True):
        obj = bpy.context.active_object
        obj.name = name
        if do_smooth:
            smooth()
        obj.data.materials.append(material)
        parts.append(obj)
        return obj

    # The bowed body: ONE lathed mesh with a parabolic stave profile. Two
    # stacked cones put a hard kink at the bilge -- separate meshes cannot
    # share smoothing, and the silhouette broke into an angle instead of a
    # bow. Nine rings keep the curve smooth at sprite scale.
    import bmesh
    seg, rings = 48, 9
    mesh = bpy.data.meshes.new("barrel_body")
    bm = bmesh.new()
    ring_verts = []
    for k in range(rings):
        z = BARREL_H * k / (rings - 1)
        u = 2.0 * z / BARREL_H - 1.0
        r = BILGE_R - (BILGE_R - HEAD_R) * (u * u)
        ring_verts.append([bm.verts.new((r * math.cos(2 * math.pi * i / seg),
                                         r * math.sin(2 * math.pi * i / seg),
                                         z)) for i in range(seg)])
    for a, b in zip(ring_verts, ring_verts[1:]):
        for i in range(seg):
            bm.faces.new((a[i], a[(i + 1) % seg],
                          b[(i + 1) % seg], b[i]))
    bm.faces.new(reversed(ring_verts[0]))
    bm.faces.new(ring_verts[-1])
    # Cylindrical UVs (u around, v up) and one material slot per stave.
    uv_layer = bm.loops.layers.uv.new("UVMap")
    n_staves = len(stave_mats)
    for face in bm.faces:
        us = []
        for loop in face.loops:
            x, y, z = loop.vert.co
            us.append((math.atan2(y, x) / (2 * math.pi)) % 1.0)
        if max(us) - min(us) > 0.5:   # the wrap seam face
            us = [u + 1.0 if u < 0.5 else u for u in us]
        for loop, u in zip(face.loops, us):
            loop[uv_layer].uv = (u, loop.vert.co.z / BARREL_H)
        centre_u = (sum(us) / len(us)) % 1.0
        pos = centre_u * n_staves
        # first sixth of each stave = the drawn seam between planks
        face.material_index = (n_staves if (pos % 1.0) < 1.0 / 6.0
                               else int(pos) % n_staves)
    bm.to_mesh(mesh)
    bm.free()
    body = bpy.data.objects.new("barrel_body", mesh)
    bpy.context.collection.objects.link(body)
    bpy.context.view_layer.objects.active = body
    body.select_set(True)
    bpy.ops.object.shade_auto_smooth(angle=math.radians(40))
    for m in stave_mats:
        body.data.materials.append(m)
    body.data.materials.append(seam_mat)
    parts.append(body)

    # Head: recessed lid inside a stave-end rim, the wood drum's top formula.
    bpy.ops.mesh.primitive_torus_add(major_radius=HEAD_R - 0.012,
                                     minor_radius=0.014, major_segments=48,
                                     minor_segments=8,
                                     location=(0, 0, BARREL_H))
    add("barrel_rim", mats["chime"], do_smooth=False)
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=HEAD_R - 0.022,
                                        depth=0.012,
                                        location=(0, 0, BARREL_H - 0.008))
    add("barrel_lid", mats["lid"], do_smooth=False)
    bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=0.034, depth=0.02,
                                        location=(0.08, -0.10,
                                                  BARREL_H + 0.004))
    add("barrel_bung", mats["bung"], do_smooth=False)

    # Iron hoops near the heads, riding the bow's local radius.
    for i, z in enumerate((0.10, BARREL_H - 0.10)):
        bpy.ops.mesh.primitive_cylinder_add(vertices=48,
                                            radius=_r_at(z) + 0.008,
                                            depth=0.05, location=(0, 0, z),
                                            end_fill_type="NOTHING")
        add(f"hoop_{i}", mats["dark"])

    # Iron spigot on the belly front. Sized for the sprite, not for realism:
    # a to-scale spigot rendered 2 px and vanished.
    ry = _r_at(0.30)
    bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=0.028, depth=0.12,
                                        location=(0.0, -(ry + 0.035), 0.30))
    spigot = add("spigot", spigot_mat, do_smooth=False)
    spigot.rotation_euler = (math.radians(90), 0.0, 0.0)
    bpy.ops.mesh.primitive_cylinder_add(vertices=10, radius=0.016, depth=0.055,
                                        location=(0.0, -(ry + 0.075), 0.255))
    add("spigot_tap", spigot_mat, do_smooth=False)
    return parts


def main() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    wood_drum.make_grain()
    drum.make_texture()
    F.register()
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    props = scene.pz_forge
    props.sheet_name = "hb_barrel_01"
    props.output_dir = str(OUT)
    props.footprint_x = props.footprint_y = 1
    props.facings = "1"
    props.show_guide = False
    props.contrast_boost = 1.0
    props.toon_shading = True

    F.build_rig(bpy.context)
    scene.cycles.samples = 512
    scene.cycles.use_denoising = True

    subject = bpy.data.objects[F.SUBJECT_NAME]
    for part in build_barrel():
        part.parent = subject

    manifest = F.render_cells(bpy.context)
    print(f"rendered {len(manifest['cells'])} cell(s) to {OUT}")


if __name__ == "__main__":
    main()
