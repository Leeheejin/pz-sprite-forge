"""Farmed crops for the FruitFarming mod -- one tilesheet per crop, eight growth stages.

Vanilla farming sprites are the reference (``vegetation_farming_01`` / ``_01b`` and
their unhealthy/dying/dead/trampled siblings). What the reference actually is, measured:

* The cell is 128x256 at 2x and every crop is **bottom anchored**: the sprite's trim box
  ends at y=250 of 256 for nearly all crops, so a plant stands on the tile, it does not
  float.  (measured over 8 crops x 8 stages with ``pzforge.compare.vanilla_sprite``)
* **Stage 0 is bare tilled soil.**  BellPepper, Cucumber, Hops, Strawberryplant and Corn
  all trim to 100x55 at stage 0 -- the same ridge bed, no plant.  The bed is part of every
  crop sprite, not a separate floor tile.
* The bed is **two ridges running screen lower-left to upper-right**, which is world +X
  (the repo's own convention: "+X direction = screen upper-right").  Leafy-head and grain
  crops use three.
* **Stage 7 is withered, not bigger.**  Measured trim heights shrink at stage 7 for Barley
  (173->134), Hops (220->118), Cucumber (144->118) and BellPepper (128->124): the game
  draws the rotten plant there, because ``nbOfGrow > fullGrown`` is the rot branch.
* Growth is not linear.  BellPepper trim heights by stage are 55,61,66,82,119,128,128,124
  px; subtracting the 55 px bed gives 0,6,11,27,64,73,73,69 px of plant.  Most of the
  growth lands between stage 3 and stage 4.
* A bush crop is **three plants**, not one (counted in the 4x read of
  ``vegetation_farming_01b_70``), standing on/between the ridges.
* Foliage paint: ``pzforge spec vegetation_farming_01b_70 --sprite`` gives the brightest
  significant shade rgb(72,112,72) = albedo (0.175,0.438,0.175) on an S face, over a
  palette that runs #305030..#487048.  Soil reads #382008..#503810.

Run one crop:
    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" -b \
        -P examples/ff_crop.py -- apple
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "blender"))
sys.path.insert(0, str(ROOT))

import pz_sprite_forge as F  # noqa: E402

STAGES = 8

# --------------------------------------------------------------------------- #
# Measured constants
# --------------------------------------------------------------------------- #

#: Screen px per world unit of HEIGHT at 2x: the cell spans sqrt(2) tiles over 128 px
#: (ortho_scale), and a vertical edge foreshortens by cos(30 deg) under the rig camera.
#: 128 / sqrt(2) * cos(30) = 78.4.
PX_PER_Z = (128.0 / math.sqrt(2.0)) * math.cos(math.radians(30.0))

#: World yaw whose axis runs ACROSS the screen under the rig camera (azimuth 45 deg):
#: screen-right is -(1,1,0)/sqrt(2), so a leaf pointing along +-45 deg shows its whole
#: blade, while one along 135/315 deg points at the viewer and projects to a sliver.
#: Vanilla draws every leaf broadside for exactly this reason -- a painter never wastes a
#: leaf on a foreshortened spike. Biasing leaf yaw toward this axis is what turns our
#: radial "thistle" silhouette back into a plant.
SCREEN_YAW = math.radians(45.0)
#: How far a leaf may stray from that axis. Wide enough to avoid a fan of parallel
#: clones, narrow enough that few leaves end up edge-on.
SCREEN_YAW_SPREAD = math.radians(52.0)

#: THE PLANT, AUTHORED NODE BY NODE.
#:
#: Measured against ``vegetation_farming_01b_70`` (mature BellPepper), which is the habit
#: every fruit crop in the mod already borrows:
#:
#: * **23.3% of the reference's foliage ink is thin runs** (horizontal runs of 3 px or
#:   less) against 7.3% of ours. That ink is the skeleton -- the main stem and the
#:   petioles -- and it is why the reference reads as a *plant* while ours read as a hedge.
#: * **A canopy row crosses background 4.91 times** in the reference against 1.93 in ours,
#:   at an almost identical fill (0.232 vs 0.238). Same amount of paint, opposite
#:   distribution: the reference spends it on many separated islands, we spent it on a slab.
#:
#: The cause was placement, not quantity. Three leaves were laid along each branch inside
#: a span (22 px) shorter than one leaf (23 px), so they could only overlap. So: **one leaf
#: per node**, hung at the END of a petiole that is longer than the blade is wide, and the
#: nodes spread up the whole stem with the sides alternating, so no two blades land in the
#: same place and the gaps between them are real background.
#:
#: Leaf size follows from the reference too: its blades measure about 14x8 px, so a plant
#: carries ~13 of them rather than a couple of dozen small ones.
#:   (height along stem, yaw offset, petiole length x, leaf size x, leaf pitch in degrees)
#: (height along stem, yaw offset, cane length x, rise deg at the base, bend deg at the
#: tip, leaflet pairs). Seven canes leave the stem alternating sides; they start steep and
#: flatten as they climb, and they shorten so the plant tapers into the rounded triangle
#: the reference's plants read as.
#: (height along stem, yaw offset, cane length x, rise deg at the base, angle at the tip,
#: leaflet pairs). The reference's branches RISE: every one leaves the stem climbing and
#: only flattens near its tip, so the plant reads as a spray opening upward. Canes that
#: arced downward, as v15's did, hung the foliage off the stem like washing on a line.
CANES = (
    (0.20, -0.58, 1.10, 54, 22, 2),
    (0.36, +0.46, 1.14, 52, 26, 2),
    (0.52, -0.38, 1.04, 56, 30, 1),
    (0.68, +0.64, 0.90, 60, 34, 1),
    (0.86, -0.28, 0.70, 66, 42, 1),
)

#: Screen rows between the bed's TOP row and where a plant standing on a ridge actually
#: projects. The bed's silhouette top is the crest of the far ridge; the plants stand in
#: front of it, so their feet land 17 px lower down the screen. BUSH_PLANT_PX measures how
#: far a vanilla plant rises ABOVE that silhouette, so a plant must be built this much
#: taller than the measurement to reach the same row. Measured by rendering and diffing
#: the per-stage alpha bbox tops against vegetation_farming_01b_64..71: ours came out
#: 6/11/21/19/19/17/17 px short at stages 1-7, which is this constant, not a scale error.
BASE_DROP_PX = 15.0

#: Plant height above the bed, per stage, in px -- BellPepper trim heights minus its
#: 55 px stage-0 bed. Converted to world units at use.
BUSH_PLANT_PX = (0.0, 6.0, 11.0, 27.0, 64.0, 73.0, 73.0, 69.0)

#: Fruit shows at the mature stage and fills out at fullGrown; stage 7 has none (the
#: withered vanilla sprites carry no fruit).
FRUIT_BY_STAGE = (0.0, 0.0, 0.0, 0.0, 0.0, 0.55, 1.0, 0.0)
#: Flowers lead the fruit by a stage, as vanilla's white/yellow blooms do.
FLOWER_BY_STAGE = (0.0, 0.0, 0.0, 0.0, 1.0, 0.6, 0.0, 0.0)

#: Soil and foliage paints are the reference palette inverted through the rig's lighting
#: response (``pzforge.spec.albedo_for``), never the rendered colours themselves -- using
#: rendered values as albedo counts the lighting twice, a documented pitfall here.
#:   soil  #382008 -> (0.099,0.036,0.006) and #503810 -> (0.200,0.099,0.013) on top
#:   leaf  #305030 -> (0.080,0.217,0.080) and #487048 -> (0.175,0.438,0.175) on S
#: v4's bed measured med RGB (72,52,20) with a value spread of only 0.122 against the
#: reference's 0.239-0.275, and its dark end never arrived (p10 0.243 vs vanilla 0.130).
#: Two measured corrections: push the dark stop well below the palette's darkest shade so
#: the texture has somewhere dark to land, and narrow the ramp window so the ~3-4 texels
#: that average into one sprite pixel are re-expanded over the full paint span (the same
#: ramp_range remedy this project calibrated on the wood table's grain).
SOIL_DARK = (0.030, 0.013, 0.003)
SOIL_LIGHT = (0.330, 0.196, 0.020)
#: Leaves are painted in SEVERAL greens, not one: the reference's palette spends its top
#: five entries on #305030 / #305838 / #487048 / #385838 / #406040. One flat green gave
#: every leaf the same tone, so overlapping leaves fused into a single mass with no
#: readable edge -- the main reason our canopy looked like moulded plastic.
#: Corrected by the MEASURED per-channel transfer against the reference canopy:
#: vanilla pepper s6 greens sit at med (62,93,65), ours at (46,75,45) -- R x1.35,
#: G x1.24, B x1.44. B moves most because vanilla foliage is a softer, greyer green
#: (B/G 0.70) than the saturated one the palette inversion alone produced (0.60).
#: Canopy hue 124.3 deg, measured on the reference's GREEN pixels only --
#: pzforge compare's whole-sprite hue mixes in the soil bed and reads 105.
#: Their SPREAD is measured too, and the old set was twice too wide. The reference's
#: green pixels run v10 0.310 / v50 0.357 / v90 0.455 -- a total tonal range of 1.47x,
#: where ours ran 0.204 / 0.282 / 0.373 = 2.9x once the ramp's own swing is counted in.
#: Half our canopy was sitting in a shadow vanilla never paints; that, not the hue, is
#: what made it read as a dark mass. So the four greens now differ by only 1.19x in value
#: (the ramp supplies the other 1.23x) and every one of them sits on hue 124.5 at the
#: reference's measured saturation, 0.62 in albedo.
LEAF_PAINTS = (
    (0.131, 0.344, 0.147),
    (0.126, 0.331, 0.141),
    (0.121, 0.318, 0.136),
    (0.116, 0.305, 0.130),
)
LEAF_PAINT = LEAF_PAINTS[0]
#: The withered sprites replace the green with a dry straw; measured off Barley stage 7
#: (#8a7a46-ish family) and used for every crop's rot frame.
DEAD_PAINT = (0.185, 0.200, 0.076)
#: Stem tone is measured, not guessed. Splitting the reference's green pixels by run
#: length gives thin runs (the skeleton) at v50 0.349 against the blades' 0.361 -- the
#: stem is the SAME green as the leaves, a hair darker, not the separate olive we used.
#: Ours read 16 deg off in hue (108 vs 124) because that olive was mixed by eye. A thin
#: cylinder does catch the ramp's top level along its whole length, so the albedo is set
#: from the measured render response of a thin part (0.965x) rather than a blade's (0.90x).
STEM_PAINT = (0.128, 0.300, 0.126)


def _f(v):
    return float(v)


# --------------------------------------------------------------------------- #
# Crop table -- archetype + the per-crop parameters that differ
# --------------------------------------------------------------------------- #
#: fruit colours are the dominant *chromatic* shade of each produce item's vanilla
#: inventory icon (Item_Apple ...), measured with a share-weighted histogram over
#: saturated pixels; hue is the identity, value is pulled into the tile art's band.
CROPS = {
    # --- fruit bushes -----------------------------------------------------
    "apple":      dict(arch="tree", shape="apple", fruit=(0.88, 0.03, 0.03), fruit_r=0.080, leaf=1.00),
    "pear":       dict(arch="tree", shape="pear", fruit=(0.95, 1.00, 0.11), fruit_r=0.078, leaf=1.00),
    "peach":      dict(arch="tree", shape="peach", blush=(1.00, 0.29, 0.24), blush_z=-0.35, fruit=(1.00, 0.68, 0.28), fruit_r=0.075, leaf=0.95),
    "cherry":     dict(arch="tree", shape="cherry", fruit=(0.898, 0.012, 0.022), fruit_r=0.036, leaf=0.80,
                       fruit_n=3),
    "orange":     dict(arch="tree", shape="citrus", fruit=(1.00, 0.38, 0.003), fruit_r=0.080, leaf=0.90),
    "lemon":      dict(arch="tree", shape="lemon", fruit=(1.00, 0.69, 0.036), fruit_r=0.070, leaf=0.90),
    "lime":       dict(arch="tree", shape="lime", fruit=(0.081, 0.41, 0.003), fruit_r=0.066, leaf=0.90),
    "grapefruit": dict(arch="tree", shape="citrus", fruit=(1.00, 0.18, 0.010), fruit_r=0.090, leaf=0.95),
    "avocado":    dict(arch="tree", shape="avocado", fruit=(0.045, 0.085, 0.030), fruit_r=0.078, leaf=1.10,
                       fruit_pear=True),
    "mango":      dict(arch="tree", shape="mango", blush=(1.00, 0.135, 0.105), blush_z=0.25, fruit=(1.00, 0.60, 0.057), fruit_r=0.072, leaf=1.15,
                       fruit_pear=True),
    # Small-fruited crops need MORE and slightly larger berries, not true-to-life ones:
    # at 2x a 0.026-radius olive is under 5 px and disappears into the canopy, which is
    # what made olive/coffee indistinguishable from the stone fruits in the v4 sheet.
    "olive":      dict(arch="tree", shape="olive", fruit=(0.258, 0.130, 0.195), fruit_r=0.040, leaf=0.70,
                       fruit_n=7),
    "coffee":     dict(arch="tree", shape="coffee", fruit=(1.00, 0.083, 0.077), fruit_r=0.040, leaf=1.05,
                       fruit_n=7),
    "peanut":     dict(arch="bush", shape="peanut", fruit=(0.62, 0.50, 0.24), fruit_r=0.050, leaf=0.85,
                       height_scale=0.55, fruit_n=5),
    # --- other habits -----------------------------------------------------
    "banana":     dict(arch="broadleaf", shape="banana", fruit=(0.72, 0.52, 0.08), fruit_r=0.030),
    "pineapple":  dict(arch="rosette", shape="pineapple", fruit=(0.62, 0.46, 0.06), fruit_r=0.062),
    "grape":      dict(arch="trellis", shape="grape", fruit=(0.30, 0.10, 0.16), fruit_r=0.024),
    "rice":       dict(arch="grain", shape="grain", fruit=(0.50, 0.42, 0.12), fruit_r=0.000),
    "ginger":     dict(arch="clump", shape="rhizome", fruit=(0.50, 0.40, 0.22), fruit_r=0.040),
}


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #

def _mesh_object(name, verts, faces, mat):
    """Build a mesh from explicit geometry.

    Scaled primitives cannot carry the features that make vanilla foliage read -- a leaf's
    midrib, its pointed tip, a fruit's lobes -- and no style pass can add a feature the
    geometry never had. So the plant parts are authored as real meshes.
    """
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.validate()
    me.update()
    obj = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(mat)
    return obj


#: Segments along a leaf blade. Seven is enough to arc it without spending polygons that
#: the 12-18 px it occupies could never show.
LEAF_SEGMENTS = 7


#: Unit vector pointing AWAY from the rig camera (camera is at +X,-Y,+Z looking in).
#: An outline shell is pushed along this so it hides behind its own leaf and shows only
#: as a rim.
_AWAY = (-0.612, 0.612, -0.5)

#: How much larger the outline shell is than the blade. 1 px of rim at the sprite's scale;
#: vanilla's drawn leaf edge measures 1-2 px (the repo's own line statistics put drawn
#: lines at 1.9 px mean thickness).
OUTLINE_GROW = 1.13


def _leaf(name, mat, loc, size, yaw, pitch, droop=0.0, midrib=0.62, curl=0.22,
          outline_mat=None, width_ratio=0.30, roll=0.0):
    """A real leaf: tapered blade, pointed tip, raised midrib, arched along its length.

    The cross-section is a shallow V (centre line above the two edges). That is what puts a
    light midrib line down a dark blade under the toon ramp -- exactly how the reference
    draws a leaf -- and it is the feature v1-v9's flattened ellipsoid could never have.
    """
    # Grass is not a broadleaf: a cereal blade is roughly 8:1, a bush leaf 3:1, and
    # drawing rice with bush proportions is what made the paddy look like a shrubbery.
    half_w = size * width_ratio
    verts, faces = [], []
    for i in range(LEAF_SEGMENTS + 1):
        t = i / LEAF_SEGMENTS
        # widest just past a third of the way out, zero at the petiole and at the tip
        w = half_w * math.sin(math.pi * (t ** 0.78)) ** 0.85
        x = size * t
        z_arc = -curl * size * t * t           # the blade arcs over as it reaches out
        edge_drop = midrib * max(w, 1e-5)      # edges hang below the midrib
        verts.append((x, -w, z_arc - edge_drop))
        verts.append((x, 0.0, z_arc))
        verts.append((x, w, z_arc - edge_drop))
    for i in range(LEAF_SEGMENTS):
        a = i * 3
        faces.append((a, a + 3, a + 4, a + 1))
        faces.append((a + 1, a + 4, a + 5, a + 2))
    obj = _mesh_object(name, verts, faces, mat)
    obj.location = loc
    # The blade is an OPEN strip, and an open surface seen from behind shades as if lit
    # from behind -- half the canopy rendered near-black. Solidify closes it into a real
    # slab, which is the same reason this project forbids bare planes.
    solid = obj.modifiers.new("leaf_solid", "SOLIDIFY")
    solid.thickness = max(size * 0.045, 0.004)
    solid.offset = 0.0
    # FLAT shading throughout: a smooth normal sweeps the ramp continuously and leaves the
    # sprite with no flat area (measured 2.9% against the reference's 21.2%). Faceting is
    # what lets each blade half resolve to one painted tone.
    # ROLL is what gives a leaf its width on screen. The blade is built lying flat, and
    # a flat blade under this rig's 30 deg camera is foreshortened to half its width --
    # which is why ours read as dashes beside the reference's broad ovals. Rolling the
    # blade about its own length turns its face toward the eye: with the length along the
    # screen axis the normal reaches the camera vector (0.612,-0.612,0.5) at exactly
    # +-60 deg, so that is the roll a leaf drawn broadside wants.
    obj.rotation_euler = (roll, pitch + droop, yaw)
    if outline_mat is None:
        return [obj]
    # A renderer draws no lines; vanilla foliage is bounded by them. Give every blade a
    # slightly larger dark shell parked just behind it, so what shows past the blade's
    # silhouette is a 1 px dark rim -- the drawn edge, built as geometry because that is
    # the only place it can come from.
    shell = _mesh_object(name + "_edge", verts, faces, outline_mat)
    shell.location = (loc[0] + _AWAY[0] * size * 0.045,
                      loc[1] + _AWAY[1] * size * 0.045,
                      loc[2] + _AWAY[2] * size * 0.045)
    shell.rotation_euler = obj.rotation_euler
    shell.scale = (OUTLINE_GROW, OUTLINE_GROW, OUTLINE_GROW)
    sm = shell.modifiers.new("edge_solid", "SOLIDIFY")
    sm.thickness = max(size * 0.045, 0.004)
    sm.offset = 0.0
    return [shell, obj]


def _berry(name, mat, loc, r, pear=False, lobes=0, lobe_amp=0.10, dimple=0.12,
           outline_mat=None):
    """Fruit with a shape, not a ball: optional lobes around the axis and a calyx dimple
    sunk into the top. A featureless sphere is what read as a plastic bead.

    ``outline_mat`` gives it the same drawn rim the leaves get. The reference bounds every
    fruit with a dark line where it crosses a leaf; without one our fruit sat *in* the
    canopy as a flat lozenge with nothing separating it from the green behind.
    """
    def _shape(nm, rr, material):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=14, ring_count=9, radius=rr,
                                             location=loc)
        obj = bpy.context.active_object
        obj.name = nm
        me = obj.data
        for v in me.vertices:
            x, y, z = v.co
            rad = math.hypot(x, y)
            if lobes and rad > 1e-6:
                s = 1.0 + lobe_amp * math.cos(lobes * math.atan2(y, x))
                v.co.x, v.co.y = x * s, y * s
            if dimple and z > rr * 0.52:        # press the stem well in
                f = (z - rr * 0.52) / (rr * 0.48)
                v.co.z = z - dimple * rr * f * f * 1.1
            if z < -rr * 0.62:                  # and flatten the blossom end a little
                v.co.z = z + 0.22 * rr * ((-z - rr * 0.62) / (rr * 0.38))
        me.update()
        if pear:
            obj.scale = (0.86, 0.86, 1.28)
        obj.data.materials.append(material)
        return obj

    obj = _shape(name, r, mat)
    if outline_mat is None:
        return [obj]
    # A shell round a SPHERE has to be pushed back further than it is grown, or its near
    # cap comes out in front of the fruit and paints the whole berry the outline colour --
    # which is exactly what turned the apples into green lumps.
    # The shell is displaced along the VIEW AXIS and nothing else. Any sideways component
    # shifts it in screen space, and because it is the LARGER sphere its surface then wins
    # the depth test down one flank -- which is why apples were coming out as red crescents
    # inside a green hood. Offset purely along _AWAY, by more than the shell is grown
    # (0.30r against 0.16r), and it sits concentrically behind and shows as an even 1 px
    # rim: the drawn outline the reference puts around every fruit.
    shell = _shape(name + "_edge", r * 1.16, outline_mat)
    shell.location = (loc[0] + _AWAY[0] * r * 0.30,
                      loc[1] + _AWAY[1] * r * 0.30,
                      loc[2] + _AWAY[2] * r * 0.30)
    return [shell, obj]


def _stalk(name, mat, base, height, r0, r1=None, tilt=0.0, yaw=0.0, sides=5, lean=0.0):
    """A tapered stem that CURVES. Vanilla stems bow under their own leaves; a straight
    cone reads as a dowel and makes the whole plant look manufactured."""
    r1 = r0 * 0.55 if r1 is None else r1
    rings = 5
    verts, faces = [], []
    for j in range(rings + 1):
        t = j / rings
        rr = r0 + (r1 - r0) * t
        z = height * t
        # bow away from the stand's centre, plus whatever lean the caller asked for
        off = (lean + tilt) * height * t * t
        cx, cy = off * math.cos(yaw), off * math.sin(yaw)
        for k in range(sides):
            a = math.tau * k / sides
            verts.append((cx + rr * math.cos(a), cy + rr * math.sin(a), z))
    for j in range(rings):
        for k in range(sides):
            a = j * sides + k
            b = j * sides + (k + 1) % sides
            faces.append((a, b, b + sides, a + sides))
    faces.append(tuple(range(rings * sides, rings * sides + sides)))   # cap the tip
    obj = _mesh_object(name, verts, faces, mat)
    obj.location = base
    return obj


def _rib(name, mat, base, length, r0, r1, euler, sides=4):
    """The midrib rod, built along +X -- the blade's axis -- so it takes the blade's own
    (roll, pitch, yaw) and lies on the midrib whatever the roll. (A +Z rod turned with
    ``pitch + 90`` only lines up at roll 0: at the broadside roll it swung off the blade
    and its tip was the lone pixel floating above every rice culm.)"""
    rings = 3
    verts, faces = [], []
    for j in range(rings + 1):
        u = j / rings
        rr = r0 + (r1 - r0) * u
        for k in range(sides):
            a = math.tau * k / sides
            verts.append((length * u, rr * math.cos(a), rr * math.sin(a)))
    for j in range(rings):
        for k in range(sides):
            a = j * sides + k
            b = j * sides + (k + 1) % sides
            faces.append((a, b, b + sides, a + sides))
    faces.append(tuple(range(rings * sides, rings * sides + sides)))
    obj = _mesh_object(name, verts, faces, mat)
    obj.location = base
    obj.rotation_euler = euler
    return obj


def _sweep(name, mat, pts, radii, sides=5):
    """A tube swept along a polyline, with a parallel-transport-free frame.

    Every stem worth drawing is a swept CURVE. ``pzforge refsheet`` on the reference makes
    that unmissable: isolate the foliage regions of ``vegetation_farming_01_110`` and what
    is left is a drawing of curving canes and coiled tendrils, not poles. A tapered cone
    cannot be any of those, so this is the primitive the plant parts are built from.
    """
    verts, faces = [], []
    n = len(pts)
    for i, (px, py, pz) in enumerate(pts):
        j = i + 1 if i < n - 1 else i
        k = i if i < n - 1 else i - 1
        fx = pts[j][0] - pts[k][0]
        fy = pts[j][1] - pts[k][1]
        fz = pts[j][2] - pts[k][2]
        fl = math.sqrt(fx * fx + fy * fy + fz * fz) or 1.0
        fx, fy, fz = fx / fl, fy / fl, fz / fl
        ux, uy, uz = (0.0, 0.0, 1.0) if abs(fz) < 0.9 else (1.0, 0.0, 0.0)
        ax, ay, az = fy * uz - fz * uy, fz * ux - fx * uz, fx * uy - fy * ux
        al = math.sqrt(ax * ax + ay * ay + az * az) or 1.0
        ax, ay, az = ax / al, ay / al, az / al
        bx, by, bz = fy * az - fz * ay, fz * ax - fx * az, fx * ay - fy * ax
        r = radii[i]
        for s in range(sides):
            a = math.tau * s / sides
            ca, sa = math.cos(a), math.sin(a)
            verts.append((px + r * (ax * ca + bx * sa),
                          py + r * (ay * ca + by * sa),
                          pz + r * (az * ca + bz * sa)))
    for i in range(n - 1):
        for s in range(sides):
            a = i * sides + s
            b = i * sides + (s + 1) % sides
            faces.append((a, b, b + sides, a + sides))
    faces.append(tuple(range((n - 1) * sides, n * sides)))
    obj = _mesh_object(name, verts, faces, mat)
    # UVs in world units -- u round the tube, v along it -- so a UV-projected map (bark)
    # runs its grain along every limb, whatever way the limb leans
    arc = [0.0]
    for (x0, y0, z0), (x1, y1, z1) in zip(pts, pts[1:]):
        arc.append(arc[-1] + math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2))
    circ = math.tau * (sum(radii) / len(radii))
    me = obj.data
    uv = me.uv_layers.new(name="UVMap")
    nquads = (n - 1) * sides
    for fi, poly in enumerate(me.polygons):
        loops = list(poly.loop_indices)
        svals = [me.loops[li].vertex_index % sides for li in loops]
        wrap = fi < nquads and (sides - 1) in svals and 0 in svals
        for li, sv in zip(loops, svals):
            vi = me.loops[li].vertex_index
            ring = min(n - 1, vi // sides)
            su = sides if (wrap and sv == 0) else sv
            uv.data[li].uv = (su / sides * circ, arc[ring])
    return obj


def _cane_path(base, length, yaw, rise_deg, bend_deg, segs=8):
    """A cane that leaves its parent at ``rise_deg`` above horizontal and has bent to
    ``bend_deg`` by its tip -- the arc every branch in the reference is drawn along."""
    pts = [tuple(base)]
    x, y, z = base
    cy, sy = math.cos(yaw), math.sin(yaw)
    step = length / segs
    for i in range(segs):
        a = math.radians(rise_deg + (bend_deg - rise_deg) * ((i + 0.5) / segs))
        x += cy * math.cos(a) * step
        y += sy * math.cos(a) * step
        z += math.sin(a) * step
        pts.append((x, y, z))
    return pts


def _tendril(name, mat, base, length, r, yaw, turns=1.6, segs=18):
    """The coiled tip of a climbing vine.

    ``refsheet`` on Greenpeas leaves these behind when the leaf regions are masked out:
    little hooks and spirals all over the frame. They are the single strongest read that a
    crop CLIMBS, and no arrangement of straight stems supplies one.
    """
    pts = []
    cy, sy = math.cos(yaw), math.sin(yaw)
    nx, ny = -sy, cy                      # the coil's axis, across the cane
    straight = length * 0.45
    hx = base[0] + cy * straight * 0.72
    hy = base[1] + sy * straight * 0.72
    hz = base[2] + straight * 0.70
    coil = r * 9.0
    for i in range(segs + 1):
        t = i / segs
        if t < 0.45:
            s = (t / 0.45) * straight
            pts.append((base[0] + cy * s * 0.72, base[1] + sy * s * 0.72,
                        base[2] + s * 0.70))
        else:
            u = (t - 0.45) / 0.55
            a = u * math.tau * turns
            rr = coil * (1.0 - 0.42 * u)
            pts.append((hx + nx * rr * math.sin(a) + cy * rr * (1.0 - math.cos(a)) * 0.25,
                        hy + ny * rr * math.sin(a) + sy * rr * (1.0 - math.cos(a)) * 0.25,
                        hz + rr * (1.0 - math.cos(a)) * 0.55 + u * coil * 0.35))
    return _sweep(name, mat, pts, [r] * len(pts), sides=4)


def _compound_leaf(name, leaf_mats, stem_mat, base, length, yaw, rise_deg, bend_deg,
                   pairs=2, leaflet=0.10, roll_tilt=0.0, outline_mat=None, dead=False):
    """A rachis carrying opposite pairs of leaflets and one at the tip.

    This is the unit the reference actually draws. Enlarge ``vegetation_farming_01b_70``
    and no plant on it carries a single blade on a stick: each branch is a thin arc with
    three or four small leaflets hung off alternating sides and a pointed one at the end.
    Building single leaves on petioles gave the right amount of green in the wrong shape --
    the silhouette of a shrub rather than of a crop.
    """
    parts = []
    pts = _cane_path(base, length, yaw, rise_deg, bend_deg, segs=8)
    r0 = max(0.0068, length * 0.030)
    parts.append(_sweep(name + "_rachis", stem_mat, pts,
                        [r0 * (1.0 - 0.60 * i / 8.0) for i in range(9)]))
    # ALTERNATE along the rachis, never in opposite pairs at one node. Two leaflets and a
    # terminal one leaving the same point make a trefoil -- the sprite grew clover, not a
    # crop. The reference staggers them up the branch so each blade has dark either side.
    n = max(2, pairs * 2 + 1)
    for j in range(n - 1):
        tt = 0.26 + 0.64 * j / max(1, n - 2)
        idx = max(1, min(8, int(round(tt * 8))))
        px, py, pz = pts[idx]
        side = -1 if j % 2 else 1
        lyaw = yaw + side * math.radians(46.0) - math.radians(5.0)
        parts.extend(_leaf(
            f"{name}_lf{j}", leaf_mats[j % len(leaf_mats)],
            (px, py, pz), leaflet * (1.0 - 0.09 * j), lyaw,
            math.radians((-26.0 + 5.0 * j) if not dead else 40.0),
            curl=0.26, width_ratio=0.22, droop=(0.22 if dead else 0.0),
            roll=_broadside_roll(lyaw, roll_tilt),
            outline_mat=outline_mat))
    tx, ty, tz = pts[-1]
    parts.extend(_leaf(f"{name}_tip", leaf_mats[0], (tx, ty, tz), leaflet * 0.90, yaw,
                       math.radians((-30.0) if not dead else 40.0),
                       curl=0.26, width_ratio=0.22, droop=(0.22 if dead else 0.0),
                       roll=_broadside_roll(yaw, roll_tilt), outline_mat=outline_mat))
    return parts


#: The bed, measured off the seven bare vanilla beds rather than guessed at.
#:
#: Vanilla ships exactly TWO bed artworks, lightly recoloured and reused by everything
#: (BellPepper vs Cucumber differ by at most 23 per channel; Corn's bed is character-for-
#: character BellPepper's). Both are laid out the same way:
#:
#:   ridges          2 (BellPepper, Corn, Strawberry, Cucumber) or 3 (Barley, Cabbages, Carrots)
#:   duty cycle      crown / pitch = 62.9-66.1% in every one of the seven -- the single most
#:                   stable constant in the bed. Ours was 52.9% on the fruit crops (furrow 45%
#:                   too wide, so the bed read as two separate strips) and 91.2% on rice and
#:                   pineapple, whose three ridges simply touched.
#:   2-ridge         pitch 0.482 wX, crown 0.309 wX, furrow 0.172 wX; total span 0.789
#:   3-ridge         pitch 0.287 wX, crown 0.188 wX, furrow 0.100 wX; total span 0.762
#:
#: And two facts that overturn how this was being built:
#:
#: 1. **The bed is FLAT.** No vanilla bed rises more than +0.5 px (2-ridge) or +1.0 px
#:    (3-ridge) above the tile's own diamond; it sits a mean 6.5-7.4 px inside it. The mound
#:    is carried entirely by TONE. Ours poked 4.5 px up, so three rounds of making the ridge
#:    taller and steeper were pushing away from the reference, not toward it.
#: 2. **The read is one hard cross-ridge tone ramp.** Take each scanline's run of ridge
#:    pixels and subtract the mean value of its left third from its right third: all 17
#:    vanilla ridges come out between +17.6 and +51.4, median +31. Ours measured +3.4, with
#:    two ridges lit the wrong way round -- a symmetric ellipsoid presents a symmetric
#:    roll-off, so no flank ever faces the light. That one number is why the bed did not read
#:    as plowed rows, and it cannot be fixed by texture.
#:
#: The crown's profile across its width is asymmetric, measured on BellPepper 0 as mean value
#: per column: a 6-column shadow line at V 30-47 pinned against the shaded furrow edge, a
#: 5-column ramp climbing to V 82, a FLAT LIT TABLETOP 18 columns wide (55% of the crown) at
#: V 74-87, then a 4-column fall to V 54. Ours peaked at V 90 four columns in from the shaded
#: edge -- exactly where vanilla is darkest.
#:
#: So a ridge is built as four long strips, each tilted about its own length so the toon ramp
#: puts it in the band the reference paints it in, and all four kept inside ~1 px of height.
#:   (share of the crown width, tilt in radians, height above the tile plane)
RIDGE_PROFILE = (
    (0.18, +0.62, 0.004),      # shadow line, hard against the shaded edge
    (0.15, +0.22, 0.010),      # ramp
    (0.55, -0.09, 0.016),      # the flat lit tabletop, over half the crown
    (0.12, -0.46, 0.008),      # fall into the lit-side furrow
)

#: Crown centres and widths per bed variant, in world units. The furrow straddles the tile
#: centre line on a 2-ridge bed and a crown sits on it on a 3-ridge bed, as measured.
BED_LAYOUT = {
    2: ((-0.235, 0.235), 0.280),
    3: ((-0.287, 0.0, 0.287), 0.176),
}


def soil_bed(mat, rng, rows=2, length=0.84):
    """Tilled ridges. See RIDGE_PROFILE for what was measured and why this is flat strips.

    ``rng`` is ignored on purpose: vanilla paints the bed ONCE and reuses it byte for byte
    under all eight growth stages (2193 bed pixels, 0 of them ever removed by the plant,
    18 differing pixels in the front rows across the whole series). Ours re-rolled the noise
    every stage -- 0 of 1828 stage-0 bed pixels survived into stage 1 -- so the soil visibly
    churned as the crop grew. A fixed seed per bed variant reproduces vanilla's two shared
    artworks instead of eighteen crops' worth of unique ones.
    """
    import random as _random
    bed_rng = _random.Random(90210 + rows)
    parts = []
    centres, crown = BED_LAYOUT.get(rows, BED_LAYOUT[2])
    half = length * 0.5
    for i, y0 in enumerate(centres):
        # The bank: a squashed ellipsoid -- the reference's ridges TAPER to their ends
        # (compare showed vanilla-only pixels at both tips of a stadium-shaped bank and
        # ours-only along its straight flanks). Height 0.05 (~4 px): the relief is
        # tonal in the reference, and this is just enough for the crest and the
        # flanks to land on different stops of the ramp.
        bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=10, radius=0.5,
                                             location=(0.0, y0, -0.014))
        bank = bpy.context.active_object
        bank.name = f"ridge_{i}"
        bank.scale = (length, crown * 1.06, 0.10)
        bank.rotation_euler = (0.0, 0.0, 0.05 if i % 2 else -0.04)
        bank.data.materials.append(mat)
        bpy.ops.object.shade_smooth()
        parts.append(bank)
        # Edge crumbs: the reference's crown edges are straight with per-pixel white-noise
        # raggedness (residual sd 0.3-1.0 px, autocorrelation ~0 at every lag); a cut
        # strip's edge is a clean line. Small flat crumbs straddling both edges break it.
        for k in range(22):
            side = -1.0 if k % 2 else 1.0
            cx = -half + length * ((k + 0.5) / 22.0) + bed_rng.uniform(-0.01, 0.01)
            cy = y0 + side * crown * 0.5 + side * bed_rng.uniform(-0.004, 0.014)
            s = bed_rng.uniform(0.014, 0.030)
            bpy.ops.mesh.primitive_uv_sphere_add(segments=6, ring_count=4, radius=0.5,
                                                 location=(cx, cy, 0.006))
            crumb = bpy.context.active_object
            crumb.name = f"crumb_{i}_{k}"
            crumb.scale = (s * bed_rng.uniform(1.0, 2.2), s, s * 0.35)
            crumb.data.materials.append(mat)
            bpy.ops.object.shade_flat()
            parts.append(crumb)
        # Clods, segregated by side because vanilla segregates them: 90-100% of its highlight
        # pixels lie in the outer half of the crown on the lit side and 69-82% of its dark
        # pixels in the inner third on the shaded side, with almost nothing crossing over.
        # Ours put 40% of the highlights on the shadow side and 42% of the darks on the lit
        # side, which is why every ridge read as a speckled tube. A clod cannot be told what
        # colour to be, but its TILT decides which band of the ramp it lands in, so lit-side
        # clods are turned to face the light and shaded-side ones away from it.
        for k in range(26):
            lit = k % 5 != 0                     # ~80% on the lit side, as measured
            frac = (bed_rng.uniform(0.52, 0.97) if lit else bed_rng.uniform(0.02, 0.30))
            cy = y0 - crown * 0.5 + crown * frac
            cx = -half + length * ((k + 0.5) / 26.0) + bed_rng.uniform(-0.014, 0.014)
            s = bed_rng.uniform(0.026, 0.050)
            bpy.ops.mesh.primitive_uv_sphere_add(
                segments=6, ring_count=4, radius=0.5,
                location=(cx, cy + bed_rng.uniform(-0.012, 0.012),
                          0.012 + bed_rng.uniform(0.0, 0.006)))
            clod = bpy.context.active_object
            clod.name = f"clod_{i}_{k}"
            clod.scale = (s * bed_rng.uniform(1.4, 2.6), s, s * 0.38)
            clod.rotation_euler = ((-0.75 if lit else +0.85) + bed_rng.uniform(-0.15, 0.15),
                                   0.0, bed_rng.uniform(-0.35, 0.35))
            clod.data.materials.append(mat)
            bpy.ops.object.shade_flat()
            parts.append(clod)
    return parts


# --------------------------------------------------------------------------- #
# Archetypes
# --------------------------------------------------------------------------- #

def _broadside_roll(yaw, tilt=0.0):
    """The roll that turns a blade's face toward the eye.

    The blade is modelled lying flat, and a flat blade under this rig's 30 deg camera loses
    half its width to foreshortening -- which is why every leaf in the v13 sheet read as a
    dash rather than the reference's broad ovals. Rolling it about its own length swings the
    normal up: for a blade lying along the screen axis the normal reaches the camera vector
    (0.612, -0.612, 0.5) at exactly 60 deg, and the sign follows which way along that axis
    the blade points. ``tilt`` backs a leaf off from dead-on so a canopy is not a wall of
    billboards.
    """
    s = math.cos(yaw - SCREEN_YAW)
    return math.radians(58.0 + tilt) * (1.0 if s >= 0.0 else -1.0)


def _rise(stage, top_px):
    """World height for a plant that should stand ``top_px`` above the bed's silhouette at
    full growth, following the reference's own growth curve (BUSH_PLANT_PX) and carrying
    the measured BASE_DROP_PX correction for where a plant on a ridge actually projects."""
    frac = BUSH_PLANT_PX[stage] / BUSH_PLANT_PX[5]
    return ((frac * top_px + BASE_DROP_PX) if frac > 0.0 else 0.0) / PX_PER_Z


#: Measured node ladder of the reference plant, in world units (px / 78.4 vertical):
#: first leaf pair 0.22 above the soil, internode ~0.10 (7-8 px at 2x).
NODE0, INTERNODE = 0.22, 0.100
#: Which layer to build (set from the command line: ``-- apple --layer stems``). Each
#: layer is rendered and read on its own before the next goes on.
LAYER = "all"

#: THE GROWTH DESIGN, per stage, decided before any geometry. Read off
#: vegetation_farming_01b_64..71 at 6x: a pepper plant is ONE main stem that forks once
#: at mid-height into two (the middle plant: three) LEADERS, and every leaf sits on the
#: stem itself on a 1-3 px petiole. Nothing arcs out sideways -- what spreads the plant
#: is the fork, and the leaders straighten toward vertical as they climb, so each reads
#: as one smooth outward-convex curve: not a rod, not a wander.
#:   height   px above the bed silhouette (the reference's measured rise)
#:   nodes    leaf nodes on the main stem below the fork (NODE0, then INTERNODE apart)
#:   fork     where the main stem splits, as a fraction of the plant height (None: none)
#:   spread   leader tilt from vertical at the fork and at the tip, degrees
#:   carries  what the stage shows
BUSH_GROWTH = (
    dict(height=0,  nodes=0, fork=None, spread=(0, 0),   carries="bare bed"),
    dict(height=6,  nodes=0, fork=None, spread=(0, 0),   carries="a hook with two cotyledons"),
    dict(height=11, nodes=1, fork=None, spread=(0, 0),   carries="cotyledons under the first true pair"),
    dict(height=27, nodes=2, fork=None, spread=(0, 0),   carries="two leaf pairs under a spear tip"),
    dict(height=64, nodes=2, fork=0.50, spread=(22, 7),  carries="forked; flowers at the top"),
    dict(height=73, nodes=2, fork=0.46, spread=(22, 7),  carries="flowers and small fruit"),
    dict(height=73, nodes=2, fork=0.46, spread=(22, 7),  carries="ripe fruit at the middle nodes"),
    dict(height=69, nodes=2, fork=0.46, spread=(19, 15), carries="withered: leaders drooping, leaves hanging"),
)
#: The three plants are not clones. Feet sit ON the ridge crowns (two on the front
#: ridge, one on the back, where the reference's stand -- ours used to stand in the
#: furrow, which is why the bed and the plants never read as one thing). Then: the fork
#: height factor, the leader count, which leader is the tallest (-1 left, 0 middle,
#: +1 right: each plant's tallest leader leans in toward the group's centre), and the
#: main stem's settled lean in degrees.
BUSH_PLANTS = (
    dict(foot=(-0.24, -0.235), fork_k=1.00, leaders=2, tallest=+1, lean=-2.0),
    dict(foot=(0.20, -0.235),  fork_k=0.82, leaders=3, tallest=0,  lean=1.0),
    dict(foot=(0.19, 0.235),   fork_k=0.92, leaders=2, tallest=-1, lean=3.0),
)
#: Unit vectors of the picture plane: across the screen (right) and toward the viewer.
_ACROSS = (0.707, 0.707, 0.0)
_NEAR = (0.707, -0.707, 0.0)


def _axis(base, length, tilt0_deg, tilt1_deg, bow=0.0, near=0.0, segs=None):
    """A stem axis: a polyline climbing from ``base`` whose tilt from vertical (across
    the screen, signed, + = screen-right) runs from ``tilt0`` to ``tilt1`` over its
    length. ``bow`` adds one gentle half-wave across (the main stem's slight S, a
    couple of px), ``near`` drifts it toward the viewer so two leaders never share a
    depth. Points are ~0.03 apart so the sweep is smooth."""
    segs = segs or max(6, int(length / 0.03))
    pts = [tuple(base)]
    lat, z = 0.0, base[2]
    step = length / segs
    for i in range(segs):
        t = math.radians(tilt0_deg + (tilt1_deg - tilt0_deg) * ((i + 0.5) / segs))
        lat += math.sin(t) * step
        z += math.cos(t) * step
        u = (i + 1) / segs
        s = lat + bow * length * math.sin(math.pi * u)
        n = near * length * u
        pts.append((base[0] + _ACROSS[0] * s + _NEAR[0] * n,
                    base[1] + _ACROSS[1] * s + _NEAR[1] * n, z))
    return pts


def _arc_len(pts):
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def _along(pts, dist):
    """Point on a polyline at arc length ``dist`` from its start (clamped to the tip)."""
    acc = 0.0
    for a, b in zip(pts, pts[1:]):
        seg = math.dist(a, b)
        if acc + seg >= dist:
            u = 0.0 if seg == 0.0 else (dist - acc) / seg
            return tuple(a[k] + (b[k] - a[k]) * u for k in range(3))
        acc += seg
    return tuple(pts[-1])


def _taper(pts, r0, r1):
    n = max(1, len(pts) - 1)
    return [r0 + (r1 - r0) * i / n for i in range(len(pts))]


def _node_leaf(name, leaf_mats, stem_mat, at, side, rise_deg, size, rng, dead=False,
               mat_i=0, yaw_jitter=0.42):
    """One leaf at a node, the way the reference attaches every leaf: a 1-3 px petiole
    leaving the stem toward ``side`` (+1 screen-right), and a blade rising ``rise_deg``
    above horizontal, drawn broadside -- one in six turned near edge-on so the canopy is
    not a wall of billboards. The midrib is a thin rod in the stem paint."""
    parts = []
    yaw = SCREEN_YAW + (0.0 if side > 0 else math.pi) + rng.uniform(-yaw_jitter, yaw_jitter)
    pet = rng.uniform(0.018, 0.034)
    tip = (at[0] + math.cos(yaw) * pet, at[1] + math.sin(yaw) * pet, at[2] + pet * 0.35)
    parts.append(_sweep(f"{name}_pet", stem_mat, [tuple(at), tip], [0.0036, 0.0028], sides=4))
    pitch = math.radians(rng.uniform(25.0, 45.0)) if dead else -math.radians(rise_deg)
    edge_on = rng.random() < 0.20
    roll = _broadside_roll(yaw, rng.uniform(-24.0, 12.0) + (58.0 if edge_on else 0.0))
    parts.extend(_leaf(f"{name}_lf", leaf_mats[mat_i % len(leaf_mats)], tip, size, yaw, pitch,
                       curl=rng.uniform(0.04, 0.12), midrib=rng.uniform(0.25, 0.45),
                       width_ratio=rng.uniform(0.17, 0.27), droop=0.0, roll=roll))
    if not edge_on:
        parts.append(_rib(f"{name}_rib", stem_mat, tip, size * 0.82, 0.0038, 0.0020, (roll, pitch, yaw)))
    return parts


def _spear(name, leaf_mats, stem_mat, tip, size, rng, dead=False):
    """The apex of every leader: a pair of small blades pointing up and apart -- the
    little spear the reference tops each stem with."""
    parts = []
    for k, side in enumerate((-1, 1)):
        parts.extend(_node_leaf(f"{name}_{k}", leaf_mats, stem_mat, tip, side,
                                rng.uniform(56.0, 68.0), size, rng, dead=dead, mat_i=k + 1,
                                yaw_jitter=0.18))
    return parts


#: THE FRUIT DESIGN. A fruit is not a ball: each crop's produce has a silhouette that
#: has to read at 12 px, and ``profile`` IS that silhouette -- (z, radius) pairs in units
#: of r, bottom to top, spun into a surface of revolution. The other terms: ``height`` /
#: ``width`` scale the axis and the girth, ``crease`` sinks a suture groove down the face
#: toward the viewer (peach), ``shear`` bows the axis across the screen (a mango's
#: kidney), ``cap`` puts the green calyx button on top at that fraction of r (``cap_z`` is
#: where, in profile units), ``stalk`` is how far below its node the fruit hangs,
#: ``style`` how the crop carries it (hang / pair / cluster / pod), ``gloss`` the
#: highlight the skin takes (a waxy apple, a matte peach). Every crop reads as its own
#: fruit from these alone; the colour is the crop's paint from CROPS.
_SPHERE = ((-1.0, 0.0), (-0.85, 0.55), (-0.5, 0.87), (0.0, 1.0), (0.5, 0.87), (0.85, 0.55), (1.0, 0.0))
FRUIT_SHAPES = {
    "sphere":  dict(profile=_SPHERE, stalk=0.012, gloss=(0.12, 0.55)),
    # oblate, dimpled at both ends, the stalk sunk in a well
    "apple":   dict(profile=((-0.72, 0.0), (-0.88, 0.38), (-0.72, 0.74), (-0.32, 0.97), (0.10, 1.0),
                             (0.50, 0.90), (0.78, 0.62), (0.90, 0.32), (0.70, 0.0)),
                    height=0.95, stalk=0.012, cap=0.26, cap_z=0.80, gloss=(0.14, 0.50)),
    # pyriform: full below, narrowing into a long neck
    "pear":    dict(profile=((-1.0, 0.0), (-0.90, 0.50), (-0.60, 0.92), (-0.20, 1.0), (0.15, 0.88),
                             (0.40, 0.62), (0.60, 0.50), (0.85, 0.50), (1.05, 0.42), (1.22, 0.26),
                             (1.30, 0.0)),
                    height=1.30, width=0.92, stalk=0.016, gloss=(0.10, 0.55)),
    # round with the suture crease and a slight point below; fuzzy skin
    "peach":   dict(profile=((-1.06, 0.0), (-0.92, 0.48), (-0.55, 0.88), (0.0, 1.0), (0.50, 0.90),
                             (0.84, 0.58), (0.98, 0.24), (0.86, 0.0)),
                    crease=0.30, stalk=0.010, cap=0.22, cap_z=0.86, gloss=(0.03, 0.75)),
    # small, dimpled on top, two on long stalks from one node
    "cherry":  dict(profile=((-1.0, 0.0), (-0.86, 0.55), (-0.45, 0.90), (0.0, 1.0), (0.50, 0.90),
                             (0.82, 0.60), (0.92, 0.30), (0.76, 0.0)),
                    style="pair", stalk=0.062, gloss=(0.18, 0.42)),
    # slightly oblate, a tiny button on top
    "citrus":  dict(profile=_SPHERE, height=0.92, stalk=0.010, cap=0.16, cap_z=0.92, gloss=(0.10, 0.60)),
    # the pointed ellipsoid with a nipple at each end
    "lemon":   dict(profile=((-1.28, 0.0), (-1.08, 0.20), (-0.86, 0.56), (-0.45, 0.92), (0.0, 1.0),
                             (0.45, 0.92), (0.86, 0.56), (1.08, 0.20), (1.28, 0.0)),
                    width=0.82, stalk=0.012, gloss=(0.10, 0.60)),
    "lime":    dict(profile=((-1.12, 0.0), (-0.96, 0.32), (-0.60, 0.82), (0.0, 1.0), (0.60, 0.82),
                             (0.96, 0.32), (1.12, 0.0)),
                    width=0.90, stalk=0.012, gloss=(0.10, 0.60)),
    # the egg drawn out into a neck, on a long stalk
    "avocado": dict(profile=((-1.0, 0.0), (-0.88, 0.55), (-0.50, 0.92), (-0.05, 1.0), (0.30, 0.92),
                             (0.60, 0.80), (0.90, 0.68), (1.20, 0.52), (1.45, 0.32), (1.60, 0.14),
                             (1.66, 0.0)),
                    height=1.10, width=0.92, stalk=0.040, gloss=(0.04, 0.70)),
    # an oval bowed into a kidney, hanging tilted on a long stalk
    "mango":   dict(profile=((-1.0, 0.0), (-0.80, 0.56), (-0.40, 0.90), (0.0, 1.0), (0.40, 0.96),
                             (0.80, 0.74), (1.06, 0.46), (1.22, 0.0)),
                    height=1.22, shear=0.42, tilt=22.0, stalk=0.050, gloss=(0.08, 0.60)),
    # small prolate ellipsoids, a few to a node
    "olive":   dict(profile=((-1.0, 0.0), (-0.86, 0.50), (-0.40, 0.90), (0.0, 1.0), (0.40, 0.90),
                             (0.86, 0.50), (1.0, 0.0)),
                    height=1.45, width=0.88, style="cluster", per=3, stalk=0.014, gloss=(0.06, 0.60)),
    # coffee cherries: a ring of berries hugging the stem at the node
    "coffee":  dict(profile=_SPHERE, style="cluster", per=5, stalk=0.0, gloss=(0.14, 0.50)),
    # the two-lobed pod, lying at the foot of the plant
    "peanut":  dict(profile=((-1.35, 0.0), (-1.20, 0.46), (-0.95, 0.76), (-0.55, 0.82), (-0.15, 0.56),
                             (0.20, 0.62), (0.60, 0.86), (0.95, 0.76), (1.20, 0.42), (1.35, 0.0)),
                    width=0.85, style="pod", gloss=(0.02, 0.80)),
    # the barrel with eight ribs; the crown is built separately
    "pineapple": dict(profile=((-1.0, 0.0), (-0.92, 0.66), (-0.55, 0.94), (0.0, 1.0), (0.55, 0.94),
                               (0.92, 0.72), (1.0, 0.0)),
                      height=1.40, lobes=8, lobe_amp=0.07, gloss=(0.06, 0.60)),
    "grain":     dict(profile=_SPHERE, gloss=(0.10, 0.6)),
    "banana":    dict(profile=_SPHERE, gloss=(0.10, 0.6)),
    "grape":     dict(profile=_SPHERE, gloss=(0.16, 0.45)),
    # the banana bract: a pointed bud, hung point-down
    "bud":       dict(profile=((-1.0, 0.0), (-0.8, 0.55), (-0.3, 0.9), (0.2, 0.86), (0.7, 0.55), (1.1, 0.25), (1.3, 0.0)),
                      height=1.1, gloss=(0.05, 0.6)),
    # a ginger hand: knobbly, lying at the foot
    "rhizome":   dict(profile=((-1.3, 0.0), (-1.1, 0.5), (-0.8, 0.7), (-0.4, 0.55), (0.0, 0.75), (0.4, 0.6),
                               (0.8, 0.8), (1.1, 0.5), (1.3, 0.0)),
                      width=0.8, gloss=(0.03, 0.8)),
}
#: the meridian that faces the camera, where a crease is cut
_CREASE_AT = math.atan2(_NEAR[1], _NEAR[0])


def _cut_profile(prof, zc, keep):
    """The part of a profile below (keep="below") or above (keep="above") height zc, the
    cut ring interpolated in -- two halves that meet on one ring make a two-tone fruit."""
    out = []
    for (z0, r0), (z1, r1) in zip(prof, prof[1:]):
        inside0 = z0 <= zc if keep == "below" else z0 >= zc
        if inside0:
            out.append((z0, r0))
        if (z0 - zc) * (z1 - zc) < 0:
            u = (zc - z0) / (z1 - z0)
            out.append((zc, r0 + (r1 - r0) * u))
    zl, rl = prof[-1]
    if (zl <= zc if keep == "below" else zl >= zc):
        out.append((zl, rl))
    return tuple(out)


def _lathe(name, mat, loc, r, shape, k=1.0, yaw=0.0, tilt=0.0, sides=14, cut=None):
    """A fruit body spun from its profile (see FRUIT_SHAPES). Poles are single vertices
    fanned to their ring so no face is degenerate; winding is outward throughout.
    ``cut=(zc, "below"|"above")`` spins only that part of the profile."""
    prof = shape["profile"] if cut is None else _cut_profile(shape["profile"], *cut)
    hk = shape.get("height", 1.0) * r * k
    wk = shape.get("width", 1.0) * r * k
    crease = shape.get("crease", 0.0)
    shear = shape.get("shear", 0.0)
    verts, faces, rings = [], [], []
    for z, rad in prof:
        zz = z * hk
        sx = shear * z * z * r * k
        if rad <= 1e-6:
            rings.append((len(verts), 1))
            verts.append((_ACROSS[0] * sx, _ACROSS[1] * sx, zz))
            continue
        start = len(verts)
        for s in range(sides):
            a = math.tau * s / sides
            g = 1.0
            if crease:
                da = (a - _CREASE_AT + math.pi) % math.tau - math.pi
                g = 1.0 - crease * math.exp(-(da / 0.30) ** 2)
            rr = rad * wk * g * (1.0 + shape.get("lobe_amp", 0.0) * math.cos(shape.get("lobes", 0) * a))
            verts.append((math.cos(a) * rr + _ACROSS[0] * sx, math.sin(a) * rr + _ACROSS[1] * sx, zz))
        rings.append((start, sides))
    for (a0, n0), (a1, n1) in zip(rings, rings[1:]):
        if n0 == 1 and n1 == 1:
            continue
        if n0 == 1:
            for s in range(n1):
                faces.append((a0, a1 + (s + 1) % n1, a1 + s))
        elif n1 == 1:
            for s in range(n0):
                faces.append((a0 + s, a0 + (s + 1) % n0, a1))
        else:
            for s in range(n0):
                faces.append((a0 + s, a0 + (s + 1) % n0, a1 + (s + 1) % n0, a1 + s))
    obj = _mesh_object(name, verts, faces, mat)
    obj.location = loc
    obj.rotation_euler = (tilt, 0.0, yaw)
    return obj


#: where a fruit's highlight sits: upper left on the sprite and toward the eye
_GLEAM_DIR = tuple(v / (sum(w * w for w in (-0.45 * 0.707 - 0.354 * 0.55 + 0.612 * 0.70,
                                             -0.45 * 0.707 + 0.354 * 0.55 - 0.612 * 0.70,
                                             0.866 * 0.55 + 0.5 * 0.70)) ** 0.5)
                   for v in (-0.45 * 0.707 - 0.354 * 0.55 + 0.612 * 0.70,
                             -0.45 * 0.707 + 0.354 * 0.55 - 0.612 * 0.70,
                             0.866 * 0.55 + 0.5 * 0.70))


def _fruit(name, mats, loc, r, shape_name, rng, alt=False, k=1.0, lying=False, blush=None, gleam=False):
    """One fruit of the crop's shape at ``loc`` (its centre), with the drawn rim behind
    it (the fruit's own dark, pushed back along the view axis so it shows as a 1 px
    outline) and the calyx button where the shape has one. ``blush=(material, zc)``
    paints the fruit above profile height zc in a second colour (mango, peach)."""
    shape = FRUIT_SHAPES[shape_name]
    if lying:
        yaw, tilt = rng.uniform(0.0, math.tau), math.radians(72.0 + rng.uniform(-10.0, 10.0))
    else:
        yaw = rng.uniform(-0.25, 0.25) + (0.0 if shape.get("crease") else rng.uniform(-1.2, 1.2))
        tilt = math.radians(shape.get("tilt", 0.0) + rng.uniform(-6.0, 6.0))
    parts = [_lathe(name + "_edge", mats["fruit_edge"],
                    (loc[0] + _AWAY[0] * r * 0.30, loc[1] + _AWAY[1] * r * 0.30, loc[2] + _AWAY[2] * r * 0.30),
                    r, shape, k=1.16 * k, yaw=yaw, tilt=tilt),
             ]
    body = mats["fruit_alt" if alt else "fruit"]
    if blush is None or lying:
        parts.append(_lathe(name, body, loc, r, shape, k=k, yaw=yaw, tilt=tilt))
    else:
        bmat, zc = blush
        parts.append(_lathe(name, body, loc, r, shape, k=k, yaw=yaw, tilt=tilt, cut=(zc, "below")))
        parts.append(_lathe(name + "_blush", bmat, loc, r, shape, k=k, yaw=yaw, tilt=tilt, cut=(zc, "above")))
    if gleam and "gleam" in mats:
        # the painted fruit's highlight: a 1-2 px pale dot on the upper-left front. A
        # glossy lobe cannot draw it on a saturated paint (it tints red, or washes the
        # whole fruit when added as white), so it is a small pale chip sitting on the skin
        g = r * k * 0.92
        bpy.ops.mesh.primitive_uv_sphere_add(segments=8, ring_count=5, radius=1.0,
                                             location=(loc[0] + _GLEAM_DIR[0] * g, loc[1] + _GLEAM_DIR[1] * g,
                                                       loc[2] + _GLEAM_DIR[2] * g))
        chip = bpy.context.active_object
        chip.name = name + "_gleam"
        chip.scale = (r * k * 0.30, r * k * 0.30, r * k * 0.24)
        chip.data.materials.append(mats["gleam"])
        bpy.ops.object.shade_smooth()
        parts.append(chip)
    if shape.get("cap") and not lying:
        cz = loc[2] + shape.get("cap_z", 1.0) * shape.get("height", 1.0) * r * k
        cap = _berry(name + "_cap", mats["calyx"], (loc[0], loc[1], cz), r * shape["cap"] * k, dimple=0.0)[0]
        cap.scale = (1.0, 1.0, 0.55)
        parts.append(cap)
    return parts


def _fruit_top(shape_name, r, k=1.0):
    """How far a fruit's top rises above its centre (to hang it below a node)."""
    shape = FRUIT_SHAPES[shape_name]
    return max(z for z, _ in shape["profile"]) * shape.get("height", 1.0) * r * k


