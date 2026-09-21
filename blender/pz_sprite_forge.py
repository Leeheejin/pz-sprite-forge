"""PZ Sprite Forge -- Blender addon for authoring Project Zomboid tile sprites.

Builds a camera/light rig whose projection and lighting are taken from measurements
of the game's own shipped tiles (see ``reference/`` in the repo), then batch-renders
a model into properly aligned tile cells ready to be packed into a ``.pack``.

Everything the rig needs is defined in the CONSTANTS block below so the addon stays
a single self-contained file that Blender's installer can copy anywhere.
"""

from __future__ import annotations

import json
import math
import os

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       StringProperty)
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Euler, Matrix, Vector

bl_info = {
    "name": "PZ Sprite Forge",
    "author": "PZ Sprite Forge",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar (N) > PZ Forge",
    "description": "Render Project Zomboid tile sprites with the game's own projection and lighting",
    "category": "Render",
}

# --------------------------------------------------------------------------- #
# CONSTANTS -- all measured from the shipped game art, not guessed.
# --------------------------------------------------------------------------- #

#: 1 Blender metre == 1 PZ tile. A tile's clear height works out at ~2.45 m,
#: which is why that number reads as a sensible storey height.
TILE = 1.0

#: PZ floor tiles are a 128x64 diamond inside a 128x256 cell (Tiles2x.floor.pack),
#: i.e. a 2:1 dimetric projection -> the camera sits 30 degrees above the horizon.
CAMERA_ELEVATION = math.radians(30.0)
CAMERA_AZIMUTH = math.radians(45.0)

#: Cell size at 1x. PZ ships tiles at 2x (128x256); 1x is the legacy size.
CELL_1X = (64, 128)

#: Vanilla's floor diamond is not centred in its cell. All 325 full-size floor
#: sprites in Tiles2x.floor.pack trim to ox=0, w=126 inside a 128 wide cell, so
#: their centre sits one 2x pixel left of the cell centre.
#:
#: Objects do *not* share that offset. Across 195 vanilla sprites with a
#: mirror-symmetric silhouette the horizontal centre averages 63.76 -- essentially
#: the cell centre at 64.0, not the floor diamond's 63.0. So the shift belongs to
#: floor tiles only; applying it to an object leaves it a pixel left of where
#: vanilla would have drawn it.
#: Expressed as a fraction of cell width, so it holds at 1x and 2x alike.
PIXEL_ALIGN_SHIFT_X = 1.0 / 128.0

#: A tile rendered in empty space has nothing to occlude its ambient light, so it
#: floats: measured against the vanilla drum, the bottom scanline came out 48
#: luminance units too bright and the top rim 30 too bright. A ground plane that is
#: invisible to camera rays but still blocks and bounces light restores the contact
#: darkening that every vanilla sprite has.
GROUND_SIZE = 24.0
GROUND_ALBEDO = (0.26, 0.25, 0.23)

#: Derived from wall sprites tagged Facing=S/E/W/N in newtiledefinitions.tiles:
#: mean sRGB luminance S=119.2, E=96.4, W=76.4, N=63.7. Solving A + K*cos(a - t)
#: for a vertical face puts the key 28.2 degrees east of south under pure lambert
#: shading; tools/calibrate_lighting.py then re-solves against real renders, where
#: bounced light lifts the shaded face, and lands on 26 degrees. These values
#: reproduce S=119.3, E=96.0, N=63.9 on a mid-grey surface -- within 0.5% of vanilla.
LIGHT_AZIMUTH_FROM_SOUTH = math.radians(26.0)
LIGHT_ELEVATION = math.radians(45.0)
DEFAULT_SUN_STRENGTH = 1.32
DEFAULT_AMBIENT_STRENGTH = 0.102

#: Light *colour* is measured too, by tools/analyze_light_colour.py. Within one
#: sprite the albedo is constant, so the chromaticity ratio between its brightest and
#: darkest pixels isolates the lighting: across 387 vanilla sprites the lit side comes
#: out at R=0.968, G=1.028, B=1.031 relative to the shaded side. PZ therefore lights
#: with a slightly *cool* key against *warm* shadow -- the opposite of the cool-sky /
#: warm-sun pairing that looks natural in a render but tints every custom sprite blue.
DEFAULT_KEY_COLOR = (0.99, 1.01, 1.00)
DEFAULT_AMBIENT_COLOR = (1.03, 0.99, 0.98)

#: Per-unit linear response of a mid-grey surface, from tools/calibrate_lighting.py.
#: Used to trade key against ambient while holding the lit face at the same level.
KEY_PER_UNIT_S = 0.1016
AMBIENT_PER_UNIT_VERTICAL = 0.49897


def contrast_settings(boost: float) -> tuple[float, float]:
    """Key and ambient strengths for a given directional-contrast boost.

    Vanilla sprites are not uniformly lit relative to each other: across ~480 of them
    the left-to-right luminance ratio has a median of 1.00 and a 90th percentile of
    1.14, but individual sprites are painted well past that -- the metal drum reaches
    1.42. Raising the key while dropping the ambient by the amount that keeps the lit
    face at the same brightness walks toward those, without touching the default.

    ``1.0`` is the calibrated setting that reproduces vanilla's *average*. The return
    clamps ambient at zero, which is the hardest contrast this azimuth can reach.
    """
    lit = DEFAULT_SUN_STRENGTH * KEY_PER_UNIT_S \
        + DEFAULT_AMBIENT_STRENGTH * AMBIENT_PER_UNIT_VERTICAL
    sun = DEFAULT_SUN_STRENGTH * max(0.0, boost)
    ambient = (lit - sun * KEY_PER_UNIT_S) / AMBIENT_PER_UNIT_VERTICAL
    return (sun, ambient) if ambient >= 0 else (lit / KEY_PER_UNIT_S, 0.0)

#: Vanilla tiles sit around a median value of 0.50 and median saturation 0.28,
#: with a dominant hue near 30 degrees (weathered wood / brick).
VANILLA_TARGETS = {"S": 119.18, "E": 96.37, "W": 76.35, "N": 63.71}

RIG_COLLECTION = "PZ_Forge_Rig"
SUBJECT_NAME = "PZ_Subject"
CAMERA_NAME = "PZ_Camera"
SUN_NAME = "PZ_KeyLight"
GUIDE_NAME = "PZ_TileGuide"
GROUND_NAME = "PZ_Ground"


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #

def cell_size(scale_2x: bool) -> tuple[int, int]:
    w, h = CELL_1X
    return (w * 2, h * 2) if scale_2x else (w, h)


def ortho_scale() -> float:
    """Horizontal world extent covered by one cell: the diagonal of a unit tile."""
    return math.sqrt(2.0) * TILE


def aim_height(cell_w: int, cell_h: int) -> float:
    """How far above the tile centre to aim, so the floor diamond sits flush
    with the bottom of the cell.

    The diamond is ``cell_w`` wide and ``cell_w / 2`` tall, so its centre needs to
    land ``cell_w / 4`` pixels above the bottom edge.
    """
    view_h = ortho_scale() * cell_h / cell_w
    drop_fraction = 0.5 - (cell_w / 4.0) / cell_h
    return drop_fraction * view_h / math.cos(CAMERA_ELEVATION)


def pixels_per_metre(cell_w: int, cell_h: int) -> float:
    """Screen pixels covered by one metre of vertical (world Z) height."""
    view_h = ortho_scale() * cell_h / cell_w
    return math.cos(CAMERA_ELEVATION) * cell_h / view_h


def clear_height(cell_w: int, cell_h: int) -> float:
    """Height that stays inside the cell no matter where on the tile it stands.

    The back corner of the diamond is the worst case: it is already ``cell_w / 4``
    pixels higher up the cell than the tile centre.
    """
    head_room_px = cell_h - cell_w / 2.0
    return head_room_px / pixels_per_metre(cell_w, cell_h)


def camera_direction() -> Vector:
    """Unit vector pointing from the aim point back toward the camera."""
    horiz = math.cos(CAMERA_ELEVATION)
    return Vector((horiz * math.sin(CAMERA_AZIMUTH),
                   -horiz * math.cos(CAMERA_AZIMUTH),
                   math.sin(CAMERA_ELEVATION)))


def sun_rotation() -> Euler:
    """Euler for a sun lamp matching the measured key-light direction.

    +Y is north, so 'south' is -Y and the key swings from there toward +X (east).
    A sun lamp emits along its local -Z, so this rotation has to aim the *travel*
    direction north-west -- putting the source itself to the south-east, which is
    what lights the S and E faces the game actually draws.
    """
    return Euler((math.pi / 2 - LIGHT_ELEVATION, 0.0, LIGHT_AZIMUTH_FROM_SOUTH), "XYZ")


def sun_travel_direction() -> Vector:
    """Unit vector the key light's photons travel along."""
    rot = sun_rotation()
    tilt, azimuth = rot[0], rot[2]
    return Vector((-math.sin(azimuth) * math.sin(tilt),
                   math.cos(azimuth) * math.sin(tilt),
                   -math.cos(tilt)))


# --------------------------------------------------------------------------- #
# Properties
# --------------------------------------------------------------------------- #