def bush(stage, spec, mats, rng):
    """The BellPepper habit, built in layers: design -> stems -> leaves -> fruit.

    Every part hangs off ONE skeleton: the main stem, its fork, and the leaders. Read
    BUSH_GROWTH / BUSH_PLANTS for the design; this function only lays it down.
    """
    parts = []
    rise = BUSH_PLANT_PX[stage]
    h = (((rise + BASE_DROP_PX) if rise > 0.0 else 0.0) / PX_PER_Z
         * spec.get("height_scale", 1.0))
    if h <= 0.001:
        return parts
    design = BUSH_GROWTH[stage]
    leaf_k = spec.get("leaf", 1.0)
    dead = stage == STAGES - 1
    stem_mat = mats["dead"] if dead else mats["stem"]
    leaf_mats = [mats["dead"]] if dead else mats["leaves"]
    want_leaves = LAYER in ("leaves", "all")
    want_fruit = LAYER == "all"
    for p, plant in enumerate(BUSH_PLANTS):
        px, py = plant["foot"]
        ph = h * (0.94 + 0.10 * rng.random())
        maturity = min(1.0, ph / 0.86)
        leaf_size = 0.165 * leaf_k * (0.84 + 0.16 * maturity)
        base = (px, py, 0.015)
        r0 = 0.0122 * min(1.0, 0.55 + ph * 0.6)
        # --- LAYER 2a: the main stem, to the fork (or the tip) --------------------------
        fork = design["fork"]
        zf = ph * fork * plant["fork_k"] if fork else ph
        if stage == 1:
            # the seedling hook: leaves the soil upright and curls over at the tip
            main = _axis(base, zf, plant["lean"], plant["lean"] + 48.0, bow=0.0)
        else:
            main = _axis(base, zf, plant["lean"] - 1.5, plant["lean"] + 1.5, bow=0.035,
                         near=0.0)
        parts.append(_sweep(f"stem_{p}", stem_mat, main, _taper(main, r0, r0 * (0.72 if fork else 0.45)), sides=6))
        for c in range(3):
            aa = c * math.tau / 3.0 + 0.5
            bpy.ops.mesh.primitive_uv_sphere_add(segments=6, ring_count=4, radius=0.5,
                                                 location=(px + math.cos(aa) * 0.026,
                                                           py + math.sin(aa) * 0.026, 0.028))
            collar = bpy.context.active_object
            collar.name = f"collar_{p}_{c}"
            collar.scale = (0.022, 0.018, 0.014)
            collar.rotation_euler = (0.0, 0.0, aa)
            collar.data.materials.append(mats["soil"])
            bpy.ops.object.shade_flat()
            parts.append(collar)
        # --- LAYER 2b: the leaders, from the fork --------------------------------------
        leaders = []                       # (pts, side, is_tallest)
        if fork:
            top = main[-1]
            parts.extend(_berry(f"fork_{p}", mats["calyx"], top, r0 * 0.95, dimple=0.0))
            t0, t1 = design["spread"]
            sides = (-1, 1) if plant["leaders"] == 2 else (-1, 0, 1)
            run = ph - zf
            for k, side in enumerate(sides):
                tallest = side == plant["tallest"]
                if side == 0:
                    a0, a1, near = 5.0, 1.0, 0.05
                else:
                    a0 = side * (t0 + rng.uniform(-3.0, 3.0))
                    a1 = side * (t1 + rng.uniform(-2.0, 2.0))
                    near = -0.025 * side
                mean = math.radians(abs(a0 + a1) * 0.5)
                length = run / max(0.3, math.cos(mean)) * (1.0 if tallest else rng.uniform(0.80, 0.90))
                pts = _axis(top, length, a0, a1, bow=0.0, near=near)
                parts.append(_sweep(f"leader_{p}_{k}", stem_mat, pts,
                                    _taper(pts, r0 * 0.72, r0 * 0.36), sides=5))
                leaders.append((pts, side, tallest))
        # --- LAYER 3: leaves, at the nodes of the skeleton -----------------------------
        nodes = []                         # (point, side, rise, size, on_leader, z-frac)
        n_main = design["nodes"]
        main_len = _arc_len(main)
        for i in range(n_main):
            d = NODE0 + i * INTERNODE
            if d > main_len - 0.03:
                break
            at = _along(main, d)
            if i == 0 or not fork:
                # the low pair: near horizontal, the lowest one drooping a little
                for side in (-1, 1):
                    nodes.append((at, side, rng.uniform(-10.0, 12.0) + 10.0 * i,
                                  leaf_size * rng.uniform(0.95, 1.20), False, d / ph))
            else:
                nodes.append((at, -plant["tallest"] or 1, rng.uniform(8.0, 22.0),
                              leaf_size * rng.uniform(0.95, 1.15), False, d / ph))
        for pts, side, tallest in leaders:
            L = _arc_len(pts)
            d, j = 0.07, 0
            while d < L - 0.04:
                at = _along(pts, d)
                # strict alternation, the outward side first; the internode shortens
                # toward the apex, where the reference crowds its smaller leaves
                s = (side or 1) * (1 if j % 2 == 0 else -1)
                frac = d / L
                rise = 10.0 + 42.0 * frac + rng.uniform(-12.0, 12.0)
                size = leaf_size * rng.uniform(0.78, 1.22) * (1.0 - 0.25 * frac)
                nodes.append((at, s, rise, size, True, at[2] / ph))
                d += INTERNODE * (1.0 - 0.32 * frac)
                j += 1
        if want_leaves:
            for k, (at, side, rise, size, on_leader, zfrac) in enumerate(nodes):
                if dead and k % 2 == 1:
                    continue               # the withered reference has shed half its leaves
                parts.extend(_node_leaf(f"leaf_{p}_{k}", leaf_mats, stem_mat, at, side, rise,
                                        size * (0.85 if dead else 1.0), rng, dead=dead, mat_i=k))
            if stage == 1:
                for s in (-1.0, 1.0):
                    cyaw = SCREEN_YAW + s * 1.25
                    tip = main[-1]
                    parts.extend(_leaf(f"cot_{p}_{int(s > 0)}", leaf_mats[0],
                                       (tip[0] + math.cos(cyaw) * 0.012, tip[1] + math.sin(cyaw) * 0.012, tip[2]),
                                       leaf_size * 0.55, cyaw, math.radians(-25.0), curl=0.25,
                                       width_ratio=0.34, roll=_broadside_roll(cyaw)))
            elif stage == 2:
                for s in (-1.0, 1.0):
                    cyaw = SCREEN_YAW + s * 1.25
                    at = _along(main, main_len * 0.45)
                    parts.extend(_leaf(f"cot_{p}_{int(s > 0)}", leaf_mats[1],
                                       (at[0] + math.cos(cyaw) * 0.012, at[1] + math.sin(cyaw) * 0.012, at[2]),
                                       leaf_size * 0.50, cyaw, math.radians(-10.0), curl=0.25,
                                       width_ratio=0.34, roll=_broadside_roll(cyaw)))
            spear_k = 0.62 if fork else 0.72
            if leaders:
                for pts, side, tallest in leaders:
                    parts.extend(_spear(f"spear_{p}_{side}", leaf_mats, stem_mat, pts[-1],
                                        leaf_size * spear_k, rng, dead=dead))
            elif stage >= 2:
                parts.extend(_spear(f"spear_{p}", leaf_mats, stem_mat, main[-1],
                                    leaf_size * spear_k, rng, dead=dead))
        if not want_fruit or dead:
            continue
        # --- LAYER 4: flowers and fruit, hung from the nodes ---------------------------
        fl = FLOWER_BY_STAGE[stage]
        if fl > 0.02 and leaders:
            for pts, side, tallest in leaders:
                for q in range(2 if fl > 0.8 else 1):
                    d = _arc_len(pts) - 0.05 - q * INTERNODE * 0.9
                    at = _along(pts, d)
                    s = (side or 1) * (1 if q % 2 else -1)
                    parts.extend(_berry(f"flower_{p}_{side}_{q}", mats["flower"],
                                        (at[0] + _ACROSS[0] * s * 0.035 + _NEAR[0] * 0.02,
                                         at[1] + _ACROSS[1] * s * 0.035 + _NEAR[1] * 0.02,
                                         at[2] + 0.012), 0.022, dimple=0.0))
        fr = FRUIT_BY_STAGE[stage]
        if fr > 0.02:
            shape_name = spec.get("shape", "sphere")
            shape = FRUIT_SHAPES[shape_name]
            style = shape.get("style", "hang")
            r = spec.get("fruit_r", 0.078) * 0.85 * spec.get("fruit_scale", 1.0) * (0.45 + 0.55 * fr)
            if style == "pod":
                # peanut: the pods set at the foot of each stem, lying on the ridge
                for q in range(4):
                    aa = _CREASE_AT + (q - 1.5) * 0.9 + rng.uniform(-0.25, 0.25)
                    rad = 0.045 + 0.02 * (q % 2)
                    parts.extend(_fruit(f"pod_{p}_{q}", mats,
                                        (px + math.cos(aa) * rad + _NEAR[0] * 0.03,
                                         py + math.sin(aa) * rad + _NEAR[1] * 0.03, 0.034 + r * 0.6),
                                        r, shape_name, rng, alt=q % 2 == 1, lying=True))
                continue
            cands = [(at, side, zfrac) for (at, side, rise, size, on_leader, zfrac) in nodes
                     if 0.28 <= zfrac <= 0.82]
            cands.sort(key=lambda c: c[2])
            want = min(len(cands), max(1, int(round(spec.get("fruit_n", 3) * (0.45 + 0.55 * fr)))))
            # spread the fruit over the height band, never two on one node
            picks = [cands[int((k + 0.5) * len(cands) / want)] for k in range(want)]
            top = _fruit_top(shape_name, r)
            for k2, (at, side, zfrac) in enumerate(picks):
                s = -side                      # opposite the node's leaf, in front of the stem
                if style == "pair":
                    # two stalks from one node, diverging, each with its cherry
                    for q, sq in enumerate((s, -s)):
                        hx = at[0] + _ACROSS[0] * sq * 0.028 + _NEAR[0] * 0.03
                        hy = at[1] + _ACROSS[1] * sq * 0.028 + _NEAR[1] * 0.03
                        hz = at[2] - shape["stalk"] * rng.uniform(0.85, 1.1)
                        parts.append(_sweep(f"pedicel_{p}_{k2}_{q}", mats["calyx"], [tuple(at), (hx, hy, hz + top * 0.8)],
                                            [0.0032, 0.0026], sides=4))
                        rk = r * rng.uniform(0.90, 1.08)
                        parts.extend(_fruit(f"fruit_{p}_{k2}_{q}", mats, (hx, hy, hz - top * rk / r), rk,
                                            shape_name, rng, alt=(k2 + q + p) % 2 == 1))
                elif style == "cluster":
                    # berries crowded round the node, hugging the stem
                    per = shape.get("per", 3)
                    for q in range(per):
                        aa = _CREASE_AT + (q - (per - 1) / 2.0) * (2.4 / per) + rng.uniform(-0.2, 0.2)
                        rad = r * 1.15
                        hx = at[0] + math.cos(aa) * rad
                        hy = at[1] + math.sin(aa) * rad
                        hz = at[2] - shape["stalk"] + r * rng.uniform(-0.9, 0.5)
                        if shape["stalk"] > 0.0:
                            parts.append(_sweep(f"pedicel_{p}_{k2}_{q}", mats["calyx"], [tuple(at), (hx, hy, hz + top * 0.7)],
                                                [0.0028, 0.0022], sides=4))
                        parts.extend(_fruit(f"fruit_{p}_{k2}_{q}", mats, (hx, hy, hz), r * rng.uniform(0.85, 1.1),
                                            shape_name, rng, alt=(k2 + q) % 2 == 1))
                else:
                    hx = at[0] + _ACROSS[0] * s * r * 0.55 + _NEAR[0] * 0.035
                    hy = at[1] + _ACROSS[1] * s * r * 0.55 + _NEAR[1] * 0.035
                    rk = r * rng.uniform(0.88, 1.08)
                    hz = at[2] - shape["stalk"] - top * rk / r
                    parts.append(_sweep(f"pedicel_{p}_{k2}", mats["calyx"], [tuple(at), (hx, hy, hz + top * rk / r * 0.85)],
                                        [0.0040, 0.0032], sides=4))
                    parts.extend(_fruit(f"fruit_{p}_{k2}", mats, (hx, hy, hz), rk, shape_name, rng,
                                        alt=(k2 + p) % 2 == 1))
            if fr < 0.8 and leaders and style == "hang":
                # the mature-but-not-full stage: a cluster of small set fruit at the top
                pts = max(leaders, key=lambda l: l[2])[0]
                for q in range(3):
                    at = _along(pts, _arc_len(pts) - 0.10 - q * 0.05)
                    s = 1 if q % 2 else -1
                    parts.extend(_fruit(f"setfruit_{p}_{q}", mats,
                                        (at[0] + _ACROSS[0] * s * 0.03 + _NEAR[0] * 0.03,
                                         at[1] + _ACROSS[1] * s * 0.03 + _NEAR[1] * 0.03,
                                         at[2] - 0.03), r, shape_name, rng, alt=True, k=0.45))
    return parts


def _at_height(pts, z):
    """(x, y) of a stem path at height z, interpolated between its points."""
    for (x0, y0, z0), (x1, y1, z1) in zip(pts, pts[1:]):
        if z0 <= z <= z1:
            u = 0.0 if z1 == z0 else (z - z0) / (z1 - z0)
            return x0 + (x1 - x0) * u, y0 + (y1 - y0) * u
    return pts[-1][0], pts[-1][1]


def _blade(name, mat, stem_mat, base, length, yaw, rise_deg, rng, width_ratio=0.06,
           curl=0.70, tilt=0.0, rib=True, dead=False):
    """A grass blade: leaves its culm rising ``rise_deg`` and arches over toward the tip
    (the curl works in the blade's own frame, so a steep blade bends forward and down,
    the way every blade in the Barley and Corn sheets is drawn). A midrib rod in the
    stem paint gives it the one interior line the reference paints."""
    parts = []
    pitch = -math.radians(rise_deg) if not dead else math.radians(10.0 + rng.uniform(0.0, 25.0))
    roll = _broadside_roll(yaw, tilt)
    parts.extend(_leaf(name, mat, base, length, yaw, pitch, curl=curl, midrib=0.30,
                       width_ratio=width_ratio, roll=roll))
    if rib:
        parts.append(_rib(f"{name}_rib", stem_mat, base, length * 0.70, 0.0034, 0.0016, (roll, pitch, yaw)))
    return parts