class PZForgeProps(PropertyGroup):
    scale_2x: BoolProperty(
        name="2x resolution",
        description="Render 128x256 cells (what B41+ ships). Off renders legacy 64x128",
        default=True,
    )
    footprint_x: IntProperty(
        name="Tiles X", default=1, min=1, max=16,
        description="Footprint width in tiles, along +X (screen right-ish)",
    )
    footprint_y: IntProperty(
        name="Tiles Y", default=1, min=1, max=16,
        description="Footprint depth in tiles, along +Y (screen up-ish)",
    )
    facings: EnumProperty(
        name="Facings",
        items=[("1", "Single", "One orientation only"),
               ("2", "Two (S, E)", "The two orientations vanilla walls use"),
               ("4", "Four (S, E, N, W)", "Full set of orientations")],
        default="1",
    )
    isolate_tiles: BoolProperty(
        name="Isolate tiles", default=False,
        description="Render each footprint tile with only its own parts "
                    "visible. For sets of INDEPENDENT per-tile pieces (wall "
                    "sets): edge-hugging walls overlap on screen, so a "
                    "southern neighbour would otherwise occlude -- and "
                    "amputate -- the piece behind it. Leave off for true "
                    "multi-tile objects whose parts span tiles",
    )
    sheet_name: StringProperty(
        name="Sheet name", default="mytiles_01",
        description="Tilesheet name; sprites become <name>_<index>",
    )
    output_dir: StringProperty(
        name="Output", subtype="DIR_PATH", default="//pz_cells",
        description="Directory to write rendered cells and the manifest into",
    )
    sun_strength: FloatProperty(name="Key", default=DEFAULT_SUN_STRENGTH, min=0.0, max=20.0)
    ambient_strength: FloatProperty(name="Ambient", default=DEFAULT_AMBIENT_STRENGTH,
                                    min=0.0, max=20.0)
    contrast_boost: FloatProperty(
        name="Directional contrast", default=1.0, min=0.2, max=3.0,
        description=("Trade ambient for key while holding the lit face steady. 1.0 is "
                     "vanilla's average; higher walks toward the hand-painted sprites "
                     "that carry more side-to-side contrast than the lighting gives"),
    )
    soft_edge: FloatProperty(
        name="Silhouette feather", default=0.9, min=0.1, max=3.0,
        description=("Render pixel-filter width, the ONLY legal place to set silhouette "
                     "softness (the style pass must leave alpha untouched). 0.9 matches "
                     "hard-surface vanilla, whose soft-edge share is 0.037; foliage is "
                     "drawn far softer -- vegetation_farming_01b_70 measures 0.677 -- so "
                     "plant recipes raise it"),
    )
    show_guide: BoolProperty(name="Tile guide", default=True)
    reference_image: StringProperty(
        name="Reference", subtype="FILE_PATH", default="",
        description="Extracted vanilla cell PNG to overlay in the camera view",
    )
    reference_opacity: FloatProperty(name="Overlay opacity", default=0.5,
                                     min=0.05, max=1.0)
    alignment: EnumProperty(
        name="Align like",
        items=[("OBJECT", "Object", "Centre on the cell, as vanilla object sprites do"),
               ("FLOOR", "Floor tile",
                "Shift one pixel left, as every vanilla floor sprite is drawn")],
        default="OBJECT",
    )
    ground_occlusion: BoolProperty(
        name="Ground occlusion", default=True,
        description=("Put an invisible floor under the subject so it picks up the "
                     "contact darkening every vanilla sprite has. Without it a tile "
                     "renders as though floating in empty space"),
    )
    toon_shading: BoolProperty(
        name="Toon ramp", default=False,
        description=("Render in EEVEE with the measured light ramp: faces snap to "
                     "the vanilla face levels instead of shading smoothly, the way "
                     "vanilla paints light. Use toon_material() for every part"),
    )


# --------------------------------------------------------------------------- #
# Rig construction
# --------------------------------------------------------------------------- #