def _palmate(name, mat, stem_mat, at, size, yaw, rise_deg, rng, dead=False):
    """A vine leaf: three blades from one petiole -- the lobed outline of a grape leaf
    at 12 px, where a single oval reads as a coin."""
    parts = []
    pet = rng.uniform(0.014, 0.026)
    tip = (at[0] + math.cos(yaw) * pet, at[1] + math.sin(yaw) * pet, at[2] + pet * 0.3)
    parts.append(_sweep(f"{name}_pet", stem_mat, [tuple(at), tip], [0.0030, 0.0024], sides=4))
    pitch = math.radians(rng.uniform(20.0, 40.0)) if dead else -math.radians(rise_deg)
    for j, (dy, sk) in enumerate(((0.0, 1.0), (0.62, 0.74), (-0.62, 0.74))):
        y2 = yaw + dy
        parts.extend(_leaf(f"{name}_{j}", mat, tip, size * sk, y2, pitch, curl=0.12,
                           midrib=0.30, width_ratio=0.30,
                           roll=_broadside_roll(y2, rng.uniform(-20.0, 10.0))))
    return parts


# --------------------------------------------------------------------------- #
# Grain (rice) -- the Barley habit
# --------------------------------------------------------------------------- #
#: Measured rise above the bed silhouette, vegetation_farming_01b_0..7, px at 2x.
GRAIN_PX = (0, 11, 15, 35, 57, 87, 120, 81)
#: THE GROWTH DESIGN. Read at 6x: nine culms in three sown rows. A culm is a single
#: near-vertical straw with 3-4 long narrow blades leaving it at nodes on alternate
#: sides and arching over, and at c5+ a PANICLE on top -- a neck that bends over into
#: a fan of five to seven spikelets strung with grains. Tufts (c1-c2) are blades only;
#: the culm shows from c3 and reaches full height at c5; c6 is ripe straw; c7 dry.
#:   blades  per culm;  culm  visible fraction;  panicle  none / green / ripe / dry
GRAIN_GROWTH = (
    dict(blades=0, culm=0.00, panicle=None),
    dict(blades=3, culm=0.00, panicle=None),
    dict(blades=4, culm=0.00, panicle=None),
    dict(blades=4, culm=0.35, panicle=None),
    dict(blades=4, culm=0.75, panicle=None),
    dict(blades=4, culm=1.00, panicle="green"),
    dict(blades=4, culm=1.00, panicle="ripe"),
    dict(blades=3, culm=1.00, panicle="dry"),
)


def grain(stage, spec, mats, rng):
    parts = []
    rise = GRAIN_PX[stage]
    h = (rise + BASE_DROP_PX) / PX_PER_Z if rise > 0 else 0.0
    if h <= 0.001:
        return parts
    design = GRAIN_GROWTH[stage]
    dead = stage == STAGES - 1
    ripe = stage == 6
    leaf_mats = mats["straw_dry"] if dead else (mats["straw"] if ripe else mats["leaves"])
    stem_mat = mats["straw_dry"][2] if dead else (mats["strawstem"] if ripe else mats["stem"])
    grain_mat = mats["straw_dry"][1] if dead else (mats["fruit"] if ripe else mats["unripe"])
    want_leaves = LAYER in ("leaves", "all")
    want_fruit = LAYER == "all"
    for i, y in enumerate((-0.287, 0.0, 0.287)):
        for k in range(3):
            x = -0.25 + k * 0.25 + (0.07 if i == 1 else 0.0) + rng.uniform(-0.02, 0.02)
            ph = h * rng.uniform(0.90, 1.10)
            # --- LAYER 2: the culm ------------------------------------------------------
            zc = ph * (0.18 + 0.82 * design["culm"])
            lean = rng.uniform(-3.0, 3.0)
            culm = _axis((x, y, 0.015), zc, lean, lean + rng.uniform(-2.0, 2.0), bow=0.02)
            parts.append(_sweep(f"culm_{i}_{k}", stem_mat, culm,
                                _taper(culm, 0.0105, 0.0060), sides=5))
            # --- LAYER 3: blades at the nodes --------------------------------------------
            n = design["blades"]
            if want_leaves:
                L = min(0.46, max(0.14, 0.36 * ph))
                for b in range(n):
                    frac = (0.15, 0.40, 0.62, 0.82)[b] if n == 4 else (0.15, 0.45, 0.75)[b]
                    at = _along(culm, _arc_len(culm) * frac)
                    side = 1 if (b + k) % 2 == 0 else -1
                    if (k == 2 and side > 0 or k == 0 and side < 0) and rng.random() < 0.7:
                        side = -side       # edge culms keep their blades inside the tile
                    yaw = SCREEN_YAW + (0.0 if side > 0 else math.pi) + rng.uniform(-0.55, 0.55)
                    parts.extend(_blade(f"blade_{i}_{k}_{b}", leaf_mats[(b + k) % len(leaf_mats)],
                                        stem_mat, at, L * rng.uniform(0.80, 1.10), yaw,
                                        48.0 + 8.0 * b + rng.uniform(-8.0, 8.0), rng,
                                        width_ratio=0.040, curl=rng.uniform(0.45, 0.70),
                                        tilt=rng.uniform(-20.0, 10.0), dead=dead))
            # --- LAYER 4: the panicle -----------------------------------------------------
            if want_fruit and design["panicle"]:
                dry = design["panicle"] == "dry"
                top = culm[-1]
                nyaw = SCREEN_YAW + (math.pi if k % 2 else 0.0) + rng.uniform(-0.5, 0.5)
                neck = _cane_path(top, 0.06, nyaw, 82.0, 40.0 if not dry else 5.0, segs=4)
                parts.append(_sweep(f"neck_{i}_{k}", stem_mat, neck, _taper(neck, 0.0075, 0.0055), sides=4))
                end = neck[-1]
                ns = 6
                for j in range(ns):
                    u = (j - (ns - 1) / 2.0) / ((ns - 1) / 2.0)
                    syaw = nyaw + u * 0.95 + rng.uniform(-0.12, 0.12)
                    r0 = 50.0 - 22.0 * abs(u) - (25.0 if dry else 0.0)
                    r1 = r0 - 45.0
                    sp = _cane_path(end, rng.uniform(0.15, 0.21), syaw, r0, r1, segs=4)
                    parts.append(_sweep(f"spike_{i}_{k}_{j}", grain_mat, sp, _taper(sp, 0.0100, 0.0052), sides=5))
                    for g in range(3):
                        gp = sp[1 + g] if 1 + g < len(sp) else sp[-1]
                        parts.extend(_berry(f"grain_{i}_{k}_{j}_{g}", grain_mat,
                                            (gp[0], gp[1], gp[2] - 0.003), 0.0115, dimple=0.0))
    return parts


# --------------------------------------------------------------------------- #
# Broadleaf (banana) -- the Corn habit
# --------------------------------------------------------------------------- #
#: Measured rise, vegetation_farming_01_72..79 (Corn), px at 2x. c1 is the reference's
#: bare bed; ours shows a sprout.
BROADLEAF_PX = (0, 6, 14, 48, 93, 156, 183, 170)
#: THE GROWTH DESIGN. Corn at 6x is a tall straight stalk with long blades leaving it
#: on alternate sides in a ladder and arching down, and a tassel on top. A banana keeps
#: that build: one thick pseudostem, and the paddles emerge from its top and fan down
#: the top half in a ladder -- lowest largest and near horizontal, top smallest and
#: steep. The bunch hangs from the throat on a peduncle from c5, with the bract below.
#:   leaves  in the crown;  bunch  0 / 0.5 (small green hands) / 1 (full)
BROADLEAF_GROWTH = (
    dict(leaves=0, bunch=0.0),
    dict(leaves=2, bunch=0.0),
    dict(leaves=3, bunch=0.0),
    dict(leaves=5, bunch=0.0),
    dict(leaves=7, bunch=0.0),
    dict(leaves=8, bunch=0.5),
    dict(leaves=9, bunch=1.0),
    dict(leaves=7, bunch=0.0),
)
BROADLEAF_STOOLS = (dict(foot=(-0.18, -0.235), lean=-2.0, k=1.00),
                    dict(foot=(0.18, -0.235), lean=2.5, k=0.92),
                    dict(foot=(0.00, 0.235), lean=-0.5, k=1.06))


def broadleaf(stage, spec, mats, rng):
    parts = []
    rise = BROADLEAF_PX[stage]
    h = (rise + BASE_DROP_PX) / PX_PER_Z if rise > 0 else 0.0
    if h <= 0.001:
        return parts
    design = BROADLEAF_GROWTH[stage]
    dead = stage == STAGES - 1
    leaf_mats = [mats["dead"]] if dead else mats["leaves"]
    stem_mat = mats["dead"] if dead else mats["stem"]
    want_leaves = LAYER in ("leaves", "all")
    want_fruit = LAYER == "all"
    mat = min(1.0, h / 2.3)
    for p, stool in enumerate(BROADLEAF_STOOLS):
        px, py = stool["foot"]
        ph = h * stool["k"] * rng.uniform(0.96, 1.04)
        # --- LAYER 2: the pseudostem, to the throat -------------------------------------
        zt = ph * 0.68
        stem = _axis((px, py, 0.015), zt, stool["lean"], stool["lean"] + 1.5, bow=0.025)
        r0 = 0.014 + 0.024 * mat
        parts.append(_sweep(f"pstem_{p}", stem_mat, stem, _taper(stem, r0, r0 * 0.62), sides=7))
        for c in range(3):
            aa = c * math.tau / 3.0 + 0.5
            bpy.ops.mesh.primitive_uv_sphere_add(segments=6, ring_count=4, radius=0.5,
                                                 location=(px + math.cos(aa) * 0.03, py + math.sin(aa) * 0.03, 0.028))
            collar = bpy.context.active_object
            collar.name = f"bcollar_{p}_{c}"
            collar.scale = (0.026, 0.020, 0.014)
            collar.rotation_euler = (0.0, 0.0, aa)
            collar.data.materials.append(mats["soil"])
            bpy.ops.object.shade_flat()
            parts.append(collar)
        # the throat: a thinner axis the leaves leave from, continuing the stem upward
        throat = _axis(stem[-1], ph - zt, stool["lean"] + 1.5, stool["lean"] + 4.0, bow=0.0)
        parts.append(_sweep(f"throat_{p}", stem_mat, throat, _taper(throat, r0 * 0.62, r0 * 0.30), sides=6))
        # --- LAYER 3: the paddles, a ladder up the throat ---------------------------------
        n = design["leaves"]
        if want_leaves:
            tl = _arc_len(throat)
            for k in range(n):
                frac = k / max(1, n - 1)
                at = _along(throat, tl * (0.05 + 0.95 * frac))
                side = 1 if (k + p) % 2 == 0 else -1
                yaw = SCREEN_YAW + (0.0 if side > 0 else math.pi) + rng.uniform(-0.9, 0.9)
                size = (0.42 - 0.16 * frac) * (0.45 + 0.55 * mat) * rng.uniform(0.9, 1.1)
                rise_deg = -4.0 + 62.0 * frac + rng.uniform(-8.0, 8.0)
                parts.extend(_blade(f"paddle_{p}_{k}", leaf_mats[k % len(leaf_mats)], stem_mat, at,
                                    size, yaw, rise_deg, rng, width_ratio=0.25,
                                    curl=rng.uniform(0.70, 1.00), tilt=rng.uniform(-18.0, 8.0), dead=dead))
        # --- LAYER 4: the bunch ----------------------------------------------------------
        b = design["bunch"]
        if want_fruit and b > 0.0:
            # the bunch hangs on the viewer's side of the stem, below the crown, where it
            # is clear of the paddles: a peduncle arcing out and down, then three hands of
            # five fingers each curving upward -- every finger a stroke with its own dark
            # edge behind it, so the hand reads as fingers and not as one yellow blob
            byaw = _CREASE_AT + rng.uniform(-0.3, 0.3)
            ped = _cane_path(_along(throat, 0.02), 0.20, byaw, -30.0, -88.0, segs=5)
            parts.append(_sweep(f"peduncle_{p}", stem_mat, ped, _taper(ped, 0.010, 0.007), sides=5))
            hx, hy, hz = ped[-1]
            hands = 3 if b > 0.8 else 2
            fr = spec["fruit_r"] * 0.85
            drop = fr * 3.2
            stalk = [(hx, hy, hz + 0.01), (hx, hy, hz - hands * drop - 0.01)]
            parts.append(_sweep(f"rachis_{p}", stem_mat, stalk, [0.007, 0.006], sides=4))
            for tier in range(hands):
                tz = hz - 0.01 - tier * drop
                for g in range(5):
                    a = byaw + (g - 2) * 0.62 + rng.uniform(-0.1, 0.1)
                    fin = _cane_path((hx + math.cos(a) * 0.008, hy + math.sin(a) * 0.008, tz),
                                     0.105, a, -62.0, 40.0, segs=4)
                    radii = [fr * s for s in (0.70, 1.0, 1.0, 0.85, 0.40)]
                    shell = [(x + _AWAY[0] * fr * 0.5, y + _AWAY[1] * fr * 0.5, z + _AWAY[2] * fr * 0.5) for x, y, z in fin]
                    parts.append(_sweep(f"nana_{p}_{tier}_{g}_edge", mats["fruit_edge"], shell, [r * 1.35 for r in radii], sides=5))
                    key = "unripe" if b < 0.8 else ("fruit" if (g + tier) % 2 else "fruit_alt")
                    parts.append(_sweep(f"nana_{p}_{tier}_{g}", mats[key], fin, radii, sides=5))
            # the bract: a dark bud hanging point-down under the last hand
            bud = _lathe(f"bract_{p}", mats["bract"], (hx, hy, hz - hands * drop - 0.05), fr * 1.8,
                         FRUIT_SHAPES["bud"], yaw=byaw, tilt=math.radians(180.0))
            parts.append(bud)
    return parts


# --------------------------------------------------------------------------- #
# Rosette (pineapple) -- the Cabbages habit
# --------------------------------------------------------------------------- #
#: The reference's heads never rise more than 8 px above the bed silhouette: a rosette
#: is LOW. Heights here are the rosette's top above the bed, px at 2x.
ROSETTE_PX = (0, 3, 6, 11, 17, 21, 23, 19)
#: THE GROWTH DESIGN: tiers of stiff narrow blades all round -- an outer skirt near
#: horizontal, a middle ring rising, an inner heart steep -- and from c5 the fruit on a
#: short central stalk with its crown. (blade counts per tier, outer first)
ROSETTE_GROWTH = (
    dict(tiers=(), fruit=0.0),
    dict(tiers=(5,), fruit=0.0),
    dict(tiers=(6, 4), fruit=0.0),
    dict(tiers=(7, 5), fruit=0.0),
    dict(tiers=(8, 6, 5), fruit=0.0),
    dict(tiers=(8, 6, 5), fruit=0.6),
    dict(tiers=(8, 6, 5), fruit=1.0),
    dict(tiers=(7, 5, 4), fruit=0.0),
)
ROSETTE_PLANTS = ((-0.21, -0.287), (0.23, -0.287), (-0.24, 0.0), (0.20, 0.0), (-0.20, 0.287), (0.22, 0.287))


def rosette(stage, spec, mats, rng):
    parts = []
    rise = ROSETTE_PX[stage]
    h = (rise + BASE_DROP_PX) / PX_PER_Z if rise > 0 else 0.0
    if h <= 0.001:
        return parts
    design = ROSETTE_GROWTH[stage]
    dead = stage == STAGES - 1
    leaf_mats = [mats["dead"]] if dead else mats["leaves"]
    stem_mat = mats["dead"] if dead else mats["stem"]
    want_leaves = LAYER in ("leaves", "all")
    want_fruit = LAYER == "all"
    mat = min(1.0, rise / 23.0)
    for p, (px, py) in enumerate(ROSETTE_PLANTS):
        # --- LAYER 2: the heart -- a stub of stem the blades leave from ----------------
        core = _axis((px, py, 0.012), 0.04 + 0.05 * mat, 0.0, 0.0)
        parts.append(_sweep(f"heart_{p}", stem_mat, core, _taper(core, 0.020, 0.012), sides=6))
        # --- LAYER 3: the tiers ----------------------------------------------------------
        if want_leaves:
            for tier, n in enumerate(design["tiers"]):
                size = (0.30, 0.24, 0.17)[tier] * (0.40 + 0.60 * mat)
                rise_deg = (6.0, 34.0, 62.0)[tier]
                for k in range(n):
                    yaw = k * math.tau / n + tier * 0.45 + p * 0.3 + rng.uniform(-0.15, 0.15)
                    at = _along(core, 0.01 + tier * 0.02)
                    at = (at[0] + math.cos(yaw) * 0.012, at[1] + math.sin(yaw) * 0.012, at[2])
                    parts.extend(_blade(f"rblade_{p}_{tier}_{k}", leaf_mats[(k + tier) % len(leaf_mats)],
                                        stem_mat, at, size * rng.uniform(0.85, 1.15), yaw,
                                        rise_deg + rng.uniform(-8.0, 8.0), rng, width_ratio=0.085,
                                        curl=0.30 if tier else 0.45, tilt=rng.uniform(-24.0, 6.0),
                                        rib=False, dead=dead))
        # --- LAYER 4: the fruit on its stalk --------------------------------------------
        f = design["fruit"]
        if want_fruit and f > 0.0:
            r = spec["fruit_r"] * (0.60 + 0.40 * f)
            top = core[-1]
            stalk = _axis(top, 0.05 + 0.04 * f, 0.0, 0.0)
            parts.append(_sweep(f"fstalk_{p}", stem_mat, stalk, _taper(stalk, 0.012, 0.010), sides=5))
            cz = stalk[-1][2] + r * 1.05
            key = "unripe" if f < 0.8 else None
            body_mat = mats["unripe"] if key else mats["fruit" if p % 2 else "fruit_alt"]
            shell = _lathe(f"pine_{p}_edge", mats["fruit_edge"],
                           (px + _AWAY[0] * r * 0.3, py + _AWAY[1] * r * 0.3, cz + _AWAY[2] * r * 0.3),
                           r, FRUIT_SHAPES["pineapple"], k=1.14)
            body = _lathe(f"pine_{p}", body_mat, (px, py, cz), r, FRUIT_SHAPES["pineapple"])
            parts.extend([shell, body])
            crown_z = cz + r * 1.35
            for c in range(6):
                yaw = c * math.tau / 6.0 + 0.4 + p * 0.2
                parts.extend(_blade(f"crown_{p}_{c}", leaf_mats[c % len(leaf_mats)], stem_mat,
                                    (px + math.cos(yaw) * r * 0.25, py + math.sin(yaw) * r * 0.25, crown_z),
                                    r * 1.6, yaw, 62.0 + rng.uniform(-8.0, 8.0), rng, width_ratio=0.10,
                                    curl=0.25, rib=False, dead=dead))
    return parts


# --------------------------------------------------------------------------- #
# Trellis (grape) -- the Greenpeas habit
# --------------------------------------------------------------------------- #
#: How far up its stake each vine has climbed, world units, per stage. The mod user's
#: Tree_Grape sheet is the design: six stakes in two rows, present from c0, and a vine
#: TWINING up each one -- a helix round the pole, laterals arching out and down off it
#: with the leaves and tendrils, bunches hanging from the laterals (green at c5, ripe
#: at c6). Vanilla's Greenpeas stays the tone reference; the frame it draws is rails,
#: the sheet's is stakes, and a vine reads as a vine only when it wraps something.
VINE_CLIMB = (0.0, 0.12, 0.26, 0.45, 0.78, 1.05, 1.05, 1.00)
VINE_GROWTH = (
    dict(laterals=0, bunch=0.0), dict(laterals=0, bunch=0.0), dict(laterals=0, bunch=0.0),
    dict(laterals=1, bunch=0.0), dict(laterals=2, bunch=0.0), dict(laterals=3, bunch=0.5),
    dict(laterals=3, bunch=1.0), dict(laterals=3, bunch=0.0),
)
STAKES = ((-0.24, 0.0, 0.24), (-0.235, 0.235))
LATERAL_Z = (0.34, 0.62, 0.88)


def _helix(base, height, radius, turns, phase=0.0, step=0.035):
    """A vine twining up a stake: a helix of ``turns`` over ``height`` round the pole."""
    n = max(4, int(height / step))
    pts = []
    for i in range(n + 1):
        u = i / n
        a = phase + u * math.tau * turns
        pts.append((base[0] + math.cos(a) * radius, base[1] + math.sin(a) * radius, base[2] + height * u))
    return pts


def trellis(stage, spec, mats, rng):
    parts = []
    dead = stage == STAGES - 1
    # --- LAYER 1: the stakes, from stage 0 -----------------------------------------------
    for i, y in enumerate(STAKES[1]):
        for j, x in enumerate(STAKES[0]):
            parts.append(_stalk(f"stake_{i}_{j}", mats["wood"], (x, y, 0.0), 1.12 + 0.03 * ((i + j) % 2),
                                0.014, 0.012))
    climb = VINE_CLIMB[stage]
    if climb <= 0.001:
        return parts
    design = VINE_GROWTH[stage]
    leaf_mats = [mats["dead"]] if dead else mats["leaves"]
    stem_mat = mats["dead"] if dead else mats["stem"]
    want_leaves = LAYER in ("leaves", "all")
    want_fruit = LAYER == "all"
    mat = min(1.0, climb / 1.05)
    leaf_size = 0.13 * (0.55 + 0.45 * mat)
    for i, y in enumerate(STAKES[1]):
        for c, cx in enumerate(STAKES[0]):
            ch = climb * rng.uniform(0.92, 1.0)
            # --- LAYER 2: the twining cane and its laterals ----------------------------
            vine = _helix((cx, y, 0.01), ch, 0.022, turns=ch * 2.1, phase=rng.uniform(0.0, math.tau))
            parts.append(_sweep(f"vine_{i}_{c}", stem_mat, vine, _taper(vine, 0.0080, 0.0045), sides=5))
            laterals = []
            for k, lz in enumerate(LATERAL_Z[:design["laterals"]]):
                if lz > ch - 0.04:
                    continue
                at = _along(vine, lz * _arc_len(vine) / ch)
                side = 1 if (k + c + i) % 2 == 0 else -1
                yaw = SCREEN_YAW + (0.0 if side > 0 else math.pi) + rng.uniform(-0.6, 0.6)
                lat = _cane_path(at, rng.uniform(0.15, 0.23) * (0.6 + 0.4 * mat), yaw, 28.0, -58.0, segs=6)
                parts.append(_sweep(f"lateral_{i}_{c}_{k}", stem_mat, lat, _taper(lat, 0.0050, 0.0028), sides=4))
                laterals.append((lat, yaw, side, lz))
                parts.append(_tendril(f"tendril_{i}_{c}_{k}", stem_mat, lat[-1], 0.08 * (0.6 + 0.4 * mat), 0.0030,
                                      yaw + rng.uniform(-0.6, 0.6), turns=1.5))
            if mat > 0.3:
                at = _along(vine, 0.55 * _arc_len(vine))
                parts.append(_tendril(f"tendril_{i}_{c}_v", stem_mat, at, 0.07, 0.0028,
                                      SCREEN_YAW + rng.uniform(-1.0, 1.0), turns=1.8))
            # --- LAYER 3: leaves on the cane and along the laterals -----------------------
            if want_leaves:
                L = _arc_len(vine)
                d, j = 0.10, 0
                while d < L - 0.03:
                    at = _along(vine, d)
                    side = 1 if (j + c) % 2 == 0 else -1
                    yaw = SCREEN_YAW + (0.0 if side > 0 else math.pi) + rng.uniform(-0.6, 0.6)
                    parts.extend(_palmate(f"vleaf_{i}_{c}_{j}", leaf_mats[j % len(leaf_mats)], stem_mat, at,
                                          leaf_size * rng.uniform(0.85, 1.15), yaw, rng.uniform(-10.0, 25.0), rng, dead=dead))
                    d += 0.13
                    j += 1
                for q, (lat, lyaw, side, lz) in enumerate(laterals):
                    for m in (1, 3, 5):
                        if m >= len(lat):
                            break
                        s2 = 1 if (m // 2 + q) % 2 else -1
                        yaw = lyaw + s2 * rng.uniform(0.6, 1.2)
                        parts.extend(_palmate(f"lleaf_{i}_{c}_{q}_{m}", leaf_mats[(q + m) % len(leaf_mats)], stem_mat,
                                              lat[m], leaf_size * rng.uniform(0.75, 1.05), yaw,
                                              rng.uniform(-20.0, 20.0), rng, dead=dead))
            # --- LAYER 4: bunches hanging from the laterals ---------------------------------
            b = design["bunch"]
            if want_fruit and b > 0.0 and laterals:
                for q, (lat, lyaw, side, lz) in enumerate(laterals):
                    if lz < 0.5 or (q + c) % 3 == 2:
                        continue
                    bx, by, bz = lat[2]
                    r = spec["fruit_r"] * 1.3 * (0.7 + 0.3 * b)
                    parts.append(_sweep(f"pedu_{i}_{c}_{q}", stem_mat, [(bx, by, bz), (bx, by, bz - 0.035)],
                                        [0.0040, 0.0032], sides=4))
                    tiers = ((5, 0.000, 1.00), (4, -0.028, 0.95), (4, -0.054, 0.88),
                             (3, -0.078, 0.80), (2, -0.098, 0.70), (1, -0.114, 0.58))
                    for t, (n, dz, rs) in enumerate(tiers):
                        for g in range(n):
                            a = g * math.tau / n + 0.6 * t
                            rad = 0.0 if n == 1 else r * 1.35 * rs
                            mkey = "unripe" if b < 0.8 else ("fruit" if (g + t) % 2 else "fruit_alt")
                            parts.extend(_berry(f"grape_{i}_{c}_{q}_{t}_{g}", mats[mkey],
                                                (bx + math.cos(a) * rad, by + math.sin(a) * rad * 0.7,
                                                 bz - 0.035 + dz - r * 0.6), r * rs, dimple=0.0))
    return parts


# --------------------------------------------------------------------------- #
# Clump (ginger) -- the SweetPotato habit
# --------------------------------------------------------------------------- #
#: Rise above the bed, read off vegetation_farming_01_96..103 (its bbox is spoiled by a
#: stray pixel, so this is measured on the plant mass), px at 2x.
CLUMP_PX = (0, 6, 24, 35, 55, 70, 80, 45)
#: THE GROWTH DESIGN: a ginger clump is several reed-like pseudostems from one crown,
#: each leaning a little outward, carrying narrow lanceolate leaves in two ranks on
#: alternate sides with a spear at the tip. The rhizome shows at the foot from c5.
CLUMP_GROWTH = (
    dict(stems=0, rhizome=0.0), dict(stems=1, rhizome=0.0), dict(stems=1, rhizome=0.0),
    dict(stems=2, rhizome=0.0), dict(stems=3, rhizome=0.0), dict(stems=4, rhizome=0.6),
    dict(stems=4, rhizome=1.0), dict(stems=3, rhizome=0.0),
)
CLUMP_PLANTS = ((-0.22, -0.235), (0.20, -0.235), (0.02, 0.235))


def clump(stage, spec, mats, rng):
    parts = []
    rise = CLUMP_PX[stage]
    h = (rise + BASE_DROP_PX) / PX_PER_Z if rise > 0 else 0.0
    if h <= 0.001:
        return parts
    design = CLUMP_GROWTH[stage]
    dead = stage == STAGES - 1
    leaf_mats = [mats["dead"]] if dead else mats["leaves"]
    stem_mat = mats["dead"] if dead else mats["stem"]
    want_leaves = LAYER in ("leaves", "all")
    want_fruit = LAYER == "all"
    mat = min(1.0, rise / 80.0)
    leaf_size = 0.20 * (0.60 + 0.40 * mat)
    for p, (px, py) in enumerate(CLUMP_PLANTS):
        n = design["stems"]
        for s in range(n):
            # --- LAYER 2: the pseudostems, leaning apart from one crown -------------------
            u = (s - (n - 1) / 2.0)
            side = 1 if u > 0 else (-1 if u < 0 else (1 if p % 2 else -1))
            lean = u * 9.0 + rng.uniform(-3.0, 3.0)
            ph = h * rng.uniform(0.80, 1.0) * (1.0 - 0.10 * abs(u))
            base = (px + _ACROSS[0] * u * 0.030 + _NEAR[0] * (s % 2) * 0.03,
                    py + _ACROSS[1] * u * 0.030 + _NEAR[1] * (s % 2) * 0.03, 0.015)
            stem = _axis(base, ph, lean * 0.6, lean * 1.4, bow=0.03)
            parts.append(_sweep(f"gstem_{p}_{s}", stem_mat, stem, _taper(stem, 0.0090, 0.0045), sides=5))
            # --- LAYER 3: two-ranked leaves and the spear ----------------------------------
            if want_leaves:
                L = _arc_len(stem)
                d, j = 0.20 * L + 0.02, 0
                while d < L - 0.03:
                    at = _along(stem, d)
                    sd = 1 if j % 2 == 0 else -1
                    yaw = SCREEN_YAW + (0.0 if sd > 0 else math.pi) + rng.uniform(-0.35, 0.35)
                    frac = d / L
                    parts.extend(_blade(f"gleaf_{p}_{s}_{j}", leaf_mats[(j + s) % len(leaf_mats)], stem_mat, at,
                                        leaf_size * rng.uniform(0.85, 1.15) * (1.0 - 0.25 * frac), yaw,
                                        22.0 + 30.0 * frac + rng.uniform(-8.0, 8.0), rng, width_ratio=0.12,
                                        curl=0.30, tilt=rng.uniform(-20.0, 8.0), dead=dead))
                    d += 0.085
                    j += 1
                parts.extend(_spear(f"gspear_{p}_{s}", leaf_mats, stem_mat, stem[-1], leaf_size * 0.55, rng, dead=dead))
        # --- LAYER 4: the rhizome at the foot -----------------------------------------------
        rz = design["rhizome"]
        if want_fruit and rz > 0.0:
            r = spec["fruit_r"] * (0.7 + 0.3 * rz)
            for q in range(3):
                aa = _CREASE_AT + (q - 1) * 0.8 + rng.uniform(-0.2, 0.2)
                parts.extend(_fruit(f"rhizome_{p}_{q}", mats,
                                    (px + math.cos(aa) * 0.05 + _NEAR[0] * 0.03,
                                     py + math.sin(aa) * 0.05 + _NEAR[1] * 0.03, 0.028 + r * 0.5),
                                    r, "rhizome", rng, alt=q % 2 == 1, lying=True))
    return parts


#: The TREE habit, measured off the painted fruit-tree sheets (Tree_Cherry/Peach/Pear/
#: Olive, 8 cells of 128x256) rather than off vanilla, which has no tree crop. Per cell,
#: green-hue bbox and centroid, brown-hue trunk width at the base, fruit blobs:
#:
#:   c0  seed: a 13 px brown lump on the ground, nothing else
#:   c1  sprout: 23x21 px of leaf at the ground, no trunk
#:   c2  sapling: trunk 3-4 px wide, canopy ~30x30 px
#:   c3  young: trunk 6-8 px, canopy 45-56 x 55-72 px
#:   c4  leafy: trunk 6-22 px, canopy 90-121 x 103-138 px, centre y~150
#:   c5  bloom: full canopy 120-128 x 152-178 px (centre y~88-125), 1000-1700 px of pale
#:       pink blossom; c6 the same with green fruit; c7 with ripe fruit -- 21-24 blobs of
#:       7-8 px diameter on Cherry/Peach, half buried in the canopy
#:
#: and, decisive for how the canopy is built: the leaf-scale autocorrelation length is
#: **2-3 px**. The canopy is a mass of tiny dabs at 75% fill, not a set of 14 px leaves.
#: So it is built from a few dozen lumpy clusters carrying a dab texture, tagged as one
#: foliage family so the style pass can light the mass crown-to-skirt as one body
#: (+0.09..+0.23 measured) and rim it (0.68-0.91), which per-leaf lighting cannot do.
#:
#: Screen->world: 90.5 px per unit across (the 128 px cell is the tile's 1.41 unit
#: diagonal), 78.4 px per unit of height. The canopy's 128 px width is therefore a
#: 1.0-unit sphere centred on the tile -- exactly the footprint, so it is built at
#: radius 0.47 to stay inside the packer's tile cut.
#:   (trunk height, trunk radius, canopy centre z, canopy rx, canopy rz, clusters,
#:    fruit count, blossom count)
TREE_STAGES = (
    None,                                                   # seed
    None,                                                   # sprout
    # v22, from the painted sheets (median of the 11 Tree_*.png, x0.92 for our floor
    # anchor): crown width x height px and its skirt above the trunk foot --
    #   c2 29x33, 25 px up; c3 44x56, 31; c4 86x106, 53; c5-c7 114x148, 62.
    # th: trunk height (the fork sits at the skirt); cz, rx, rz: crown centre and radii
    # (across the screen = 1.40 rx); tk: trunk radius as a fraction of the crop's own.
    dict(th=0.32, cz=0.53, rx=0.115, rz=0.21, tk=0.25),
    dict(th=0.40, cz=0.75, rx=0.174, rz=0.36, tk=0.45),
    dict(th=0.68, cz=1.35, rx=0.34, rz=0.66, tk=0.80),
    dict(th=0.79, cz=1.73, rx=0.45, rz=0.92, tk=1.00),
    dict(th=0.79, cz=1.73, rx=0.45, rz=0.92, tk=1.00),
    dict(th=0.79, cz=1.73, rx=0.45, rz=0.92, tk=1.00),
)
#: unripe fruit at c6 is green: measured off Cherry c6 (yellow-green blobs) and Banana c6
UNRIPE_PAINT = (0.34, 0.44, 0.12)
BLOOM_PAINT = (0.86, 0.60, 0.68)
#: toward the camera, for putting fruit and blossom on the canopy's visible face
_TOWARD = (0.612, -0.612, 0.5)


#: Three leaf tones for the canopy shells, painted per FACE inside one mesh. The
#: reference's canopy is a mass of 2-3 px dabs in a few greens with the lit ones running
#: to a warm yellow-green -- (0.55,0.75,0.30)-class highlights against (0.10,0.25,0.10)
#: shadow -- not the blue-green a single leaf paint under a ramp gives.
CANOPY_PAINTS = ((0.090, 0.260, 0.070), (0.230, 0.530, 0.120), (0.500, 0.740, 0.190),
                 (0.035, 0.100, 0.035))
BLOSSOM_PAINTS = ((0.520, 0.270, 0.360), (0.780, 0.480, 0.580), (0.950, 0.740, 0.810),
                  (0.300, 0.140, 0.220))


def _leaf_shell(name, mats, centre, radius, n, rng, lit=(0.55, -0.55, 0.62)):
    """A cluster's skin of small blades, ONE mesh: ``n`` tapered blades on the sphere,
    each turned to face outward and rolled a little, each face given one of three leaf
    tones by how much its outward normal faces the key light (with noise, because a
    painter's dabs are not a Lambert map). The painted canopies decorrelate within 2-3 px;
    at 6-8 px these blades are the largest thing that still reads as a leaf mass rather
    than a set of leaves. One mesh per cluster keeps the element count sane."""
    verts, faces, tones = [], [], []
    lx, ly, lz = lit
    for k in range(n):
        u, v = rng.random(), rng.random()
        th = math.tau * u
        ph = math.acos(2.0 * v - 1.0)
        nx, ny, nz = (math.sin(ph) * math.cos(th), math.sin(ph) * math.sin(th), math.cos(ph))
        bx, by, bz = (centre[0] + nx * radius, centre[1] + ny * radius, centre[2] + nz * radius)
        # blade axis: mostly tangent, tipped outward; width axis perpendicular
        ax = math.tau * rng.random()
        tx, ty, tz = (math.cos(ax), math.sin(ax), 0.0)
        # remove the normal component so the blade lies on the shell, then tip outward
        d = tx * nx + ty * ny + tz * nz
        tx, ty, tz = (tx - d * nx, ty - d * ny, tz - d * nz)
        L = math.sqrt(tx * tx + ty * ty + tz * tz) or 1.0
        tx, ty, tz = (tx / L, ty / L, tz / L)
        tip = 0.55
        tx, ty, tz = (tx + nx * tip, ty + ny * tip, tz + nz * tip)
        L = math.sqrt(tx * tx + ty * ty + tz * tz) or 1.0
        tx, ty, tz = (tx / L, ty / L, tz / L)
        wx, wy, wz = (ty * nz - tz * ny, tz * nx - tx * nz, tx * ny - ty * nx)
        size = radius * rng.uniform(0.75, 1.10)      # 8-12 px blades at the crown's scale
        hw = size * 0.40                             # broad: the reference leaf is ~1.2:1
        i0 = len(verts)
        verts.extend([
            (bx - wx * hw * 0.3, by - wy * hw * 0.3, bz - wz * hw * 0.3),
            (bx + wx * hw * 0.3, by + wy * hw * 0.3, bz + wz * hw * 0.3),
            (bx + tx * size * 0.55 + wx * hw, by + ty * size * 0.55 + wy * hw,
             bz + tz * size * 0.55 + wz * hw),
            (bx + tx * size, by + ty * size, bz + tz * size),
            (bx + tx * size * 0.55 - wx * hw, by + ty * size * 0.55 - wy * hw,
             bz + tz * size * 0.55 - wz * hw),
        ])
        faces.append((i0, i0 + 1, i0 + 2, i0 + 3, i0 + 4))
        # tone by facing, jittered
        f = nx * lx + ny * ly + nz * lz + rng.uniform(-0.45, 0.45)
        tones.append(2 if f > 0.25 else (1 if f > -0.30 else 0))
        # its outline: the same blade 14% larger, pushed back along the view axis, in
        # the rim tone -- the drawn edge round every leaf that the reference has and
        # that no element-boundary pass can add between blades of one mesh
        g = 1.22
        back = (radius * 0.16)
        j0 = len(verts)
        for vx, vy, vz in verts[i0:i0 + 5]:
            verts.append((bx + (vx - bx) * g + _AWAY[0] * back,
                          by + (vy - by) * g + _AWAY[1] * back,
                          bz + (vz - bz) * g + _AWAY[2] * back))
        faces.append((j0, j0 + 1, j0 + 2, j0 + 3, j0 + 4))
        tones.append(3)
    obj = _mesh_object(name, verts, faces, mats[0])
    obj.data.materials.append(mats[1])
    obj.data.materials.append(mats[2])
    obj.data.materials.append(mats[3])
    for poly, tone in zip(obj.data.polygons, tones):
        poly.material_index = tone
    solid = obj.modifiers.new("shell_solid", "SOLIDIFY")
    solid.thickness = radius * 0.05
    solid.offset = 0.0
    return obj


def _cluster(name, mat, centre, radius, rng):
    """One lumpy foliage cluster: an ico sphere with its vertices jittered radially so
    the silhouette breaks up the way dabbed paint does, smooth shaded so the dab texture
    and the crown-to-skirt ramp do the tonal work."""
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=radius, location=centre)
    obj = bpy.context.active_object
    obj.name = name
    for v in obj.data.vertices:
        k = 1.0 + rng.uniform(-0.16, 0.16)
        v.co = (v.co.x * k, v.co.y * k, v.co.z * k * rng.uniform(0.85, 1.05))
    obj.data.update()
    obj.rotation_euler = (rng.uniform(-0.5, 0.5), rng.uniform(-0.5, 0.5), rng.uniform(0, 6.28))
    obj.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    return obj