def rig_collection(context) -> bpy.types.Collection:
    coll = bpy.data.collections.get(RIG_COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(RIG_COLLECTION)
        context.scene.collection.children.link(coll)
    return coll


def _relink(obj, coll) -> None:
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    coll.objects.link(obj)


def build_guide(coll, props) -> bpy.types.Object:
    """Wireframe box showing one tile's footprint and its guaranteed clear height."""
    cw, ch = cell_size(props.scale_2x)
    height = clear_height(cw, ch)
    nx, ny = props.footprint_x, props.footprint_y

    mesh = bpy.data.meshes.new(GUIDE_NAME)
    verts, edges = [], []
    for i in range(nx):
        for j in range(ny):
            base = len(verts)
            x0, y0 = i - 0.5, j - 0.5
            x1, y1 = i + 0.5, j + 0.5
            verts += [(x0, y0, 0), (x1, y0, 0), (x1, y1, 0), (x0, y1, 0),
                      (x0, y0, height), (x1, y0, height),
                      (x1, y1, height), (x0, y1, height)]
            edges += [(base + a, base + b) for a, b in
                      ((0, 1), (1, 2), (2, 3), (3, 0),
                       (4, 5), (5, 6), (6, 7), (7, 4),
                       (0, 4), (1, 5), (2, 6), (3, 7))]
    mesh.from_pydata(verts, edges, [])
    mesh.update()

    old = bpy.data.objects.get(GUIDE_NAME)
    if old:
        bpy.data.objects.remove(old, do_unlink=True)
    guide = bpy.data.objects.new(GUIDE_NAME, mesh)
    guide.display_type = "WIRE"
    guide.hide_render = True
    guide.hide_select = True
    _relink(guide, coll)
    return guide


def build_rig(context) -> None:
    props = context.scene.pz_forge
    coll = rig_collection(context)
    cw, ch = cell_size(props.scale_2x)

    # --- camera -----------------------------------------------------------
    cam = bpy.data.objects.get(CAMERA_NAME)
    if cam is None or cam.type != "CAMERA":
        cam = bpy.data.objects.new(CAMERA_NAME, bpy.data.cameras.new(CAMERA_NAME))
    _relink(cam, coll)
    cam.data.type = "ORTHO"
    cam.data.sensor_fit = "HORIZONTAL"
    cam.data.ortho_scale = ortho_scale()
    cam.data.clip_start = 0.01
    cam.data.clip_end = 500.0
    cam.data.shift_x = PIXEL_ALIGN_SHIFT_X if props.alignment == "FLOOR" else 0.0
    cam.data.shift_y = 0.0
    aim = Vector((0.0, 0.0, aim_height(cw, ch)))
    cam.location = aim + camera_direction() * 100.0
    cam.rotation_euler = Euler(
        (math.pi / 2 - CAMERA_ELEVATION, 0.0, CAMERA_AZIMUTH), "XYZ")

    # --- key light --------------------------------------------------------
    sun = bpy.data.objects.get(SUN_NAME)
    if sun is None or sun.type != "LIGHT":
        sun = bpy.data.objects.new(SUN_NAME, bpy.data.lights.new(SUN_NAME, "SUN"))
    _relink(sun, coll)
    sun.data.type = "SUN"
    sun_strength, ambient_strength = contrast_settings(props.contrast_boost)
    sun.data.energy = sun_strength
    sun.data.angle = math.radians(12.0)  # soft edges; vanilla shadows are not crisp
    sun.data.color = DEFAULT_KEY_COLOR
    sun.location = (0.0, 0.0, 10.0)
    sun.rotation_euler = sun_rotation()

    # --- ground occluder --------------------------------------------------
    ground = bpy.data.objects.get(GROUND_NAME)
    if props.ground_occlusion:
        if ground is None or ground.type != "MESH":
            mesh = bpy.data.meshes.new(GROUND_NAME)
            half = GROUND_SIZE / 2
            mesh.from_pydata([(-half, -half, 0.0), (half, -half, 0.0),
                              (half, half, 0.0), (-half, half, 0.0)],
                             [], [(0, 1, 2, 3)])
            mesh.update()
            ground = bpy.data.objects.new(GROUND_NAME, mesh)
        _relink(ground, coll)
        material = bpy.data.materials.get("PZ_GroundMat") or \
            bpy.data.materials.new("PZ_GroundMat")
        material.use_nodes = True
        bsdf = material.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (*GROUND_ALBEDO, 1.0)
            bsdf.inputs["Roughness"].default_value = 1.0
            if "Specular IOR Level" in bsdf.inputs:
                bsdf.inputs["Specular IOR Level"].default_value = 0.0
        ground.data.materials.clear()
        ground.data.materials.append(material)
        # Seen by shadow and bounce rays, never by the camera, so it occludes and
        # bounces without ever appearing in the sprite or breaking the alpha.
        for attr in ("visible_camera", "visible_shadow"):
            if hasattr(ground, attr):
                setattr(ground, attr, attr != "visible_camera")
        ground.hide_select = True
    elif ground is not None:
        bpy.data.objects.remove(ground, do_unlink=True)

    # --- subject anchor ---------------------------------------------------
    subject = bpy.data.objects.get(SUBJECT_NAME)
    if subject is None:
        subject = bpy.data.objects.new(SUBJECT_NAME, None)
        subject.empty_display_type = "PLAIN_AXES"
        subject.empty_display_size = 0.35
    _relink(subject, coll)
    subject.location = (0.0, 0.0, 0.0)
    subject.rotation_euler = Euler((0.0, 0.0, 0.0), "XYZ")

    if props.show_guide:
        build_guide(coll, props)
    elif bpy.data.objects.get(GUIDE_NAME):
        bpy.data.objects.remove(bpy.data.objects[GUIDE_NAME], do_unlink=True)

    apply_render_settings(context)
    context.scene.camera = cam


def apply_render_settings(context) -> None:
    """Render settings that make output match vanilla tiles rather than look 'nice'."""
    scene = context.scene
    props = scene.pz_forge
    cw, ch = cell_size(props.scale_2x)

    if props.toon_shading:
        use_eevee(scene)
    scene.render.resolution_x = cw
    scene.render.resolution_y = ch
    scene.render.resolution_percentage = 100
    # Narrower pixel filter than Blender's 1.5 default: the styled silhouettes
    # measured a soft-edge share of 0.083 against vanilla's 0.037 -- twice the
    # feather -- and alpha cannot be tightened after the fact (the style pass
    # must preserve it exactly; trimmed sprite offsets depend on it). So the
    # feather is narrowed at the only legal place: the render filter.
    scene.render.filter_size = props.soft_edge
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.render.image_settings.compression = 90

    # Standard, never AgX/Filmic: the game's art is plain sRGB and any filmic
    # tonemap immediately reads as "not a Zomboid tile".
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.display_settings.display_device = "sRGB"

    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("PZ_World")
        scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (*DEFAULT_AMBIENT_COLOR, 1.0)
        bg.inputs[1].default_value = contrast_settings(props.contrast_boost)[1]

    engine = scene.render.engine
    if "EEVEE" in engine:
        ev = getattr(scene, "eevee", None)
        if ev is not None:
            for attr, value in (("taa_render_samples", 64),
                                ("use_gtao", True),
                                ("use_raytracing", True),
                                ("use_shadows", True)):
                if hasattr(ev, attr):
                    setattr(ev, attr, value)
    elif engine == "CYCLES":
        scene.cycles.samples = 128
        scene.cycles.use_denoising = True


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

FACING_ORDER = ["S", "E", "N", "W"]


def _normal_pass_material():
    """World-space normals as emission, for the style pass's orientation mask.

    The style pass runs on flat PNGs and cannot tell a lid from a flank, but some
    of its treatments are orientation-bound -- brush strokes are metal wear and run
    down gravity, so they belong on vertical surfaces only. Emission is noise-free,
    so the pass renders in a handful of samples with no denoising.
    """
    mat = bpy.data.materials.get("PZ_NormalPass")
    if mat is None:
        mat = bpy.data.materials.new("PZ_NormalPass")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    geo = nodes.new("ShaderNodeNewGeometry")
    remap = nodes.new("ShaderNodeVectorMath")
    remap.operation = "MULTIPLY_ADD"
    remap.inputs[1].default_value = (0.5, 0.5, 0.5)
    remap.inputs[2].default_value = (0.5, 0.5, 0.5)
    emission = nodes.new("ShaderNodeEmission")
    out = nodes.new("ShaderNodeOutputMaterial")
    links.new(geo.outputs["Normal"], remap.inputs[0])
    links.new(remap.outputs["Vector"], emission.inputs["Color"])
    links.new(emission.outputs["Emission"], out.inputs["Surface"])
    return mat


# --------------------------------------------------------------------------- #
# Toon ramp shading
# --------------------------------------------------------------------------- #

#: Ramp output levels in linear light, derived from the measured face luminances
#: (S 119.2, E 96.4, W 76.4, N 63.7 in sRGB, decoded to linear and normalised so a
#: 0.438-albedo south face renders at 112 -- the same anchor the Cycles rig was
#: calibrated to). Top is the measured crate-top/S ratio. The ramp *is* the
#: contrast model now: faces do not shade smoothly, they snap to these levels,
#: which is how vanilla paints light.
TOON_LIGHT_S = 0.370
TOON_LEVELS = {  # relative to S, in linear light
    "shadow": 0.230, "N": 0.267, "W": 0.389, "E": 0.636, "S": 1.0, "top": 1.43,
}
#: Measured lit/shaded chromaticity ratio (R, G, B): the key is cool, the shadow
#: warm. Split symmetrically so the overall balance stays neutral.
TOON_LIT_TINT = (0.984, 1.014, 1.016)
TOON_SHADE_TINT = (1.016, 0.986, 0.984)

#: Contact-shadow (ambient occlusion) term multiplied onto the ramp output.
#: The key light stands almost behind the camera's view axis, so its cast
#: shadows fall AWAY from the camera and barely reach the sprite -- vanilla's
#: corner pockets (a couch seat beside its arm, the foot of a wall of crates)
#: are occlusion shading, not key shadows. Measured on the vanilla couch: the
#: seat beside the arm renders at 0.81x of the open seat, as a soft gradient.
#: Strength calibrated so a tight corner lands there; open faces read AO~1
#: and keep their calibrated levels.
AO_STRENGTH = 0.5
AO_DISTANCE = 0.7

#: Ramp input positions: captured Shader-to-RGB luminance separating the faces,
#: measured with tools/calibrate_toon.py under the default rig (EEVEE): ambient
#: alone reads 0.101 on every orientation, E 0.232, S 0.369, top 0.399. Each stop
#: sits midway between neighbours. N and W share the ambient-only input -- the
#: key cannot tell them apart at this azimuth -- so N's band is vestigial and
#: unlit faces land on the W level; cast shadows on lit faces do the same.
TOON_STOPS = [0.085, 0.095, 0.167, 0.300, 0.384]  # N | W | E | S | top


def toon_ramp_group(soft: bool = False, levels: dict | None = None,
                    tints: dict | None = None, ao: tuple | None = None):
    """The shared light ramp: capture shading, snap it to the measured levels.

    White diffuse -> Shader to RGB -> luminance -> constant ColorRamp whose stop
    colours are the measured vanilla light levels with the measured cool-lit /
    warm-shadow tints. Every toon material multiplies its paint by this group's
    output, so the whole sprite shares one light grammar.

    ``levels`` overrides individual TOON_LEVELS entries for material families
    vanilla shades to different depths (measured: wall sets run their W face at
    0.88 of the N face where furniture runs E/S at ~0.81).
    """
    merged = dict(TOON_LEVELS)
    if levels:
        merged.update(levels)
    lit_tint = (tints or {}).get("lit", TOON_LIT_TINT)
    shade_tint = (tints or {}).get("shade", TOON_SHADE_TINT)
    suffix = ("_" + "_".join(f"{k}{merged[k]:.3f}" for k in sorted(levels))
              if levels else "")
    if tints:
        suffix += "_t" + "_".join(f"{c:.3f}" for c in (*lit_tint, *shade_tint))
    if ao is not None:
        suffix += f"_ao{ao[0]:.2f}x{ao[1]:.2f}"
    name = ("PZ_ToonRampSoft" if soft else "PZ_ToonRamp") + suffix
    group = bpy.data.node_groups.get(name)
    if group is not None:
        return group
    group = bpy.data.node_groups.new(name, "ShaderNodeTree")
    group.interface.new_socket("Light", in_out="OUTPUT", socket_type="NodeSocketColor")
    nodes, links = group.nodes, group.links

    diffuse = nodes.new("ShaderNodeBsdfDiffuse")
    diffuse.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    diffuse.inputs["Roughness"].default_value = 0.5
    capture = nodes.new("ShaderNodeShaderToRGB")
    luminance = nodes.new("ShaderNodeRGBToBW")
    ramp = nodes.new("ShaderNodeValToRGB")
    # Linear with paired stops, not constant: each level keeps a flat plateau but
    # neighbouring levels blend across a narrow window at the boundary. Razor
    # steps turned a smooth cylinder into what read as an octagonal prism -- the
    # band edges became facet edges. Vanilla's painted steps have soft borders.
    ramp.color_ramp.interpolation = "LINEAR"

    levels = ["shadow", "N", "W", "E", "S", "top"]
    boundaries = TOON_STOPS

    def colour(level):
        value = TOON_LIGHT_S * merged[level]
        tint = lit_tint if merged[level] >= merged["E"] else shade_tint
        return (value * tint[0], value * tint[1], value * tint[2], 1.0)

    if soft:
        # Fabric variant: the same measured levels at band centres with plain
        # linear interpolation -- cloth shades in gradients. Chroma follows the
        # MEASURED lit/shaded sweep (~3%, reference/light_colour.json) and
        # nothing more: an invented 26% crimson sweep here made every face a
        # different colour, which read as parts painted from different pots.
        def colour_t(level):
            base = colour(level)
            # colour() already applies the measured lit/shade tints.
            return base
        centres = []
        prev = 0.0
        for k, boundary in enumerate(boundaries):
            centres.append(((prev + boundary) / 2, colour_t(levels[k])))
            prev = boundary
        centres.append((min(1.0, prev + 0.08), colour_t(levels[-1])))
        stops = centres
    else:
        stops = [(0.0, colour(levels[0]))]
        for k, boundary in enumerate(boundaries):
            prev_gap = boundary - (boundaries[k - 1] if k else 0.0)
            next_gap = (boundaries[k + 1] if k + 1 < len(boundaries) else 1.0) - boundary
            blend = min(0.02, 0.3 * min(prev_gap, next_gap))
            stops.append((boundary - blend, colour(levels[k])))
            stops.append((boundary + blend, colour(levels[k + 1])))

    while len(ramp.color_ramp.elements) < len(stops):
        ramp.color_ramp.elements.new(0.5)
    for element, (pos, col) in zip(ramp.color_ramp.elements, stops):
        element.position = pos
        element.color = col

    # Contact shading: the ramp output darkens toward tight corners by the
    # measured pocket factor. Applied AFTER quantisation so the pocket is the
    # soft gradient vanilla paints, not a level jump.
    ao_dist, ao_str = ao if ao is not None else (AO_DISTANCE, AO_STRENGTH)
    ao = nodes.new("ShaderNodeAmbientOcclusion")
    ao.inputs["Distance"].default_value = ao_dist
    ao_scale = nodes.new("ShaderNodeMath")
    ao_scale.operation = "MULTIPLY_ADD"
    ao_scale.inputs[1].default_value = ao_str
    ao_scale.inputs[2].default_value = 1.0 - ao_str
    pocket = nodes.new("ShaderNodeMix")
    pocket.data_type = "RGBA"
    pocket.blend_type = "MULTIPLY"
    pocket.inputs["Factor"].default_value = 1.0

    out = nodes.new("NodeGroupOutput")
    links.new(diffuse.outputs["BSDF"], capture.inputs["Shader"])
    links.new(capture.outputs["Color"], luminance.inputs["Color"])
    links.new(luminance.outputs["Val"], ramp.inputs["Fac"])
    links.new(ao.outputs["AO"], ao_scale.inputs[0])
    links.new(ramp.outputs["Color"], pocket.inputs["A"])
    links.new(ao_scale.outputs["Value"], pocket.inputs["B"])
    links.new(pocket.outputs["Result"], out.inputs["Light"])
    return group


#: Material-class defaults, anchored to the measured signatures in
#: reference/material_signatures.json. Fabric is the smoothest class (median
#: local gradient 0.0078 vs wood's 0.0157) so it shades in gradients; metal and
#: wood keep the stepped ramp. Texture grammars live in
#: ``pzforge.texture.material_spec``.
MATERIAL_DEFAULTS = {
    "metal": {"shading": "step"},
    "wood": {"shading": "step"},
    "fabric": {"shading": "soft"},
}


def _hue_steel(p):
    """Measured steel correction: the styled drum rendered at hue 45 against
    vanilla's 21.8; narrowing the green-blue span pulls near-neutral steel
    toward vanilla's warm grey. Strength 0.55 measured on the drum; the crate
    needed only 0.94 (1.3 deg off), so recipes may pass their own."""
    r, g, b = p
    return (r, b + 0.55 * (g - b), b)


def _hue_wood(p):
    """Measured wood correction: renders came out 3.4 deg yellower and 0.04
    less saturated than vanilla, so blue drops and the green span narrows."""
    r, g, b = p
    b2 = 0.88 * b
    return (r, b2 + 0.87 * (g - b), b2)


def _hue_foliage(p):
    """Foliage hue correction. Set from a render-vs-reference measurement in the
    same way as steel and wood: the first crop render is compared with
    vegetation_farming_01b_70 on green pixels only and the per-channel transfer
    ratio goes here. Identity until that measurement exists."""
    return p


#: Stage 1 of the two-stage forge workflow: the measured grammar of each
#: MATERIAL CLASS, independent of any object built from it. An object recipe
#: (stage 2) decides shape and per-part paint; the class supplies everything
#: that makes the paint read as that material -- hue correction, dark-to-light
#: swing, ramp mode with per-family level/tint overrides, texture ramp range,
#: projection and scale, and which pzforge.texture grammar draws its map.
#: All numbers are measured against vanilla references (drum/crate = metal,
#: table = wood, couch = fabric, exterior wall = brick).
MATERIAL_CLASSES = {
    "metal": {
        "shading": "step", "hue": _hue_steel, "swing": (0.55, 1.45),
        "ramp_range": (0.08, 0.92), "projection": "UV", "texture_scale": 1.0,
        "texture": "metal", "accent_position": 0.18,
    },
    "wood": {
        "shading": "step", "hue": _hue_wood, "swing": (0.40, 1.60),
        "ramp_range": (0.22, 0.78), "projection": "BOX", "texture_scale": 1.1,
        "texture": "wood",
    },
    "fabric": {
        # Fabric shades in gradients and keeps a narrow same-hue swing --
        # a wide or hue-rotating swing reads as varnished wood.
        "shading": "soft", "hue": None, "swing": (0.845, 1.0),
        "ramp_range": (0.02, 0.98), "projection": "BOX", "texture_scale": 2.0,
        "texture": "fabric",
    },
    "brick": {
        # Walls shade shallower than furniture (W/N 0.88 vs E/S 0.81) and
        # their shadows stay cool; the swing spans brick body to mortar.
        "shading": {"mode": "step", "levels": {"E": 0.756},
                    "tints": {"shade": TOON_LIT_TINT}},
        "hue": None, "swing": (0.797, 1.0), "ramp_range": (0.08, 0.92),
        "projection": "BOX", "texture_scale": 1.13, "texture": "brick",
    },
    "foliage": {
        # Measured on vanilla farming crops at fullGrown (green pixels of BellPepper,
        # Tomato, Cabbages, Corn, SweetPotato, Greenpeas; reference/material_signatures
        # .json "foliage"): p10-p90 spread 0.25, median saturation 0.375, local
        # gradient 0.020 (crisper than wood's 0.016), 8-10 tones per 12 px window
        # (a canopy is many small elements, against 4 for one object), and a FLAT
        # saturation-vs-value curve (0.42/0.38/0.36 across the value tiers on
        # BellPepper) -- foliage keeps its saturation in shadow like wood, it does
        # not desaturate like dyed cloth. Leaves are flat-painted faces (a 3x3
        # window inside a leaf varies by <0.05 on 31% of BellPepper's interior),
        # so the ramp steps. The swing is the within-canopy p10-p90 of 0.25 around
        # the median, hue left uncorrected until a render has been measured against
        # the reference (see _hue_foliage).
        "shading": "step", "hue": _hue_foliage, "swing": (0.74, 1.26),
        "ramp_range": (0.20, 0.80), "projection": "WORLD", "texture_scale": 2.2,
        "texture": "foliage",
        # painter's terms: every blade split light/dark across its own midrib (the
        # reference's leaves read as two halves whichever way they face); plant-
        # scale occlusion so leaves darken each other and stems pocket into the bed
        # (the couch-corner AO at 0.7 m sees nothing 1 cm wide)
        "split": (1.16, 0.80), "ao": (0.08, 0.85),
    },
    "soil": {
        # The tilled bed, measured on the same sprites plus the bare beds
        # vegetation_farming_01b_64 / _01b_0 ("soil" in material_signatures.json):
        # spread 0.29, saturation 0.77-0.88 (the most saturated class), local
        # gradient 0.035 -- the highest of any class, a per-pixel stipple -- and
        # 6-8 tones per window. Saturation FALLS into the lit tier on the two-ridge
        # beds (0.77 -> 0.55 on BellPepper's bare bed, 0.85 -> 0.34 on Corn's) so the
        # light stop is pulled toward neutral; the dark stop stays saturated.
        # Painted as a flat bank with a hard cross-ridge tone ramp (see the crop
        # recipe), so the class supplies stipple and paint, not relief.
        "shading": "step", "hue": None, "swing": (0.52, 1.48),
        "ramp_range": (0.30, 0.70), "projection": "WORLD", "texture_scale": 2.2,
        "texture": "soil",
    },
    "fruit": {
        # A crop's fruit: flat-painted, posterised chips. Measured on BellPepper /
        # Tomato / Strawberry fruit blobs: centre-to-rim luminance +0.00..+0.03 (no
        # radial falloff at all), so the ramp steps and there is no texture; the
        # paint is the spec inversion of the fruit's brightest common shade
        # (Tomato: (1.000, 0.175, 0.107) on S).
        "shading": "step", "hue": None, "swing": (0.80, 1.20),
        "ramp_range": (0.08, 0.92), "projection": "BOX", "texture_scale": 1.0,
        "texture": None,
        # the drawn outline round every fruit and its highlight dot
        "rim": (0.30, 0.64), "gloss": (0.22, 0.30), "ao": (0.08, 0.85),
    },
}


def forge_material(name: str, material: str, paint=None, *, texture_path=None,
                   dark=None, light=None, accent=None, accent_position=None,
                   swing=None, hue=None, shading=None, ramp_range=None,
                   projection=None, texture_scale=None, split=None, rim=None,
                   gloss=None, ao=None):
    """Express a MATERIAL CLASS on one part -- stage 1 of the forge workflow.

    With only ``paint``, the part gets a flat class-corrected paint. With
    ``texture_path``, the class's swing spreads the paint into the dark/light
    texture stops (or take explicit ``dark``/``light`` where a reference was
    calibrated part by part). ``accent`` adds the class's accent stop (rust on
    metal). Every convention can be overridden, but the defaults are the
    class's measured grammar -- which is what lets the same object recipe be
    rebuilt in another material (a wooden drum) and still read correctly.
    """
    cls = MATERIAL_CLASSES[material]
    correct = hue if hue is not None else (cls.get("hue") or (lambda p: p))
    shading = shading if shading is not None else cls["shading"]
    terms = dict(split=split if split is not None else cls.get("split"),
                 rim=rim if rim is not None else cls.get("rim"),
                 gloss=gloss if gloss is not None else cls.get("gloss"),
                 ao=ao if ao is not None else cls.get("ao"))
    if texture_path is None:
        return toon_material(name, correct(paint), shading=shading, **terms)
    lo, hi = swing or cls["swing"]
    if dark is None:
        dark = tuple(c * lo for c in paint)
    if light is None:
        light = tuple(min(1.0, c * hi) for c in paint)
    kwargs = {}
    if accent is not None:
        kwargs["rust"] = correct(accent)
        kwargs["rust_position"] = (accent_position if accent_position is not None
                                   else cls.get("accent_position", 0.18))
    return toon_material(
        name, texture_path=texture_path,
        dark=correct(dark), light=correct(light),
        projection=projection or cls["projection"],
        texture_scale=texture_scale or cls["texture_scale"],
        ramp_range=ramp_range or cls["ramp_range"],
        shading=shading, **terms, **kwargs)


def tag_family(objs, family: str):
    """Mark parts as one family (foliage / fruit / wood / flower / soil) for the style
    pass. Accepts one object or any iterable of them; returns what it was given."""
    seq = objs if hasattr(objs, "__iter__") else (objs,)
    for o in seq:
        o["pz_family"] = family
    return objs


def toon_material(name: str, paint=None, texture_path=None, dark=None, light=None,
                  rust=None, rust_position=0.32, projection="UV",
                  texture_scale=1.0, ramp_range=(0.08, 0.92), shading=None,
                  material=None, split=None, rim=None, gloss=None, ao=None):
    """A stylised material: flat paint times the shared toon light ramp.

    ``paint`` is the flat paint colour; alternatively ``texture_path`` with
    ``dark``/``light`` ramps a detail map between two paint tones, exactly like
    the Cycles-era textured metal but composed with stepped light. ``rust`` adds
    a third paint stop between them -- a hue shift, not just a value shift, so
    weathering reads as staining rather than shading. With the vertical streak
    maps this is what makes wear look like rust run-off. Output is emission, so
    the engine adds no further lighting of its own.
    """
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()

    if shading is None:
        shading = MATERIAL_DEFAULTS.get(material, {}).get("shading", "step")
    # ``shading`` is either a mode string or a dict: {"mode": "step"|"soft",
    # "levels": {...}} with per-family TOON_LEVELS overrides.
    if isinstance(shading, dict):
        shade_mode = shading.get("mode", "step")
        shade_levels = shading.get("levels")
        shade_tints = shading.get("tints")
    else:
        shade_mode, shade_levels, shade_tints = shading, None, None
    ramp_node = nodes.new("ShaderNodeGroup")
    ramp_node.node_tree = toon_ramp_group(soft=(shade_mode == "soft"),
                                          levels=shade_levels,
                                          tints=shade_tints, ao=ao)

    if texture_path is not None:
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = bpy.data.images.load(str(texture_path), check_existing=True)
        tex.image.colorspace_settings.name = "Non-Color"
        tex.interpolation = "Cubic"
        tex.extension = "REPEAT"
        if projection in ("BOX", "WORLD"):
            tex.projection = "BOX"
            tex.projection_blend = 0.2
            mapping = nodes.new("ShaderNodeMapping")
            mapping.inputs["Scale"].default_value = (texture_scale,) * 3
            if projection == "WORLD":
                # World position, not object space. Object coordinates are LOCAL, so a
                # surface built from many small parts -- a bank of soil clods, a pile, a
                # rubble heap -- samples the same patch of the map in every one of them and
                # they all come out painted identically. Measured on the crop bed: 46 clods,
                # one texture. The map then carries no structure larger than a single part,
                # and the whole assembly reads as moulded repeats. World coordinates make one
                # continuous field across every part, which is what a tilled bed needs.
                geo = nodes.new("ShaderNodeNewGeometry")
                links.new(geo.outputs["Position"], mapping.inputs["Vector"])
            else:
                coords = nodes.new("ShaderNodeTexCoord")
                links.new(coords.outputs["Object"], mapping.inputs["Vector"])
            links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
        # ramp_range narrows how much of the map's 0-1 field spans dark-to-light.
        # Texture sampling averages ~4 texels per sprite pixel, pulling extremes
        # toward the middle; a narrower range maps those averaged mid values back
        # out to the full paint span so dark streaks actually reach dark paint.
        paint_ramp = nodes.new("ShaderNodeValToRGB")
        paint_ramp.color_ramp.elements[0].position = ramp_range[0]
        paint_ramp.color_ramp.elements[1].position = ramp_range[1]
        paint_ramp.color_ramp.elements[0].color = (*dark, 1.0)
        paint_ramp.color_ramp.elements[1].color = (*light, 1.0)
        if rust is not None:
            mid = paint_ramp.color_ramp.elements.new(rust_position)
            mid.color = (*rust, 1.0)
            # Recovery stop just past the rust: on a linear ramp the rust hue
            # otherwise interpolates all the way to the light stop and the whole
            # surface drifts wood-brown. This pins the mid-range back to the
            # neutral dark-to-light line, confining rust to the darkest streaks.
            rec_pos = min(0.85, rust_position + 0.22)
            t = (rec_pos - ramp_range[0]) / (ramp_range[1] - ramp_range[0])
            t = max(0.0, min(1.0, t))
            rec = paint_ramp.color_ramp.elements.new(rec_pos)
            rec.color = (*(d + (l - d) * t for d, l in zip(dark, light)), 1.0)
        links.new(tex.outputs["Color"], paint_ramp.inputs["Fac"])
        paint_out = paint_ramp.outputs["Color"]
    else:
        rgb = nodes.new("ShaderNodeRGB")
        rgb.outputs["Color"].default_value = (*paint, 1.0)
        paint_out = rgb.outputs["Color"]

    light_out = ramp_node.outputs["Light"]
    # --- painter's terms ----------------------------------------------------
    # These exist because a plant is not lit the way a crate is. A painter shades
    # each part on its own: the light half of a leaf is on the same side of every
    # leaf (split), a stem darkens along its underside and a fruit carries a drawn
    # outline (rim), and a fruit has a highlight dot (gloss). None of that comes
    # out of one diffuse ramp over 300 parts a few pixels wide.
    if split is not None:
        # light/dark halves keyed to the part's OWN width axis (object-local Y):
        # _leaf builds its blade along +X with the width along Y, so Y's sign is
        # the half. The reference paints the light half toward the screen's
        # upper-left on every leaf regardless of how the leaf actually faces.
        k_light, k_dark = split
        coord = nodes.new("ShaderNodeTexCoord")
        sep = nodes.new("ShaderNodeSeparateXYZ")
        side = nodes.new("ShaderNodeMath")
        side.operation = "GREATER_THAN"
        side.inputs[1].default_value = 0.0
        lerp = nodes.new("ShaderNodeMapRange")
        lerp.inputs["From Min"].default_value = 0.0
        lerp.inputs["From Max"].default_value = 1.0
        lerp.inputs["To Min"].default_value = k_dark
        lerp.inputs["To Max"].default_value = k_light
        scale = nodes.new("ShaderNodeVectorMath")
        scale.operation = "SCALE"
        links.new(coord.outputs["Object"], sep.inputs["Vector"])
        links.new(sep.outputs["Y"], side.inputs[0])
        links.new(side.outputs["Value"], lerp.inputs["Value"])
        links.new(light_out, scale.inputs[0])
        links.new(lerp.outputs["Result"], scale.inputs["Scale"])
        light_out = scale.outputs["Vector"]
    if rim is not None:
        # a drawn edge: where the surface grazes the view (Layer Weight facing
        # above 1-width) the light is multiplied by k. On a 2 px stem this is the
        # dark underside; on a fruit it is the outline.
        width, k = rim
        lw = nodes.new("ShaderNodeLayerWeight")
        lw.inputs["Blend"].default_value = 0.5
        edge = nodes.new("ShaderNodeMath")
        edge.operation = "GREATER_THAN"
        edge.inputs[1].default_value = 1.0 - width
        dark = nodes.new("ShaderNodeMapRange")
        dark.inputs["From Min"].default_value = 0.0
        dark.inputs["From Max"].default_value = 1.0
        dark.inputs["To Min"].default_value = 1.0
        dark.inputs["To Max"].default_value = k
        scale2 = nodes.new("ShaderNodeVectorMath")
        scale2.operation = "SCALE"
        links.new(lw.outputs["Facing"], edge.inputs[0])
        links.new(edge.outputs["Value"], dark.inputs["Value"])
        links.new(light_out, scale2.inputs[0])
        links.new(dark.outputs["Result"], scale2.inputs["Scale"])
        light_out = scale2.outputs["Vector"]
    if gloss is not None:
        # the highlight dot: a tight glossy lobe captured to RGB and added to the
        # light, so it lands where the key's reflection is -- upper left of a
        # fruit, as the reference paints it
        strength, rough = gloss if isinstance(gloss, (tuple, list)) else (gloss, 0.22)
        gl = nodes.new("ShaderNodeBsdfGlossy")
        gl.inputs["Roughness"].default_value = rough
        cap = nodes.new("ShaderNodeShaderToRGB")
        bw = nodes.new("ShaderNodeRGBToBW")
        gain = nodes.new("ShaderNodeMath")
        gain.operation = "MULTIPLY"
        gain.inputs[1].default_value = strength
        addv = nodes.new("ShaderNodeVectorMath")
        addv.operation = "ADD"
        tovec = nodes.new("ShaderNodeCombineXYZ")
        links.new(gl.outputs["BSDF"], cap.inputs["Shader"])
        links.new(cap.outputs["Color"], bw.inputs["Color"])
        links.new(bw.outputs["Val"], gain.inputs[0])
        for axis in ("X", "Y", "Z"):
            links.new(gain.outputs["Value"], tovec.inputs[axis])
        links.new(light_out, addv.inputs[0])
        links.new(tovec.outputs["Vector"], addv.inputs[1])
        light_out = addv.outputs["Vector"]
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.inputs["Factor"].default_value = 1.0
    emission = nodes.new("ShaderNodeEmission")
    out = nodes.new("ShaderNodeOutputMaterial")
    links.new(paint_out, mix.inputs["A"])
    links.new(light_out, mix.inputs["B"])
    links.new(mix.outputs["Result"], emission.inputs["Color"])
    links.new(emission.outputs["Emission"], out.inputs["Surface"])
    return mat


def use_eevee(scene) -> None:
    """Toon shading needs Shader to RGB, which only EEVEE evaluates.

    The EEVEE feature block is (re)applied here, not only in
    apply_render_settings: recipes often build the rig while the scene is
    still on Cycles, and without ray tracing EEVEE Next silently evaluates
    the Ambient Occlusion node -- the ramp's contact-shadow term -- as 1.0.
    """
    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    else:
        raise RuntimeError("no EEVEE engine available for toon shading")
    ev = getattr(scene, "eevee", None)
    if ev is not None:
        for attr, value in (("taa_render_samples", 64),
                            ("use_gtao", True),
                            ("use_raytracing", True),
                            ("use_shadows", True),
                            ("use_fast_gi", True)):
            if hasattr(ev, attr):
                setattr(ev, attr, value)


def _light_pass_material():
    """Pure white diffuse, for the light pass.

    Rendering the whole subject in white recovers the rig's light field alone --
    key, ambient, bounces, occlusion, and the cool-key/warm-shadow chroma -- with
    the paint factored out. The styled build divides the beauty render by this
    pass to recover each pixel's paint colour, restyles the *light* the way the
    vanilla painter does (quantised steps, per element), and recomposes. Painting
    with an understanding of the light instead of correcting a finished picture.
    """
    mat = bpy.data.materials.get("PZ_LightPass")
    if mat is None:
        mat = bpy.data.materials.new("PZ_LightPass")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    bsdf = nodes.new("ShaderNodeBsdfDiffuse")
    bsdf.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.5
    out = nodes.new("ShaderNodeOutputMaterial")
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def _id_pass_material():
    """Every object's own colour as flat emission, for the element map.

    Vanilla art treats each fitting of a sprite individually -- outline weight,
    a lit top edge, a shaded underside -- and the style pass can only do the same
    if it knows which pixels belong to which part. Object colours pass through a
    single override material, so no per-object material juggling is needed.
    """
    mat = bpy.data.materials.get("PZ_IDPass")
    if mat is None:
        mat = bpy.data.materials.new("PZ_IDPass")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    info = nodes.new("ShaderNodeObjectInfo")
    emission = nodes.new("ShaderNodeEmission")
    out = nodes.new("ShaderNodeOutputMaterial")
    links.new(info.outputs["Color"], emission.inputs["Color"])
    links.new(emission.outputs["Emission"], out.inputs["Surface"])
    return mat


def _tile_pass_material():
    """Flat colour per footprint TILE (floor of world x/y), for cutting a
    multi-tile object along the tile seam planes.

    A rotated footprint can fit the whole object inside one cell's camera
    frame, so the frame no longer cuts the object -- every cell would carry a
    complete copy. Vanilla cuts its art so each sprite holds exactly the
    pixels whose surface point stands on its own tile; this pass gives the
    build that assignment per pixel, id-pass style.
    """
    mat = bpy.data.materials.get("PZ_TilePass")
    if mat is None:
        mat = bpy.data.materials.new("PZ_TilePass")
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    geo = nodes.new("ShaderNodeNewGeometry")
    # Grid y runs SOUTH (screen lower-left) while Blender's +Y runs north, so
    # world y is negated before flooring -- the pass then encodes game grid
    # coordinates directly.
    flip = nodes.new("ShaderNodeVectorMath")
    flip.operation = "MULTIPLY"
    flip.inputs[1].default_value = (1.0, -1.0, 0.0)
    shift = nodes.new("ShaderNodeVectorMath")
    shift.operation = "ADD"
    shift.inputs[1].default_value = (0.5, 0.5, 0.0)
    snap = nodes.new("ShaderNodeVectorMath")
    snap.operation = "FLOOR"
    scale = nodes.new("ShaderNodeVectorMath")
    scale.operation = "MULTIPLY"
    # 0.055 per tile from a 0.05 base, NOT 0.25 from 0.15. The old spacing ran
    # off the end of the colour range at the fourth tile (0.15 + 0.25*4 = 1.15),
    # so every tile from 4 on rendered clamped white and decoded back to tile 3.
    # A wall set never noticed -- they are two or three tiles wide -- but an
    # eight-cell growth strip had its last four cells cut along tile 3's seam
    # planes, slicing spheres into crescents and ridges into gravel. This spacing
    # holds 16 tiles per axis inside [0,1] and is still 35x the 8-bit sRGB
    # quantisation step at that end of the curve, so the nearest-colour decode is
    # unambiguous.
    scale.inputs[1].default_value = (0.055, 0.055, 0.0)
    lift = nodes.new("ShaderNodeVectorMath")
    lift.operation = "ADD"
    lift.inputs[1].default_value = (0.05, 0.05, 0.5)
    emission = nodes.new("ShaderNodeEmission")
    out = nodes.new("ShaderNodeOutputMaterial")
    links.new(geo.outputs["Position"], flip.inputs[0])
    links.new(flip.outputs["Vector"], shift.inputs[0])
    links.new(shift.outputs["Vector"], snap.inputs[0])
    links.new(snap.outputs["Vector"], scale.inputs[0])
    links.new(scale.outputs["Vector"], lift.inputs[0])
    links.new(lift.outputs["Vector"], emission.inputs["Color"])
    links.new(emission.outputs["Emission"], out.inputs["Surface"])
    return mat


#: Linear colour a tile-pass pixel renders for footprint tile (i, j).
def tile_pass_color(i: int, j: int) -> tuple[float, float, float]:
    return (0.05 + 0.055 * i, 0.05 + 0.055 * j, 0.5)


#: Channel levels for element id colours -- 5 per channel, 125 distinct parts.
_ID_LEVELS = (0.9, 0.6, 0.35, 0.15, 0.75)


def _id_color(k: int) -> tuple[float, float, float]:
    return (_ID_LEVELS[k % 5], _ID_LEVELS[(k // 5) % 5], _ID_LEVELS[(k // 25) % 5])


def _descendant_meshes(root) -> list:
    out, stack = [], list(root.children)
    while stack:
        obj = stack.pop()
        stack.extend(obj.children)
        if obj.type == "MESH":
            out.append(obj)
    return out


def render_cells(context, report=None) -> dict:
    props = context.scene.pz_forge
    scene = context.scene
    cw, ch = cell_size(props.scale_2x)

    cam = bpy.data.objects.get(CAMERA_NAME)
    subject = bpy.data.objects.get(SUBJECT_NAME)
    if cam is None or subject is None:
        raise RuntimeError("Rig not found -- press 'Build PZ Rig' first")

    out_dir = bpy.path.abspath(props.output_dir)
    os.makedirs(out_dir, exist_ok=True)

    apply_render_settings(context)
    scene.camera = cam

    n_facings = int(props.facings)
    original_rotation = subject.rotation_euler.copy()
    original_location = subject.location.copy()
    original_filepath = scene.render.filepath
    cells = []

    # The light pass renders under Cycles even for toon builds (material_override
    # is Cycles-only, and Shader-to-RGB would not evaluate there anyway), so it
    # always carries the physical field; toon builds skip the relight path.
    light_override = _light_pass_material()

    parts = _descendant_meshes(subject)
    original_part_colors = {p.name: tuple(p.color) for p in parts}
    original_hidden = {p.name: p.hide_render for p in parts}
    element_colors = {}
    # Part FAMILIES ride along with the element ids. A recipe tags a part with
    # ``part["pz_family"] = "foliage"`` (or fruit, wood, flower, soil ...) and the
    # style pass can then treat a whole canopy as one thing -- ramp its light from
    # crown to skirt, rim its leaves -- which per-element passes cannot see. Set
    # with ``tag_family``; untagged parts export an empty string.
    families = {}
    # 125 ids for however many parts there are. A canopy of forty clusters, their
    # leaf shells, two dozen fruit with outline shells and a trunk runs past that,
    # and round-robin ids then hand one colour to a leaf AND a fruit, so the family
    # passes light fruit as foliage and rim leaves as fruit. So when parts exceed the
    # budget, ids are dealt out in FAMILY BLOCKS: each family gets a contiguous range
    # sized to its share, and its parts cycle inside it. Two parts may then share an
    # id, but never two families -- which is the only thing the family passes need.
    by_family: dict = {}
    for part in parts:
        by_family.setdefault(str(part.get("pz_family", "")), []).append(part)
    budget = len(_ID_LEVELS) ** 3
    ids: dict = {}
    if len(parts) <= budget or len(by_family) <= 1:
        for k, part in enumerate(parts):
            ids[part.name] = k % budget
    else:
        start = 0
        fams = sorted(by_family.items(), key=lambda kv: -len(kv[1]))
        for n, (fam, members) in enumerate(fams):
            block = (max(1, round(budget * len(members) / len(parts)))
                     if n < len(fams) - 1 else budget - start)
            block = max(1, min(block, budget - start - (len(fams) - 1 - n)))
            for j, part in enumerate(members):
                ids[part.name] = start + (j % block)
            start += block
    for part in parts:
        colour = _id_color(ids[part.name])
        part.color = (*colour, 1.0)
        element_colors[part.name] = list(colour)
        families[part.name] = str(part.get("pz_family", ""))

    try:
        for f in range(n_facings):
            facing = FACING_ORDER[f]
            # The key light is fixed in world space, exactly as it is in vanilla art,
            # so turning the subject is what makes an E-facing side read darker
            # than an S-facing one -- matching the game's own S/E contrast.
            subject.rotation_euler = Euler(
                (original_rotation.x, original_rotation.y,
                 original_rotation.z + math.radians(90.0 * f)), "XYZ")
            # A rotated multi-tile footprint occupies transposed tiles (2x1
            # becomes 1x2): swing the subject about its footprint centre and
            # park that centre on the rotated footprint's centre, so every
            # facing stays on tiles (0..fx-1, 0..fy-1) -- the layout the game
            # expects from its SpriteGridPos entries. Grid y runs SOUTH, which
            # is Blender's -Y, hence the negated y throughout.
            swap = f % 2 == 1
            fx = props.footprint_y if swap else props.footprint_x
            fy = props.footprint_x if swap else props.footprint_y
            c0 = Vector(((props.footprint_x - 1) * TILE / 2.0,
                         -(props.footprint_y - 1) * TILE / 2.0, 0.0))
            c1 = Vector(((fx - 1) * TILE / 2.0, -(fy - 1) * TILE / 2.0, 0.0))
            spin = Matrix.Rotation(math.radians(90.0 * f), 4, "Z")
            subject.location = original_location + c1 - (spin @ c0)

            overhang: list[tuple[str, float]] = []
            for j in range(fy):
                for i in range(fx):
                    aim = Vector((i * TILE, -j * TILE, aim_height(cw, ch)))
                    cam.location = aim + camera_direction() * 100.0
                    if props.isolate_tiles:
                        # Independent per-tile pieces (wall sets): hide every
                        # part whose footprint tile is not this cell's, so a
                        # southern neighbour cannot occlude -- and amputate --
                        # the piece behind it.
                        context.view_layer.update()
                        for part in parts:
                            corners = [part.matrix_world @ Vector(c)
                                       for c in part.bound_box]
                            cx = sum(v.x for v in corners) / 8.0
                            cy = sum(v.y for v in corners) / 8.0
                            tile = (int(math.floor(cx + 0.5)),
                                    int(math.floor(-cy + 0.5)))
                            part.hide_render = tile != (i, j)
                            if tile == (i, j):
                                # A part may sit on this tile and still HANG OVER
                                # it. The packer cuts every cell to its own tile
                                # with the tile pass, so the overhang is sliced
                                # off mid-object: spheres come out as crescents
                                # and ridges as fragments. The cut is correct --
                                # a wall-set piece must not bleed into its
                                # neighbour -- so the recipe has to stay inside,
                                # and the only thing missing was being told.
                                lo_x = min(v.x for v in corners)
                                hi_x = max(v.x for v in corners)
                                lo_y = min(v.y for v in corners)
                                hi_y = max(v.y for v in corners)
                                over = max(i * TILE - 0.5 * TILE - lo_x,
                                           hi_x - (i * TILE + 0.5 * TILE),
                                           (-j * TILE - 0.5 * TILE) - lo_y,
                                           hi_y - (-j * TILE + 0.5 * TILE))
                                if over > 0.004:
                                    overhang.append((part.name, over))
                    if overhang:
                        overhang.sort(key=lambda kv: -kv[1])
                        print(f"pz_sprite_forge: WARNING {len(overhang)} part(s) "
                              f"hang over tile ({i},{j}) and will be cut by the "
                              f"packer; worst: "
                              + ", ".join(f"{n} +{o * 100:.1f}% of a tile"
                                          for n, o in overhang[:5]))
                        overhang.clear()
                    name = f"{props.sheet_name}_{facing}_x{i}_y{j}.png"
                    scene.render.filepath = os.path.join(out_dir, name)
                    bpy.ops.render.render(write_still=True)

                    normal_name = name[:-4] + "_N.png"
                    element_name = name[:-4] + "_E.png"
                    light_name = name[:-4] + "_L.png"
                    tile_name = name[:-4] + "_T.png"
                    multi_tile = fx * fy > 1
                    view_layer = context.view_layer
                    samples = scene.cycles.samples
                    denoising = scene.cycles.use_denoising
                    filter_size = scene.render.filter_size
                    try:
                        # material_override only evaluates under Cycles, so the
                        # aux passes render there even when the beauty is EEVEE.
                        if props.toon_shading:
                            scene.render.engine = "CYCLES"
                        # Light pass at full quality: the light field is what the
                        # styled build divides by, so its noise becomes paint noise.
                        scene.render.filepath = os.path.join(out_dir, light_name)
                        view_layer.material_override = light_override
                        bpy.ops.render.render(write_still=True)

                        scene.cycles.samples = 16
                        scene.cycles.use_denoising = False
                        scene.render.filepath = os.path.join(out_dir, normal_name)
                        view_layer.material_override = _normal_pass_material()
                        bpy.ops.render.render(write_still=True)

                        # Near-box pixel filter: an antialiased element edge blends
                        # two id colours into a third that maps to neither part.
                        scene.render.filepath = os.path.join(out_dir, element_name)
                        view_layer.material_override = _id_pass_material()
                        scene.render.filter_size = 0.01
                        bpy.ops.render.render(write_still=True)

                        if multi_tile:
                            scene.render.filepath = os.path.join(out_dir,
                                                                 tile_name)
                            view_layer.material_override = _tile_pass_material()
                            bpy.ops.render.render(write_still=True)
                    finally:
                        view_layer.material_override = None
                        scene.cycles.samples = samples
                        scene.cycles.use_denoising = denoising
                        scene.render.filter_size = filter_size
                        if props.toon_shading:
                            use_eevee(scene)

                    record = {"file": name, "facing": facing, "x": i, "y": j,
                              "normal": normal_name, "element": element_name,
                              "light": light_name}
                    if multi_tile:
                        record["tile"] = tile_name
                    cells.append(record)
    finally:
        subject.rotation_euler = original_rotation
        subject.location = original_location
        scene.render.filepath = original_filepath
        cam.location = Vector((0.0, 0.0, aim_height(cw, ch))) + camera_direction() * 100.0
        for part in parts:
            part.color = original_part_colors[part.name]
            part.hide_render = original_hidden[part.name]

    manifest = {
        "elements": element_colors,
        "families": families,
        "toon": bool(props.toon_shading),
        # Unit vector from the aim point toward the camera, so image-space passes
        # (edge-turn shading) can tell which surfaces graze the view.
        "view": [round(c, 6) for c in camera_direction()],
        "sheet": props.sheet_name,
        "cell": [cw, ch],
        "scale": "2x" if props.scale_2x else "1x",
        "footprint": [props.footprint_x, props.footprint_y],
        "isolate_tiles": bool(props.isolate_tiles),
        "facings": [FACING_ORDER[f] for f in range(n_facings)],
        "clear_height_m": round(clear_height(cw, ch), 4),
        "cells": cells,
    }
    path = os.path.join(out_dir, "manifest.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    if report:
        report({"INFO"}, f"PZ Forge: rendered {len(cells)} cell(s) to {out_dir}")
    return manifest


# --------------------------------------------------------------------------- #
# Operators
# --------------------------------------------------------------------------- #

class PZFORGE_OT_build_rig(Operator):
    bl_idname = "pzforge.build_rig"
    bl_label = "Build PZ Rig"
    bl_description = "Create/refresh the isometric camera, key light, subject anchor and render settings"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        build_rig(context)
        cw, ch = cell_size(context.scene.pz_forge.scale_2x)
        self.report({"INFO"},
                    f"PZ rig ready: {cw}x{ch} cells, {clear_height(cw, ch):.2f} m clear height")
        return {"FINISHED"}


class PZFORGE_OT_parent_selected(Operator):
    bl_idname = "pzforge.parent_selected"
    bl_label = "Attach Selection to Subject"
    bl_description = "Parent the selected objects to PZ_Subject so facing variants rotate them"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        subject = bpy.data.objects.get(SUBJECT_NAME)
        if subject is None:
            self.report({"ERROR"}, "Build the rig first")
            return {"CANCELLED"}
        n = 0
        for obj in context.selected_objects:
            if obj is subject or obj.name in (CAMERA_NAME, SUN_NAME, GUIDE_NAME):
                continue
            obj.parent = subject
            obj.matrix_parent_inverse = subject.matrix_world.inverted()
            n += 1
        self.report({"INFO"}, f"Attached {n} object(s) to {SUBJECT_NAME}")
        return {"FINISHED"}


class PZFORGE_OT_load_reference(Operator):
    bl_idname = "pzforge.load_reference"
    bl_label = "Load Reference Overlay"
    bl_description = ("Show a vanilla sprite as a camera background so you can model "
                      "directly against it. Extract the PNG first with: "
                      "pzforge extract <pack> <dir>")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.pz_forge
        cam = bpy.data.objects.get(CAMERA_NAME)
        if cam is None:
            self.report({"ERROR"}, "Build the rig first")
            return {"CANCELLED"}
        path = bpy.path.abspath(props.reference_image)
        if not os.path.isfile(path):
            self.report({"ERROR"}, f"No file at {path}")
            return {"CANCELLED"}

        cam.data.show_background_images = True
        cam.data.background_images.clear()
        bg = cam.data.background_images.new()
        bg.image = bpy.data.images.load(path, check_existing=True)
        # The extracted cell PNG shares the render's exact aspect, so stretching
        # aligns it 1:1 with the frame -- the reference sits precisely where the
        # render will land, pixel for pixel.
        bg.frame_method = "STRETCH"
        bg.alpha = props.reference_opacity
        bg.display_depth = "FRONT"
        self.report({"INFO"}, f"Reference overlaid at {props.reference_opacity:.0%}")
        return {"FINISHED"}


class PZFORGE_OT_render_cells(Operator):
    bl_idname = "pzforge.render_cells"
    bl_label = "Render Tile Cells"
    bl_description = "Render every tile of the footprint, for every facing, into the output directory"

    def execute(self, context):
        try:
            render_cells(context, self.report)
        except Exception as ex:  # surfaced in the UI rather than only the console
            self.report({"ERROR"}, str(ex))
            return {"CANCELLED"}
        return {"FINISHED"}


class PZFORGE_OT_calibrate(Operator):
    bl_idname = "pzforge.calibrate"
    bl_label = "Check Lighting vs Vanilla"
    bl_description = ("Render a reference cube and compare its face brightness with the "
                      "values measured from the game's own wall tiles")

    def execute(self, context):
        try:
            result = calibrate(context)
        except Exception as ex:
            self.report({"ERROR"}, str(ex))
            return {"CANCELLED"}
        self.report({"INFO"}, result)
        return {"FINISHED"}


def _emission_material(name: str, value: float):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    emit = nodes.new("ShaderNodeEmission")
    emit.inputs[0].default_value = (value, value, value, 1.0)
    links.new(emit.outputs[0], out.inputs[0])
    return mat


def calibrate(context) -> str:
    """Render a mid-grey cube and compare its face brightness against vanilla.

    Faces are isolated with noise-free emission renders rather than by sampling
    fixed rectangles, and pixel values are decoded from sRGB before being averaged,
    because ``Image.pixels`` returns the file buffer rather than linear light.
    """
    props = context.scene.pz_forge
    coll = rig_collection(context)
    out_dir = bpy.path.abspath(props.output_dir)
    os.makedirs(out_dir, exist_ok=True)

    grey = bpy.data.materials.get("PZ_CalMat") or bpy.data.materials.new("PZ_CalMat")
    grey.use_nodes = True
    bsdf = grey.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.5, 0.5, 0.5, 1.0)
        bsdf.inputs["Roughness"].default_value = 1.0
        if "Specular IOR Level" in bsdf.inputs:
            bsdf.inputs["Specular IOR Level"].default_value = 0.0

    mesh = bpy.data.meshes.new("PZ_CalCube")
    s = 0.5
    verts = [(-s, -s, 0), (s, -s, 0), (s, s, 0), (-s, s, 0),
             (-s, -s, 1), (s, -s, 1), (s, s, 1), (-s, s, 1)]
    mesh.from_pydata(verts, [], [(3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4),
                                 (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)])
    mesh.update()
    cube = bpy.data.objects.new("PZ_CalCube", mesh)
    _relink(cube, coll)

    # Faces are picked by where their centre sits, not by their normal: normals depend
    # on vertex winding, and a flipped one silently empties the mask.
    visible = {"S": lambda c: c.y < -0.49,
               "E": lambda c: c.x > 0.49,
               "top": lambda c: c.z > 0.99}
    for _ in range(4):
        cube.data.materials.append(grey)
    for poly in cube.data.polygons:
        poly.material_index = next(
            (i for i, test in enumerate(visible.values()) if test(poly.center)), 3)

    prev_path = context.scene.render.filepath

    def render(name: str):
        path = os.path.join(out_dir, name)
        context.scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        img = bpy.data.images.load(path, check_existing=False)
        px = list(img.pixels)
        bpy.data.images.remove(img)
        return px

    def to_linear(v):
        v = min(1.0, max(0.0, v))
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    try:
        apply_render_settings(context)
        white = _emission_material("PZ_CalWhite", 1.0)
        black = _emission_material("PZ_CalBlack", 0.0)

        masks = {}
        for i, face in enumerate(visible):
            for j, slot in enumerate(cube.material_slots):
                slot.material = white if j == i else black
            px = render(f"_cal_mask_{face}.png")
            masks[face] = [k for k in range(0, len(px), 4)
                           if px[k + 3] > 0.95 and px[k] > 0.5]

        for slot in cube.material_slots:
            slot.material = grey
        px = render("_calibration.png")
    finally:
        context.scene.render.filepath = prev_path
        bpy.data.objects.remove(cube, do_unlink=True)

    def mean(face):
        idx = masks[face]
        if not idx:
            return float("nan")
        total = sum(0.2126 * to_linear(px[k]) + 0.7152 * to_linear(px[k + 1])
                    + 0.0722 * to_linear(px[k + 2]) for k in idx)
        linear = total / len(idx)
        encoded = (linear * 12.92 if linear <= 0.0031308
                   else 1.055 * linear ** (1 / 2.4) - 0.055)
        return encoded * 255.0

    south, east = mean("S"), mean("E")
    got = south / east if east else float("nan")
    want = VANILLA_TARGETS["S"] / VANILLA_TARGETS["E"]
    ok = abs(got - want) < 0.05 and abs(south - VANILLA_TARGETS["S"]) < 8
    return (f"S={south:.1f} (vanilla {VANILLA_TARGETS['S']:.1f}), "
            f"E={east:.1f} (vanilla {VANILLA_TARGETS['E']:.1f}), "
            f"S/E={got:.3f} (vanilla {want:.3f}) -- "
            + ("matches vanilla" if ok else "adjust Key/Ambient"))


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #

class PZFORGE_PT_panel(Panel):
    bl_label = "PZ Sprite Forge"
    bl_idname = "PZFORGE_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "PZ Forge"

    def draw(self, context):
        layout = self.layout
        props = context.scene.pz_forge
        cw, ch = cell_size(props.scale_2x)

        col = layout.column(align=True)
        col.prop(props, "scale_2x")
        row = col.row(align=True)
        row.prop(props, "footprint_x")
        row.prop(props, "footprint_y")
        col.prop(props, "facings")
        col.prop(props, "alignment")
        col.prop(props, "show_guide")
        col.prop(props, "ground_occlusion")

        box = layout.box()
        box.label(text=f"Cell {cw}x{ch} px", icon="MESH_GRID")
        box.label(text=f"Clear height {clear_height(cw, ch):.2f} m")
        box.label(text=f"{pixels_per_metre(cw, ch):.1f} px per metre")

        layout.operator(PZFORGE_OT_build_rig.bl_idname, icon="OUTLINER_OB_CAMERA")
        layout.operator(PZFORGE_OT_parent_selected.bl_idname, icon="LINKED")

        ref = layout.box()
        ref.label(text="Model against a reference", icon="IMAGE_REFERENCE")
        ref.prop(props, "reference_image")
        ref.prop(props, "reference_opacity")
        ref.operator(PZFORGE_OT_load_reference.bl_idname, icon="IMAGE_BACKGROUND")

        light = layout.box()
        light.label(text="Lighting (measured from vanilla)", icon="LIGHT_SUN")
        light.prop(props, "sun_strength")
        light.prop(props, "ambient_strength")
        light.prop(props, "contrast_boost")
        light.operator(PZFORGE_OT_calibrate.bl_idname, icon="SEQ_HISTOGRAM")

        layout.separator()
        layout.prop(props, "sheet_name")
        layout.prop(props, "output_dir")
        layout.operator(PZFORGE_OT_render_cells.bl_idname, icon="RENDER_STILL")


CLASSES = (PZForgeProps, PZFORGE_OT_build_rig, PZFORGE_OT_parent_selected,
           PZFORGE_OT_load_reference,
           PZFORGE_OT_render_cells, PZFORGE_OT_calibrate, PZFORGE_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.pz_forge = bpy.props.PointerProperty(type=PZForgeProps)


def unregister():
    del bpy.types.Scene.pz_forge
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()