def _on_canopy(rng, cz, rx, rz, depth=0.90, spread=1.15):
    """A point on the canopy's camera-facing shell, ``depth`` of the way out (below 1.0
    buries it a little, as the reference's fruit are)."""
    tx, ty, tz = _TOWARD
    base = math.atan2(ty, tx)
    yaw = base + rng.uniform(-spread, spread)
    pitch = rng.uniform(-0.9, 0.9)
    dx, dy, dz = math.cos(yaw) * math.cos(pitch), math.sin(yaw) * math.cos(pitch), math.sin(pitch)
    return (dx * rx * depth, dy * rx * depth, cz + dz * rz * depth)


#: THE TREE DESIGN (v22). The mod user's painted sheets (Tree_*.png) are the reference a
#: fruit tree has -- vanilla has no tree crop -- and what the user singled out in them is
#: the TRUNK AND LIMBS (their pixel drawing and their colour) and the FRUIT. Read at 6x
#: and measured on all 11 sheets (ripe cell):
#:   * trunk: a straight, slightly tapering cylinder 8-17 px wide, lit on its left ~40%,
#:     thin low-contrast streaks along it, no outline; at the crown's skirt it forks into
#:     2-4 limbs that splay up into the crown in a V and show for 10-20 px before the
#:     leaves close over them. The base is cut straight or flares into 2-3 short roots
#:     (base/mid width 1.0-2.4). Colour per crop: near-black red-brown (cherry), grey-brown
#:     (orange, coffee), warm mid brown (lemon, avocado), silver grey (olive).
#:   * forms: one trunk (most), a low vase of four limbs (cherry), twin trunks (olive),
#:     a clump of stems (coffee).
#:   * fruit: 15-24 fruit (cherry 22 pairs, olive/coffee ~25 clusters) spread evenly over
#:     the crown's FRONT, each sitting on the leaves on a short stem, deep and saturated,
#:     with a 1 px highlight, a dark side and a dark rim; mango/peach two-toned.
#:   * shadow: a flat black ellipse ~0.42 of the sprite's width (drawn by the build:
#:     --ground-shadow-shape ellipse), none under seed and sprout.
#:   limbs / secs / twigs  how far the skeleton branches;  leaf  blade size (world)
#:   bloom  share of the crown that is blossom;  fruit  share of the full fruit count
TREE_GROWTH = (
    dict(limbs=0, secs=0, twigs=0, leaf=0.00, bloom=0.0, fruit=0.0),
    dict(limbs=0, secs=0, twigs=0, leaf=0.00, bloom=0.0, fruit=0.0),
    dict(limbs=2, secs=2, twigs=2, leaf=0.085, bloom=0.0, fruit=0.0),
    dict(limbs=3, secs=3, twigs=3, leaf=0.090, bloom=0.0, fruit=0.0),
    dict(limbs=4, secs=3, twigs=5, leaf=0.105, bloom=0.0, fruit=0.0),
    dict(limbs=4, secs=4, twigs=5, leaf=0.110, bloom=0.62, fruit=0.0),
    dict(limbs=4, secs=4, twigs=5, leaf=0.110, bloom=0.0, fruit=1.0),
    dict(limbs=4, secs=4, twigs=5, leaf=0.100, bloom=0.0, fruit=0.65),
)
#: Per crop, off its own painted sheet (ripe cell):
#:   r      trunk radius: mid-trunk width px / 2 / 90.5 px per metre, x0.92 (our tree
#:          stands on the game's floor anchor, 15-20 px higher than the sheets', so the
#:          whole tree is drawn at 0.92 of the sheet's height)
#:   flare  base width / mid width; past 1.3 the base grows root buttresses
#:   form   single | vase | twin | multi (see above)
#:   bark   albedo of the trunk's lit (p90) shade on the S face (pzforge.spec.albedo_for)
#:   crown  median rendered colour of the crown's greens -- the leaf paints are scaled to it
#:   n, fr  fruit count and fruit radius (median fruit blob, less the outline's ~16%)
TREE_FORMS = {
    "cherry":     dict(bloom=(0.92, (0.87, 0.26, 0.47)), seen=(61, 84, 42), r=0.086, flare=1.2, form="vase",   bark=(0.100, 0.052, 0.023), crown=(71, 98, 43),  n=22, fr=0.031,
                       T=185, S=38, W=113, fork=0, fl=0.49, gl=(0.70, 0.06)),
    "pear":       dict(bloom=(0.14, (0.95, 0.95, 0.9)), seen=(65, 83, 48), r=0.041, flare=1.0, form="single", bark=(0.147, 0.089, 0.063), crown=(78, 98, 50),  n=20, fr=0.042,
                       T=199, S=36, W=101, fork=0, fl=1.0, gl=(0.22, 0.16)),
    "peach":      dict(bloom=(0.92, (1.0, 0.38, 0.75)), seen=(77, 106, 53), r=0.071, flare=1.6, form="single", bark=(0.166, 0.107, 0.074), crown=(91, 123, 52), n=20, fr=0.065,
                       T=212, S=66, W=116, fork=-6, fl=1.76, gl=(0.12, 0.18)),
    "orange":     dict(bloom=(0.12, (0.95, 0.96, 0.88)), seen=(66, 92, 61), r=0.066, flare=1.1, form="single", bark=(0.156, 0.114, 0.107), crown=(88, 119, 67), n=27, fr=0.059,
                       T=213, S=57, W=116, fork=8, fl=1.89, gl=(0.30, 0.14)),
    "lemon":      dict(bloom=(0.22, (0.95, 0.96, 0.85)), seen=(59, 98, 57), r=0.051, flare=1.2, form="single", bark=(0.264, 0.166, 0.111), crown=(79, 130, 65), n=16, fr=0.075,
                       T=209, S=56, W=101, fork=2, fl=2.2, gl=(0.30, 0.14)),
    "lime":       dict(bloom=(0.2, (0.95, 0.96, 0.85)), seen=(46, 97, 53), r=0.051, flare=1.2, form="single", bark=(0.264, 0.170, 0.114), crown=(63, 120, 60), n=16, fr=0.074,
                       T=208, S=53, W=104, fork=2, fl=0.60, gl=(0.30, 0.14)),
    "grapefruit": dict(bloom=(0.12, (0.95, 0.96, 0.88)), seen=(75, 102, 66), r=0.061, flare=1.1, form="single", bark=(0.156, 0.114, 0.107), crown=(88, 119, 67), n=26, fr=0.064,
                       T=207, S=58, W=117, fork=8, fl=1.81, gl=(0.30, 0.14)),
    "avocado":    dict(bloom=(0.0, None), seen=(42, 66, 43), r=0.071, flare=2.4, form="single", bark=(0.234, 0.139, 0.086), crown=(56, 86, 46),  n=18, fr=0.046,
                       T=205, S=53, W=110, fork=-8, fl=1.0, gl=(0.50, 0.07)),
    "mango":      dict(bloom=(0.0, None), seen=(59, 89, 55), r=0.071, flare=2.4, form="single", bark=(0.234, 0.152, 0.093), crown=(65, 97, 52),  n=16, fr=0.06,
                       T=206, S=49, W=112, fork=-8, fl=1.86, gl=(0.22, 0.16)),
    # single dark olives (the painted blobs are 5x7 px, one fruit each), silver birch-like
    # twin trunks on one stump
    "olive":      dict(bloom=(0.25, (0.95, 0.93, 0.8)), seen=(59, 73, 51), r=0.040, flare=1.0, form="twin",   bark=(0.530, 0.480, 0.440), crown=(79, 94, 54),  n=30, fr=0.034,
                       T=212, S=55, W=109, fork=4, style="hang", bark_tex="birch", fl=0.34, gl=(0.55, 0.07)),
    # a low bundle of stems under a big round crown; berries in clumps of 5-7
    "coffee":     dict(bloom=(0.0, None), seen=(74, 92, 49), r=0.040, flare=1.0, form="multi",  bark=(0.143, 0.093, 0.074), crown=(93, 112, 51), n=25, fr=0.027,
                       T=194, S=31, W=114, fork=4, per=6, fl=0.5, gl=(0.60, 0.06)),
    # no apple sheet: the cherry's silhouette and red, a little lighter
    "apple":      dict(bloom=(0.55, (1.0, 0.72, 0.84)), seen=(60, 82, 43), r=0.068, flare=1.3, form="single", bark=(0.130, 0.075, 0.045), crown=(80, 108, 48), n=20, fr=0.052,
                       T=180, S=42, W=105, fork=0, fl=0.75, gl=(0.55, 0.07)),
}
#:   T, S, W  where the crown is SET: the painted ripe crown x0.92 (silhouette rows of
#:            >= 6 green px, top / skirt above the trunk foot, width px) less the residual
#:            v22f measured between that setting and our shell-crowned silhouette
#:   fork     px the fork sits ABOVE the visible skirt (hidden in the crown: orange,
#:            grapefruit) or below it (a V of limbs showing: avocado, mango, peach)
#:   bloom    the bloom stage (c5) off the painted c5 cell: share of the crown in flower
#:            and the blossom paint (cherry and peach flower all over in magenta-pink,
#:            pear/citrus/olive carry 12-25% white, avocado/mango/coffee none)
#:   seen     the crown median v22c rendered: the leaf paints are corrected by
#:            target/seen per channel in linear light (a second closed loop)
#:   fl, gl   fruit light (x every toon level: the painted fruit are drawn brighter than
#:            our ramp's top step reaches -- orange p50 209 vs 165 -- or darker: cherry,
#:            olive, lime) and the highlight (strength, roughness): a crisp dot on cherry
#:            and berries, a soft sheen on citrus, peach, pear, mango
#:   T, S, W  the painted ripe crown: top and skirt above the trunk foot, width (px, x0.92)
#:   fork     px the fork sits ABOVE the visible skirt (hidden in the crown: orange,
#:            grapefruit) or below it (a V of limbs showing: avocado, mango, peach)
#: How a crown ellipsoid renders with its leaf shell (v22e): the leafy top lands ~4 px
#: over the ellipsoid's top, the skirt ~3 px under its bottom (leaves droop), the width
#: ~8 px over (rim leaves, kept on the tile)
_SIL_TOP, _SIL_SKIRT, _SIL_W = 4.0, -3.0, 8.0


def _tree_stage(form, stage):
    """The crop's crown and trunk at a stage: c5-c7 solved from its painted silhouette,
    the earlier stages the median stage table scaled by its height and width."""
    st = TREE_STAGES[stage]
    T, S, W = form["T"], form["S"], form["W"]
    if stage >= 5:
        cz = (T - _SIL_TOP + S - _SIL_SKIRT) / 2.0 / PX_PER_Z
        rz = (T - _SIL_TOP - S + _SIL_SKIRT) / 2.0 / PX_PER_Z
        rx = (W - _SIL_W) / (2.0 * _CROWN_ACROSS * 90.5)
        th = (S + form["fork"]) / PX_PER_Z
        return dict(th=th, cz=cz, rx=rx, rz=rz, tk=st["tk"])
    hf, wf = T / 211.0, W / 105.0
    return dict(th=st["th"] * hf, cz=st["cz"] * hf, rx=st["rx"] * wf, rz=st["rz"] * hf, tk=st["tk"])
#: the rendered crown median the base leaf paints give (measured on the cherry, v22);
#: each crop's paints are scaled per channel (in linear light) from this to its own crown
CROWN_RESPONSE = (71, 98, 43)
#: how many leaf areas the crown's outer shell carries per unit of its visible area
SHELL_COVER = 1.3
#: drooping boughs under a hidden fork -- off since the shell fills the lower crown
#: (with both, v22e's orange skirt hung 15 px below the painted one)
SKIRT_BOUGHS = False
#: crown semi-axes across the screen and in depth, as multiples of TREE_STAGES' rx
_CROWN_ACROSS, _CROWN_DEPTH = 1.40, 0.60
#: the painter's light: from the upper left and a little in front
_LIGHT = (-0.55, 0.35, 0.76)
_UP = (0.0, 0.0, 1.0)
#: screen axes in world units (rig: 90.5 px per metre across, 78.4 per metre of height)
_SCREEN_UP = (-0.354, 0.354, 0.866)


def _clamp_tile(p, lim=0.48):
    return (max(-lim, min(lim, p[0])), max(-lim, min(lim, p[1])), p[2])


def _screen(p):
    """World point -> sprite px (x right, y up) relative to the tile centre."""
    return ((p[0] * _ACROSS[0] + p[1] * _ACROSS[1]) * 90.5,
            (p[0] * _SCREEN_UP[0] + p[1] * _SCREEN_UP[1] + p[2] * _SCREEN_UP[2]) * 90.5)


def _bough(a, b, bow, rng, segs=6, lift=0.0):
    """A limb from ``a`` to ``b``: one smooth sideways bow (perpendicular to its run,
    in the ground plane) and an upward lift, so no limb is a straight rod."""
    dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    hl = math.hypot(dx, dy) or 1e-6
    nx, ny = -dy / hl, dx / hl
    s = 1.0 if rng.random() < 0.5 else -1.0
    pts = []
    for i in range(segs + 1):
        u = i / segs
        w = math.sin(math.pi * u)
        pts.append((a[0] + dx * u + nx * s * bow * w, a[1] + dy * u + ny * s * bow * w,
                    a[2] + dz * u + lift * w))
    return pts


def _on_crown(cz, rx, rz, yaw, el, k):
    """A point at fraction ``k`` of the crown ellipsoid's radius in direction (yaw, el):
    yaw runs round the SCREEN-aligned ellipsoid (0 = screen-right, pi/2 = toward the
    camera), el up from its equator."""
    ce = math.cos(el)
    across = ce * math.cos(yaw) * rx * _CROWN_ACROSS * k
    depth = ce * math.sin(yaw) * rx * _CROWN_DEPTH * k
    return _clamp_tile((_ACROSS[0] * across + _NEAR[0] * depth,
                        _ACROSS[1] * across + _NEAR[1] * depth,
                        cz + math.sin(el) * rz * k))


def _crown_shade(p, cz, rx, rz):
    """Where a leaf sits on the crown: (dot of its shell normal with the light, its
    radius fraction). The painter's rule: lit side bright, far side dark, inside dark."""
    across = (p[0] * _ACROSS[0] + p[1] * _ACROSS[1]) / (rx * _CROWN_ACROSS)
    depth = (p[0] * _NEAR[0] + p[1] * _NEAR[1]) / (rx * _CROWN_DEPTH)
    up = (p[2] - cz) / rz
    k = math.sqrt(across * across + depth * depth + up * up) or 1e-6
    nx = across * _ACROSS[0] + depth * _NEAR[0]
    ny = across * _ACROSS[1] + depth * _NEAR[1]
    nz = up
    nl = math.sqrt(nx * nx + ny * ny + nz * nz) or 1e-6
    lit = (nx * _LIGHT[0] + ny * _LIGHT[1] + nz * _LIGHT[2]) / nl
    return lit, k


def _tone(mats_list, lit, k, rng, bloom=False):
    """Pick the leaf paint for a crown position: five tones from the lit crown to the
    shadowed interior, with one step of jitter so clusters are not banded."""
    n = len(mats_list)
    t = 0.5 + 0.5 * lit                 # 0 far side .. 1 lit side
    if k < 0.62:
        t *= 0.45                       # interior: shadow
    idx = int(round((1.0 - t) * (n - 1))) + rng.choice((-1, 0, 0, 1))
    return mats_list[max(0, min(n - 1, idx))]


def _trunk_radii(pts, r, flare, top_k=0.78):
    """A trunk's radius along its axis: tapering to ``top_k`` at the fork, swelling into
    the base over its lowest 15% (capped at 1.35x; wider flares are root buttresses)."""
    n = max(1, len(pts) - 1)
    out = []
    for i in range(len(pts)):
        u = i / n
        swell = 1.0 + (min(flare, 1.35) - 1.0) * max(0.0, 1.0 - u / 0.15) ** 2
        out.append(r * (1.0 - (1.0 - top_k) * u) * swell)
    return out


def _stems(form, th, r, rng):
    """The crop's trunk(s): a list of (axis polyline, radius, limbs at its top, fork
    height fraction). Saplings are always single stems."""
    lean = rng.uniform(-3.0, 3.0)
    if form == "vase":
        # a short thick trunk forking low into four splayed limbs (painted cherry: the
        # trunk splits ~24 px under the skirt and the limbs fan through the lower crown)
        return [(_axis((0.0, 0.0, 0.0), th * 0.52, lean, lean * 0.4, bow=0.02), r, 4, 0.52)]
    if form == "twin":
        # two trunks off one stump, nearly parallel (painted olive: 7-8 px each, a 19 px
        # stump, diverging a few degrees)
        out = []
        for s in (-1, 1):
            base = (_ACROSS[0] * s * r * 0.85, _ACROSS[1] * s * r * 0.85, 0.0)
            out.append((_axis(base, th * 1.02, s * 4.0 + rng.uniform(-1, 1), s * 2.5, bow=0.02), r, 2, 1.0))
        return out
    if form == "multi":
        # a bundle: three stems close together, splaying a little (painted coffee: 21-24 px
        # wide at the foot, the crown low over them)
        out = []
        for s in (-1, 0, 1):
            base = (_ACROSS[0] * s * r * 1.25 + _NEAR[0] * (0.015 if s == 0 else -0.01),
                    _ACROSS[1] * s * r * 1.25 + _NEAR[1] * (0.015 if s == 0 else -0.01), 0.0)
            hk = 1.0 if s == 0 else 0.94
            out.append((_axis(base, th * hk, s * 9.0 + rng.uniform(-2, 2), s * 6.0, bow=0.02), r, 2, 1.0))
        return out
    return [(_axis((0.0, 0.0, 0.0), th, lean, lean * 0.4, bow=0.03), r, None, 1.0)]


def _front_points(cz, rx, rz, n, rng, k=(0.86, 0.96), el=(-38.0, 60.0)):
    """``n`` points on the FRONT of the crown, spread evenly on the sprite: Poisson-disc
    in screen px, the spacing set by the crown's visible area over the count."""
    across_px = rx * _CROWN_ACROSS * 90.5
    half_h = rz * 78.4
    area = math.pi * across_px * half_h * 0.80
    dmin = 0.80 * math.sqrt(area / max(1, n))
    pts, scr = [], []
    for _ in range(4000):
        if len(pts) >= n:
            break
        yaw = math.pi / 2.0 + rng.uniform(-1.30, 1.30)
        e = math.radians(rng.uniform(*el))
        p = _on_crown(cz, rx, rz, yaw, e, rng.uniform(*k))
        sp = _screen(p)
        if all((sp[0] - q[0]) ** 2 + (sp[1] - q[1]) ** 2 >= dmin * dmin for q in scr):
            pts.append(p)
            scr.append(sp)
    return pts


def _pick_spots(cands, cz, rx, rz, n, rng, margin=0.0):
    """``n`` fruit spots out of the front leaf anchors, spread evenly on the sprite
    (Poisson-disc in screen px). Fruit then always sit ON the crown -- v22a drew them
    on the crown ellipsoid, and where the leaves fell short of it they floated."""
    across_px = rx * _CROWN_ACROSS * 90.5
    half_h = rz * 78.4
    dmin = 0.82 * math.sqrt(math.pi * across_px * half_h * 0.80 / max(1, n))
    # a fruit must sit wholly on the tile once moved in front of its leaf (+_TOWARD 0.12):
    # past the edge the packer cuts its body and the outline shell behind it is left as a
    # dark crescent (v22g: orange, lemon, grapefruit, peach)
    lim = 0.47 - margin
    pool = [q for q in cands
            if abs(q[0] + _TOWARD[0] * 0.12) <= lim and abs(q[1] + _TOWARD[1] * 0.12) <= lim]
    rng.shuffle(pool)
    out, scr = [], []
    for shrink in (1.0, 0.85, 0.7):
        d2 = (dmin * shrink) ** 2
        for p in pool:
            if len(out) >= n:
                break
            sp = _screen(p)
            if all((sp[0] - q[0]) ** 2 + (sp[1] - q[1]) ** 2 >= d2 for q in scr):
                out.append(p)
                scr.append(sp)
        if len(out) >= n:
            break
    return out


def tree(stage, spec, mats, rng):
    parts = []
    form = TREE_FORMS.get(spec.get("name"), TREE_FORMS["apple"])
    if stage == 0:
        bpy.ops.mesh.primitive_uv_sphere_add(segments=8, ring_count=5, radius=0.5,
                                             location=(0.0, 0.0, 0.035))
        seed = bpy.context.active_object
        seed.name = "seed"
        seed.scale = (0.110, 0.085, 0.068)
        seed.data.materials.append(mats["bark"])
        bpy.ops.object.shade_smooth()
        parts.append(F.tag_family(seed, "wood"))
        return parts
    want_leaves = LAYER in ("leaves", "all")
    want_fruit = LAYER == "all"
    if stage == 1:
        parts.append(F.tag_family(_stalk("sprout_stem", mats["stem"], (0.0, 0.0, 0.0),
                                         0.07, 0.008, 0.005), "wood"))
        if want_leaves:
            for k in range(4):
                yaw = SCREEN_YAW + (k - 1.5) * 0.8 + (math.pi if k % 2 else 0.0)
                parts.extend(F.tag_family(_leaf(
                    f"sprout_{k}", mats["tree_leaves"][1 + k % 2],
                    (0.0, 0.0, 0.03 + 0.012 * k), 0.11, yaw, math.radians(-30.0 - 8.0 * k), curl=0.20,
                    width_ratio=0.30, roll=_broadside_roll(yaw)), "foliage"))
        return parts
    st = _tree_stage(form, stage)
    design = TREE_GROWTH[stage]
    th, cz, rx, rz = st["th"], st["cz"], st["rx"], st["rz"]
    r = form["r"] * st["tk"]
    withered = stage == STAGES - 1
    tones = mats["tree_leaves"]
    if withered:
        tones = [mats["tree_leaves"][0], mats["dead"], mats["tree_leaves"][2], mats["dead"], mats["tree_leaves"][4]]
    # --- LAYER 2a: the trunk(s) and the root flare ------------------------------------------
    kind = form["form"] if stage >= 4 else "single"
    stems = _stems(kind, th, r, rng)
    for si, (axis, sr, _nl, _fk) in enumerate(stems):
        parts.append(F.tag_family(_sweep(f"trunk_{si}", mats["bark"], axis,
                                         _trunk_radii(axis, sr, form["flare"] if stage >= 4 else 1.0, top_k=0.70),
                                         sides=8), "wood"))
    if kind == "twin":
        stump = [(0.0, 0.0, 0.0), (0.0, 0.0, 0.05), (0.0, 0.0, 0.11)]
        parts.append(F.tag_family(_sweep("stump", mats["bark"], stump, [r * 2.3, r * 2.0, r * 1.2], sides=8), "wood"))
    if stage >= 4 and form["flare"] > 1.3:
        for c in range(3):
            aa = _CREASE_AT + (c - 1) * 1.7 + rng.uniform(-0.25, 0.25)
            reach = r * form["flare"] * rng.uniform(0.95, 1.10)
            root = [(math.cos(aa) * r * 0.3, math.sin(aa) * r * 0.3, r * 1.4),
                    (math.cos(aa) * r * 0.85, math.sin(aa) * r * 0.85, r * 0.45),
                    (math.cos(aa) * reach, math.sin(aa) * reach, 0.004)]
            parts.append(F.tag_family(_sweep(f"root_{c}", mats["bark"], root,
                                             [r * 0.62, r * 0.42, r * 0.16], sides=6), "wood"))
    # --- LAYER 2b: limbs from the fork -> secondaries -> twigs --------------------------------
    twigs = []                      # (polyline, is_secondary)
    li_total = 0
    for si, (axis, sr, n_lim, fork_k) in enumerate(stems):
        # the limbs leave from INSIDE the trunk, below its end, each nearly as thick as
        # the trunk there: their union is the fork, and the trunk's end cap stays buried
        # (v22a's limbs started at the cap and left a visible step at every fork)
        top = _along(axis, _arc_len(axis) * 0.80)
        n_limbs = n_lim if n_lim else design["limbs"]
        limb_r = sr * (0.66 if n_limbs >= 4 else 0.74)
        base_yaw = rng.uniform(-0.35, 0.35)
        for i in range(n_limbs):
            yaw = base_yaw + i * math.tau / n_limbs + rng.uniform(-0.30, 0.30)
            if kind == "vase":
                # long splayed limbs through the lower crown: they end low and wide
                el = math.radians(rng.uniform(-28.0, 12.0))
                end = _on_crown(cz, rx, rz, yaw, el, 0.74)
            else:
                el = math.radians(rng.uniform(5.0, 42.0))
                end = _on_crown(cz, rx, rz, yaw, el, 0.46)
            if len(stems) > 1:
                # each stem of a twin or a clump feeds its own side of the crown
                end = (end[0] + (top[0] - axis[0][0]) * 1.5, end[1] + (top[1] - axis[0][1]) * 1.5, end[2])
                end = _clamp_tile(end)
            limb = _bough(top, end, bow=0.04 + 0.03 * rx, rng=rng, lift=0.02)
            parts.append(F.tag_family(_sweep(f"limb_{si}_{i}", mats["bark"], limb,
                                             _taper(limb, limb_r, limb_r * 0.50), sides=6), "wood"))
            twigs.append((limb[2:], True))
            n_sec = design["secs"] if len(stems) == 1 else max(2, design["secs"] - 1)
            for s in range(n_sec):
                base = limb[2 + (s * 4) // max(1, n_sec)] if len(limb) > 6 else limb[-1]
                syaw = yaw + (s - (n_sec - 1) / 2.0) * 0.85 + rng.uniform(-0.3, 0.3)
                sel = math.radians(rng.uniform(-62.0, 76.0))
                send = _on_crown(cz, rx, rz, syaw, sel, rng.uniform(0.80, 0.96))
                sec = _bough(base, send, bow=0.05, rng=rng, lift=0.03)
                sec_r = limb_r * 0.55
                parts.append(F.tag_family(_sweep(f"sec_{si}_{i}_{s}", mats["bark"], sec,
                                                 _taper(sec, sec_r, sec_r * 0.45), sides=5), "wood"))
                twigs.append((sec[2:], True))
                for t in range(design["twigs"]):
                    tb = sec[2 + t * (len(sec) - 3) // max(1, design["twigs"] - 1)] if design["twigs"] > 1 else sec[-2]
                    tyaw = syaw + (t % 2 * 2 - 1) * rng.uniform(0.5, 1.1)
                    tel = math.radians(rng.uniform(-10.0, 55.0))
                    tl = 0.12 + 0.14 * rx + rng.uniform(-0.02, 0.04)
                    tend = _clamp_tile((tb[0] + math.cos(tel) * math.cos(tyaw) * tl,
                                        tb[1] + math.cos(tel) * math.sin(tyaw) * tl,
                                        tb[2] + math.sin(tel) * tl * 1.3))
                    twig = _bough(tb, tend, bow=0.012, rng=rng, segs=4)
                    parts.append(F.tag_family(_sweep(f"twig_{si}_{i}_{s}_{t}", mats["bark"], twig,
                                                     _taper(twig, 0.0045, 0.0022), sides=4), "wood"))
                    twigs.append((twig, False))
            li_total += 1
        if SKIRT_BOUGHS and form["fork"] > 2 and stage >= 4:
            # under a fork hidden in the crown nothing reaches the crown's lower shell:
            # boughs droop from the stem's top out and down to it (v22c: the orange's
            # skirt sat 27 px above the painted one's with the lower crown empty)
            n_skirt = 5 if len(stems) == 1 else 3
            for q in range(n_skirt):
                syaw = base_yaw + (q + 0.5) * math.tau / n_skirt + rng.uniform(-0.3, 0.3)
                start = _along(axis, _arc_len(axis) * rng.uniform(0.70, 0.95))
                send = _on_crown(cz, rx, rz, syaw, math.radians(rng.uniform(-80.0, -45.0)), rng.uniform(0.86, 0.96))
                droop = _bough(start, send, bow=0.04, rng=rng, lift=-0.02)
                dr = sr * 0.40
                parts.append(F.tag_family(_sweep(f"skirt_{si}_{q}", mats["bark"], droop,
                                                 _taper(droop, dr, dr * 0.45), sides=5), "wood"))
                twigs.append((droop[2:], True))
                for tq in range(3):
                    tb = droop[2 + tq]
                    tyaw = syaw + (tq % 2 * 2 - 1) * rng.uniform(0.5, 1.1)
                    tel = math.radians(rng.uniform(-35.0, 10.0))
                    tl = 0.10 + 0.12 * rx
                    tend = _clamp_tile((tb[0] + math.cos(tel) * math.cos(tyaw) * tl,
                                        tb[1] + math.cos(tel) * math.sin(tyaw) * tl,
                                        tb[2] + math.sin(tel) * tl))
                    twig = _bough(tb, tend, bow=0.010, rng=rng, segs=4)
                    parts.append(F.tag_family(_sweep(f"stwig_{si}_{q}_{tq}", mats["bark"], twig,
                                                     _taper(twig, 0.0042, 0.0022), sides=4), "wood"))
                    twigs.append((twig, False))
    # --- LAYER 3: the crown ---------------------------------------------------------------
    fruit_spots = []                # front-facing leaf anchors: where fruit can sit
    # the skirt is kept thin round the fork only where the fork shows (a V of limbs under
    # the crown); a fork hidden in the crown keeps a full skirt (v22b: clearing it there
    # lifted orange/grapefruit skirts 22-25 px)
    clear_skirt = form["fork"] <= 2 and kind in ("single", "vase")
    if want_leaves:
        size = design["leaf"]
        bloom = form.get("bloom", (design["bloom"], None))[0] if design["bloom"] > 0.0 else 0.0
        # a crown that flowers all over turns its leaves to blossom; a sparse white
        # bloom is drawn as small flower dots on top instead (after the leaves)
        flower_dots = bloom if 0.0 < bloom < 0.5 else 0.0
        if flower_dots:
            bloom = 0.0
        li = 0

        def put_leaf(at, sz, yaw, pitch, name):
            nonlocal li
            if at[0] * _NEAR[0] + at[1] * _NEAR[1] > 0.0:
                fruit_spots.append(tuple(at))
            # keep the blade on the tile: a tip past the edge is cut by the packer, so
            # turn the blade back toward the trunk (and shorten it if that is not enough)
            for _ in range(2):
                tip = (at[0] + math.cos(yaw) * math.cos(pitch) * sz, at[1] + math.sin(yaw) * math.cos(pitch) * sz)
                if max(abs(tip[0]), abs(tip[1])) <= 0.485:
                    break
                yaw = math.atan2(-at[1], -at[0]) + rng.uniform(-0.6, 0.6)
            tip = (at[0] + math.cos(yaw) * math.cos(pitch) * sz, at[1] + math.sin(yaw) * math.cos(pitch) * sz)
            over = max(abs(tip[0]), abs(tip[1])) - 0.485
            if over > 0.0:
                sz = max(sz * 0.4, sz - over * 1.2)
            lit, k = _crown_shade(at, cz, rx, rz)
            if bloom > 0.0 and rng.random() < bloom:
                mat = _tone(mats["blossom"], lit, k, rng)
            else:
                mat = _tone(tones, lit, k, rng)
            edge = rng.random() < 0.12
            roll = _broadside_roll(yaw, rng.uniform(-28.0, 14.0) + (58.0 if edge else 0.0))
            parts.extend(F.tag_family(_leaf(name, mat, at, sz, yaw, pitch, curl=rng.uniform(0.05, 0.20),
                                            midrib=0.35, width_ratio=rng.uniform(0.21, 0.29), roll=roll), "foliage"))
            li += 1

        # the dark inner fill: large shadow leaves deep in the crown so nothing shows
        # through the clusters but the tree's own shade
        n_fill = int(70 * rx * rz)
        for f in range(n_fill):
            yaw = rng.uniform(0.0, math.tau)
            el = math.radians(rng.uniform(-50.0, 70.0))
            at = _on_crown(cz, rx, rz, yaw, el, rng.uniform(0.20, 0.50))
            y2 = SCREEN_YAW + (math.pi if f % 2 else 0.0) + rng.uniform(-0.8, 0.8)
            parts.extend(F.tag_family(_leaf(f"fill_{f}", tones[-1], at, size * 1.35, y2,
                                            math.radians(rng.uniform(-30.0, 30.0)), curl=0.05, midrib=0.2,
                                            width_ratio=0.45, roll=_broadside_roll(y2, rng.uniform(-20.0, 10.0))), "foliage"))
        for pts, is_sec in twigs:
            L = _arc_len(pts)
            d = 0.02 if not is_sec else L * 0.15
            while d < L - 0.01:
                at = _along(pts, d)
                # the skirt stays thin where the limbs leave the fork, so they show
                if clear_skirt and at[2] < cz - rz * 0.72 and math.hypot(at[0], at[1]) < rx * 0.55:
                    d += 0.02
                    continue
                side = 1 if li % 2 == 0 else -1
                yaw = SCREEN_YAW + (0.0 if side > 0 else math.pi) + rng.uniform(-0.9, 0.9)
                put_leaf(at, size * rng.uniform(0.75, 1.25), yaw, math.radians(rng.uniform(-45.0, 30.0)), f"leaf_{li}")
                d += 0.020 + rng.uniform(0.0, 0.010)
            if not is_sec:
                # the tuft at the twig tip: a rosette of leaves fanning out and up
                tx, ty, tz = pts[-1]
                tx, ty = max(-0.45, min(0.45, tx)), max(-0.45, min(0.45, ty))
                n_tuft = rng.randint(5, 7)
                for k in range(n_tuft):
                    yaw = k * math.tau / n_tuft + rng.uniform(-0.3, 0.3)
                    at = (tx + math.cos(yaw) * 0.012, ty + math.sin(yaw) * 0.012, tz + rng.uniform(-0.01, 0.02))
                    put_leaf(at, size * rng.uniform(0.85, 1.15), yaw, math.radians(rng.uniform(-55.0, 15.0)), f"tuft_{li}")
        # the outer shell: leaves over the crown's whole front and rim. The painted crowns
        # are full rounded masses -- leaves everywhere, lit top-left, dark lower right,
        # small dark gaps -- and the twig leaves alone left ours ragged, with 25-40% fewer
        # leaf pixels; the shell sets the outline the silhouette constants assume.
        if stage >= 3:
            across_px = rx * _CROWN_ACROSS * 90.5
            leaf_px = (size * 90.5) * (size * 90.5 * 0.25) * 0.85
            n_shell = int(SHELL_COVER * math.pi * across_px * rz * 78.4 / max(1.0, leaf_px))
            for s_i in range(n_shell):
                el = math.asin(rng.uniform(-0.92, 0.98))               # even over the shell
                # the front half and the rim -- and, high up, all the way round: the camera
                # looks down 30 deg, so the crown's top BACK is its top edge on the sprite
                # (v22h: avocado, olive and coffee had a dark notch there)
                if math.sin(el) > 0.30:
                    yaw = rng.uniform(0.0, math.tau)
                else:
                    yaw = rng.uniform(-0.30 * math.pi, 1.30 * math.pi)
                at = _on_crown(cz, rx, rz, yaw, el, rng.uniform(0.88, 1.0))
                if clear_skirt and at[2] < cz - rz * 0.72 and math.hypot(at[0], at[1]) < rx * 0.55:
                    continue
                out = math.atan2(at[1], at[0]) if math.hypot(at[0], at[1]) > 1e-3 else rng.uniform(0, math.tau)
                rise = 30.0 * math.sin(el) + rng.uniform(-25.0, 25.0)
                put_leaf(at, size * rng.uniform(0.80, 1.20), out + rng.uniform(-1.0, 1.0),
                         -math.radians(rise), f"shell_{s_i}")
    # --- LAYER 3b: sparse white bloom as small flower dots on the front leaves ------------
    if want_leaves and flower_dots and fruit_spots and "flower_dot" in mats:
        n_fl = int(flower_dots * 900)
        for k, at in enumerate(_pick_spots(fruit_spots, cz, rx, rz, n_fl, rng, margin=0.02)):
            at = _clamp_tile((at[0] + _TOWARD[0] * 0.08, at[1] + _TOWARD[1] * 0.08, at[2] + _TOWARD[2] * 0.08))
            bpy.ops.mesh.primitive_uv_sphere_add(segments=6, ring_count=4, radius=1.0, location=at)
            fl = bpy.context.active_object
            fl.name = f"flower_{k}"
            s = rng.uniform(0.016, 0.024)
            fl.scale = (s, s, s * 0.7)
            fl.data.materials.append(mats["flower_dot"])
            bpy.ops.object.shade_flat()
            parts.append(F.tag_family(fl, "fruit"))
    # --- LAYER 4: the fruit, spread over the crown's front --------------------------------
    fr = design["fruit"]
    if want_fruit and fr > 0.0:
        shape_name = spec.get("shape", "sphere")
        shape = FRUIT_SHAPES[shape_name]
        style = shape.get("style", "hang")
        n_want = max(1, int(round(form["n"] * fr)))
        rr = form["fr"] * (0.92 if withered else 1.0)
        top_r = _fruit_top(shape_name, rr)
        stem_mat = mats["bark"]
        blush = (mats["fruit_blush"], spec["blush_z"]) if "fruit_blush" in mats else None
        style = form.get("style", style)
        spots = _pick_spots(fruit_spots, cz, rx, rz, n_want, rng, margin=rr * 1.6) if fruit_spots else \
            _front_points(cz, rx, rz, n_want, rng)
        for k, at in enumerate(spots):
            # sit the fruit in front of the leaves at that spot
            at = _clamp_tile((at[0] + _TOWARD[0] * 0.12, at[1] + _TOWARD[1] * 0.12, at[2] + _TOWARD[2] * 0.12))
            alt = (k % 3 == 1) or withered
            if style == "pair":
                # cherries: two on long stalks from one spur, the stalks a V
                # the painted pair: an inverted V of 1 px stalks 5-6 px long, the two
                # cherries a pixel apart (v22a's touched and read as one red bar)
                spur = (at[0], at[1], at[2] + rr * 3.6)
                for q, s in enumerate((-1.0, 1.0)):
                    c = _clamp_tile((at[0] + _ACROSS[0] * s * rr * 1.40, at[1] + _ACROSS[1] * s * rr * 1.40,
                                     at[2] - (0.0 if q == 0 else rr * 0.30)))
                    parts.append(F.tag_family(_sweep(f"tstem_{k}_{q}", stem_mat, [spur, (c[0], c[1], c[2] + top_r * 0.8)],
                                                     [0.0048, 0.0040], sides=4), "wood"))
                    parts.extend(F.tag_family(_fruit(f"tfruit_{k}_{q}", mats, c, rr * (1.0 if q == 0 else 0.94),
                                                     shape_name, rng, alt=alt, blush=blush, gleam=True), "fruit"))
            elif style == "cluster":
                # a clump: berries crowded irregularly round the spot, the nearer ones
                # in front (v22a's centre-plus-ring read as a red cross)
                per = form.get("per", shape.get("per", 3))
                offs = [(0.0, 0.0, 0.0)]
                while len(offs) < per:
                    a = rng.uniform(0.0, math.tau)
                    dd = rng.uniform(1.1, 1.9)
                    offs.append((math.cos(a) * dd, math.sin(a) * dd * 0.85, rng.uniform(-0.6, 0.6)))
                offs.sort(key=lambda o: o[2])
                for q, (ox, oy, oz) in enumerate(offs):
                    c = _clamp_tile((at[0] + _ACROSS[0] * ox * rr + _TOWARD[0] * oz * rr,
                                     at[1] + _ACROSS[1] * ox * rr + _TOWARD[1] * oz * rr,
                                     at[2] + oy * rr + _TOWARD[2] * oz * rr))
                    parts.extend(F.tag_family(_fruit(f"tfruit_{k}_{q}", mats, c, rr * rng.uniform(0.88, 1.06),
                                                     shape_name, rng, alt=(q % 2 == 1) or withered,
                                                     gleam=(q == 0)), "fruit"))
            else:
                hang = (at[0], at[1], at[2] - top_r * 0.35)
                parts.append(F.tag_family(_sweep(f"tstem_{k}", stem_mat,
                                                 [(hang[0], hang[1], hang[2] + top_r + 0.022),
                                                  (hang[0], hang[1], hang[2] + top_r * 0.85)],
                                                 [0.0032, 0.0028], sides=4), "wood"))
                parts.extend(F.tag_family(_fruit(f"tfruit_{k}", mats, hang, rr * rng.uniform(0.92, 1.06),
                                                 shape_name, rng, alt=alt, blush=blush, gleam=True), "fruit"))
    return parts


ARCHETYPES = {"tree": tree, "bush": bush, "broadleaf": broadleaf, "rosette": rosette,
              "trellis": trellis, "grain": grain, "clump": clump}
ROWS = {"tree": 0, "bush": 2, "broadleaf": 2, "rosette": 3, "trellis": 2, "grain": 3, "clump": 2}

#: The perennials -- every crop FruitFarming's conf gives a growBack. Their last column is
#: not a withered plant but the plant AFTER HARVEST. Vanilla's harvest() puts a growBack
#: crop back to an early stage (nbOfGrow growBack+1, the young tree of the first growth),
#: and a tree that shrank to a sapling each time it was picked was the complaint;
#: FF_farmingSprites.lua shows this column instead while a plant that has fruited regrows.
#: In play the healthy last column was never seen otherwise: with fullGrown 6 a ripe crop
#: left on the plant rots (deadSprite) before nbOfGrow reaches 8. The column is the
#: fruiting stage's own plant -- same seed, same geometry -- with the fruit taken off, so
#: a harvest takes the fruit away and nothing else.
AFTER_HARVEST = ("apple", "pear", "peach", "cherry", "orange", "lemon", "lime", "grapefruit",
                 "avocado", "mango", "olive", "coffee", "grape", "banana", "pineapple")
#: the fruit's parts per habit, by object-name prefix (every helper names its sub-parts
#: after the part: "tfruit_3_edge", "tfruit_3_gleam", "pine_2_edge", ...)
FRUIT_PARTS = {"tree": ("tfruit_", "tstem_"), "trellis": ("pedu_", "grape_"),
               "broadleaf": ("peduncle_", "rachis_", "nana_", "bract_"),
               "rosette": ("fstalk_", "pine_", "crown_")}


def _pick_fruit(parts, prefixes):
    """``parts`` without the fruit: the fruit's objects are deleted from the scene (an
    object left behind unparked would render in the first cell)."""
    kept = []
    for part in parts:
        if part.name.startswith(prefixes):
            bpy.data.objects.remove(part, do_unlink=True)
        else:
            kept.append(part)
    return kept


# --------------------------------------------------------------------------- #
# Materials
# --------------------------------------------------------------------------- #

#: How wide a leaf's own light/dark swing is, per habit -- measured, and NOT the same for
#: every crop. ``pzforge refsheet`` + the green-pixel percentiles give:
#:   BellPepper  v 0.310/0.357/0.455  (1.47x)  -- open canopy, contrast comes from the gaps
#:   Greenpeas   v 0.341/0.447/0.588  (1.72x)
#:   Barley      v 0.333/0.404/0.545  (1.64x)
#:   Cabbages    v 0.180/0.365/0.580  (3.2x)   -- a solid head, nothing but value separates
#:   Corn        v 0.192/0.294/0.631  (3.3x)   -- big blades, each one modelled in the round
#: A dense or big-leaved plant has to carry its form INSIDE the leaf, because it has no
#: background showing between them to do the job. Using the bush's narrow ramp everywhere
#: is what made the banana paddles and the pineapple rosette look like moulded plastic.
LEAF_RAMP = {
    "bush": (0.880, 1.170),
    "trellis": (0.820, 1.230),
    "grain": (0.830, 1.220),
    "rosette": (0.560, 1.560),
    "broadleaf": (0.540, 1.580),
    "clump": (0.780, 1.270),
}

#: A ripe cereal is not green. Barley's mature sheet spends its top regions on #838c1c /
#: #7d8b1b with #485a04 beneath -- an olive gold, culm and blade and ear alike -- and rice
#: ripens the same way. Painting the paddy in leaf green was the single most wrong thing
#: about that crop. Albedos are the refsheet's own S-face column, pulled down out of
#: clipping (it reports (0.61,0.71,0.03), which is at the top of the range).
STRAW_PAINTS = (
    (0.455, 0.520, 0.045),
    (0.420, 0.480, 0.042),
    (0.370, 0.430, 0.038),
    (0.315, 0.370, 0.034),
)
STRAW_STEM = (0.360, 0.415, 0.038)


def materials(spec):
    """Stage 1 of the forge workflow, through the measured classes only.

    Paints are ``pzforge spec`` inversions of the reference's brightest common shade --
    the one shade that is on the lit face -- so the lighting is not counted twice:
      foliage  vegetation_farming_01b_70  (72,112,72)  -> (0.175, 0.438, 0.175) on S
      soil     vegetation_farming_01b_64  (88,56,16)   -> (0.264, 0.107, 0.014) on S
      fruit    the crop's own vanilla produce icon hue (CROPS table), value in the
               band the tile art uses; BellPepper's own pepper inverts to
               (1.000, 0.175, 0.107) on S and the table's reds sit near that.
    Everything else -- swing, ramp mode, ramp range, projection, scale, the detail
    map's grammar -- is the class's measured grammar and is not overridden here.
    """
    from pzforge.texture import material_spec, write_surface_map
    foliage_map = write_surface_map(ROOT / "build" / "ff_foliage.png", 512, 512,
                                    material_spec("foliage", seed=19))
    soil_map = write_surface_map(ROOT / "build" / "ff_soil.png", 512, 512,
                                 material_spec("soil", seed=7))
    bark_map = write_surface_map(ROOT / "build" / "ff_bark.png", 512, 512,
                                 material_spec("bark", seed=11))
    leaf_paint = (0.124, 0.346, 0.128)
    # the reference's top five palette entries are five greens (#305030 #305838
    # #487048 #385838 #406040): neighbouring leaves differ in tone, so four paints
    # within the class swing, all on the reference's hue
    leaf_paints = [tuple(c * k for c in leaf_paint) for k in (1.00, 0.92, 0.85, 0.78)]
    fruit_gloss = FRUIT_SHAPES[spec.get("shape", "sphere")].get("gloss", (0.12, 0.55))
    alt_paint = (spec["fruit"][0] * 0.85, spec["fruit"][1] + spec["fruit"][0] * 0.10, spec["fruit"][2] * 0.90)
    edge_k = 0.40
    fruit_ao = None
    fruit_shading = None
    form = TREE_FORMS.get(spec.get("name"))
    tree_leaf_paints = ((0.38, 0.62, 0.12), (0.26, 0.47, 0.10), (0.17, 0.33, 0.08),
                        (0.10, 0.21, 0.06), (0.055, 0.115, 0.04))
    bark_paint = (0.100, 0.052, 0.023)
    bark_class = "bark"
    if spec.get("arch") == "tree" and form:
        # the painted fruit: deep paint, a 1 px highlight (a tight lobe -- v21's wide one
        # washed every cherry pink), a near-black rim; the alternate is a step darker
        fruit_gloss = form.get("gl", (0.60, 0.07))
        fruit_ao = (0.015, 0.50)
        fruit_shading = {"mode": "step",
                         "levels": {k: v * form.get("fl", 1.0) for k, v in F.TOON_LEVELS.items()}}
        alt_paint = tuple(c * 0.85 for c in spec["fruit"])
        edge_k = 0.30
        bark_paint = form["bark"]
        if form.get("bark_tex") == "birch":
            bark_map = write_surface_map(ROOT / "build" / "ff_bark_birch.png", 512, 512,
                                         material_spec("bark_birch", seed=13), grain_axis="u")
            bark_class = "bark_birch"
        from pzforge.spec import srgb_to_linear
        gain = [srgb_to_linear(form["crown"][i] / 255.0) / srgb_to_linear(CROWN_RESPONSE[i] / 255.0)
                for i in range(3)]
        if "seen" in form:
            gain = [g * srgb_to_linear(form["crown"][i] / 255.0) / srgb_to_linear(form["seen"][i] / 255.0)
                    for i, g in enumerate(gain)]
        tree_leaf_paints = tuple(tuple(min(1.0, c * g) for c, g in zip(pt, gain)) for pt in tree_leaf_paints)
    stem_paint = (leaf_paint[0] * 1.45, leaf_paint[1] * 1.28, leaf_paint[2] * 1.05)   # thin runs measure 0.967x the blades; a thin cylinder sits on the ramp's top stop, so the paint compensates
    blossom_paints = BLOSSOM_PAINTS
    if form and form.get("bloom") and form["bloom"][1]:
        # five tones of the crop's own blossom, lit crown to shaded interior
        bp = form["bloom"][1]
        blossom_paints = tuple(tuple(min(1.0, ch * k) for ch in bp) for k in (1.10, 0.95, 0.80, 0.62, 0.45))
    extra = {}
    if form and form.get("bloom") and form["bloom"][1] and form["bloom"][0] < 0.5:
        # a 2-3 px flower dot: lit bright and left undarkened -- the fruit class's rim and
        # contact shade turned v22k's first dots grey and lost them in the crown
        extra["flower_dot"] = F.forge_material(
            "ff_flower_dot", "fruit", form["bloom"][1], gloss=(0.0, 0.5), ao=(0.01, 0.0), rim=(0.01, 1.0),
            shading={"mode": "step", "levels": {k: v * 1.8 for k, v in F.TOON_LEVELS.items()}})
    if spec.get("blush"):
        extra["fruit_blush"] = F.forge_material("ff_fruit_blush", "fruit", spec["blush"], gloss=fruit_gloss, ao=fruit_ao,
                                                shading=fruit_shading)
    return {
        **extra,
        "leaves": [F.forge_material(f"ff_leaf{i}", "foliage", pt)
                   for i, pt in enumerate(leaf_paints)],
        "leaf": F.forge_material("ff_leaf", "foliage", leaf_paint, texture_path=str(foliage_map)),
        "tree_leaves": [F.forge_material(f"ff_tleaf{i}", "foliage", pt)
                        for i, pt in enumerate(tree_leaf_paints)],
        "stem": F.forge_material("ff_stem", "foliage", stem_paint, split=None,
                                 rim=(0.38, 0.70)),
        "dead": F.forge_material("ff_dead", "foliage", DEAD_PAINT, texture_path=str(foliage_map)),
        "straw_dry": [F.forge_material(f"ff_strawdry{i}", "foliage", (pt[0] * 0.62, pt[1] * 0.56, pt[2] * 0.9 + 0.02))
                      for i, pt in enumerate(STRAW_PAINTS)],
        "straw": [F.forge_material(f"ff_straw{i}", "foliage", pt, texture_path=str(foliage_map))
                  for i, pt in enumerate(STRAW_PAINTS)],
        "strawstem": F.forge_material("ff_strawstem", "foliage", STRAW_STEM),
        "flower": F.forge_material("ff_flower", "fruit", (0.80, 0.80, 0.72)),
        "calyx": F.forge_material("ff_calyx", "foliage", tuple(c * 0.70 for c in leaf_paint)),
        # the drawn rim round a fruit: the darkest foliage tone, not black
        "edge": F.forge_material("ff_edge", "foliage", tuple(c * 0.62 for c in leaf_paint)),
        "fruit": F.forge_material("ff_fruit", "fruit", spec["fruit"], gloss=fruit_gloss, ao=fruit_ao,
                                  shading=fruit_shading),
        "fruit_alt": F.forge_material("ff_fruit_alt", "fruit", alt_paint, gloss=fruit_gloss, ao=fruit_ao,
                                      shading=fruit_shading),
        "fruit_edge": F.forge_material("ff_fruit_edge", "fruit", tuple(c * edge_k for c in spec["fruit"]),
                                       gloss=(0.0, 0.5)),
        "unripe": F.forge_material("ff_unripe", "fruit", UNRIPE_PAINT),
        "bract": F.forge_material("ff_bract", "fruit", (0.20, 0.04, 0.10), gloss=(0.03, 0.7)),
        "soil": F.forge_material("ff_soil", "soil", (0.133, 0.047, 0.0045), texture_path=str(soil_map)),
        "wood": F.forge_material("ff_trellis", "wood", (0.42, 0.26, 0.12)),
        # tree-habit extras (kept for the tree archetype)
        "canopy": F.forge_material("ff_canopy", "foliage", tuple(c * 0.80 for c in leaf_paint),
                                   texture_path=str(foliage_map)),
        "bark": F.forge_material("ff_bark", bark_class, bark_paint, texture_path=str(bark_map)),
        "canopy_leaves": [F.forge_material(f"ff_cleaf{i}", "foliage", c) for i, c in enumerate(CANOPY_PAINTS)],
        "blossom": [F.forge_material(f"ff_blossom{i}", "fruit", c) for i, c in enumerate(blossom_paints)],
        "bloom": F.forge_material("ff_bloom", "fruit", BLOOM_PAINT),
        "shadow": F.forge_material("ff_shadow", "soil", (0.045, 0.040, 0.030)),
        "gleam": F.forge_material("ff_gleam", "fruit", (0.95, 0.92, 0.86), gloss=(0.0, 0.5), ao=(0.01, 0.0),
                                  rim=(0.01, 1.0),
                                  shading={"mode": "step", "levels": {k: v * 2.2 for k, v in F.TOON_LEVELS.items()}}),
        "blossom_core": F.forge_material("ff_blossom_core", "fruit", (0.42, 0.20, 0.30)),
    }


def _ramp(spec):
    return LEAF_RAMP.get(spec["arch"], LEAF_RAMP["bush"])


def leaf_texture():
    """Leaves are not flat paint. A vanilla leaf carries a lighter midrib running its
    length and a faint mottle either side of it, and that is surface texture, not
    shading -- so it belongs in a map, sampled along the blade."""
    from pzforge.texture import SurfaceSpec, write_surface_map
    path = ROOT / "build" / "ff_leaf.png"
    spec = SurfaceSpec(octaves=[(70, 1.00), (30, 0.55), (14, 0.30)],
                       vertical_stretch=6.0, contrast=1.35,
                       stroke_count=26, stroke_length=150, stroke_width=3,
                       stroke_amplitude=0.30, stroke_drift=0.05, seed=19)
    return write_surface_map(path, 256, 256, spec)


def canopy_texture():
    """Leaf dabs. The painted canopies decorrelate within 2-3 px, so the map's energy
    sits at 15-30 texel features (1 px = 12.5 texels at scale 2.2) with round dabs on
    top -- SurfaceSpec's daubs are exactly a painter's dab, so they carry the layer."""
    from pzforge.texture import SurfaceSpec, write_surface_map
    path = ROOT / "build" / "ff_canopy.png"
    spec = SurfaceSpec(octaves=[(64, 0.35), (30, 0.85), (17, 1.00), (10, 0.45)],
                       vertical_stretch=1.0, contrast=1.55,
                       daub_count=520, daub_radius=13, daub_depth=0.30, seed=23)
    return write_surface_map(path, 512, 512, spec)


def soil_texture():
    from pzforge.texture import SurfaceSpec, write_surface_map
    path = ROOT / "build" / "ff_soil.png"
    # Clods, not grain: the reference's soil is the most line-dense region of the sprite
    # (23.8% drawn-line pixels over the whole plant, concentrated in the bed), so the map
    # is big soft lumps with a coarse speckle rather than directional streaks.
    spec = SurfaceSpec(octaves=[(150, 0.30), (72, 0.45), (38, 0.80), (22, 1.00),
                               (13, 0.70)],
                       vertical_stretch=1.0, contrast=1.75, seed=7)
    return write_surface_map(path, 512, 512, spec)


# --------------------------------------------------------------------------- #

def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    crop = argv[0] if argv else "apple"
    global LAYER
    if "--layer" in argv:
        LAYER = argv[argv.index("--layer") + 1]
    if crop not in CROPS:
        raise SystemExit(f"unknown crop {crop!r}; known: {', '.join(sorted(CROPS))}")
    spec = dict(CROPS[crop], name=crop)
    out = ROOT / "build" / f"ff_{crop}_cells"

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    # detail maps come from the material classes' grammars (materials())
    F.register()
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    props = scene.pz_forge
    props.sheet_name = f"ff_{crop}_01"
    props.output_dir = str(out)
    props.footprint_x = STAGES
    props.footprint_y = 1
    props.facings = "1"
    # Each growth stage is an independent single-tile sprite that happens to be rendered
    # in one run, exactly like a wall set -- not one object spanning eight tiles.
    props.isolate_tiles = True
    props.show_guide = False
    props.contrast_boost = 1.0
    props.toon_shading = True
    # Soft edge, but only just. The reference's 0.677 soft-alpha share is produced by its
    # THIN GEOMETRY -- a 1 px petiole cannot cover a whole pixel -- not by a soft filter,
    # and a 2.1 px filter bought that number at the cost of the whole sprite: interior
    # detail smeared, and every 4-5 px gap between two leaves filled in by the two blurred
    # edges meeting in it. That is measurable: the canopy would not open past 2.5
    # background crossings per row however the geometry was spread, against the
    # reference's 4.91. Vanilla's interior transitions are 1 px.
    props.soft_edge = 1.05

    F.build_rig(bpy.context)
    scene.cycles.samples = 256
    scene.cycles.use_denoising = True

    mats = materials(spec)
    build = ARCHETYPES[spec["arch"]]
    rows = ROWS[spec["arch"]]
    subject = bpy.data.objects[F.SUBJECT_NAME]

    for stage in range(STAGES):
        # a perennial's last column is its fruiting plant after harvest (AFTER_HARVEST)
        after_harvest = stage == STAGES - 1 and crop in AFTER_HARVEST
        grown = STAGES - 2 if after_harvest else stage
        # Stable seed: Python's hash() is salted per process, which would reshuffle a
        # crop's leaves on every re-render and make pixel-diffing a refactor impossible.
        rng = random.Random(sum(ord(c) * (i + 7) for i, c in enumerate(crop)) * 1000 + grown)
        parts = soil_bed(mats["soil"], rng, rows=rows) if rows else []
        parts += build(grown, spec, mats, rng)
        if after_harvest:
            parts = _pick_fruit(parts, FRUIT_PARTS[spec["arch"]])
        for part in parts:
            part.location.x += stage * F.TILE   # grid +x, one tile per stage
            part["pz_tile"] = (stage, 0)       # this stage's cell, whatever hangs over
            part.parent = subject

    manifest = F.render_cells(bpy.context)
    print(f"rendered {len(manifest['cells'])} cell(s) for {crop} to {out}")


if __name__ == "__main__":
    main()
