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
DEAD_PAINT = (0.105, 0.088, 0.036)
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
    "apple":      dict(arch="bush", fruit=(0.62, 0.10, 0.05), fruit_r=0.080, leaf=1.00),
    "pear":       dict(arch="bush", fruit=(0.44, 0.50, 0.12), fruit_r=0.080, leaf=1.00),
    "peach":      dict(arch="bush", fruit=(0.60, 0.16, 0.07), fruit_r=0.075, leaf=0.95),
    "cherry":     dict(arch="bush", fruit=(0.88, 0.06, 0.09), fruit_r=0.036, leaf=0.80,
                       fruit_n=3, fruit_pairs=True, fruit_scale=0.85),
    "orange":     dict(arch="bush", fruit=(0.78, 0.28, 0.06), fruit_r=0.080, leaf=0.90),
    "lemon":      dict(arch="bush", fruit=(0.80, 0.66, 0.10), fruit_r=0.072, leaf=0.90),
    "lime":       dict(arch="bush", fruit=(0.38, 0.58, 0.10), fruit_r=0.067, leaf=0.90),
    "grapefruit": dict(arch="bush", fruit=(0.55, 0.13, 0.06), fruit_r=0.090, leaf=0.95),
    "avocado":    dict(arch="bush", fruit=(0.16, 0.26, 0.07), fruit_r=0.080, leaf=1.10,
                       fruit_pear=True),
    "mango":      dict(arch="bush", fruit=(0.52, 0.16, 0.05), fruit_r=0.084, leaf=1.15,
                       fruit_pear=True),
    # Small-fruited crops need MORE and slightly larger berries, not true-to-life ones:
    # at 2x a 0.026-radius olive is under 5 px and disappears into the canopy, which is
    # what made olive/coffee indistinguishable from the stone fruits in the v4 sheet.
    "olive":      dict(arch="bush", fruit=(0.30, 0.28, 0.05), fruit_r=0.041, leaf=0.70,
                       fruit_n=7),
    "coffee":     dict(arch="bush", fruit=(0.52, 0.10, 0.12), fruit_r=0.043, leaf=1.05,
                       fruit_n=7),
    "peanut":     dict(arch="bush", fruit=(0.34, 0.26, 0.10), fruit_r=0.038, leaf=0.85,
                       height_scale=0.55, fruit_n=5),
    # --- other habits -----------------------------------------------------
    "banana":     dict(arch="broadleaf", fruit=(0.72, 0.52, 0.08), fruit_r=0.036),
    "pineapple":  dict(arch="rosette", fruit=(0.62, 0.46, 0.06), fruit_r=0.102),
    "grape":      dict(arch="trellis", fruit=(0.30, 0.10, 0.16), fruit_r=0.024),
    "rice":       dict(arch="grain", fruit=(0.46, 0.40, 0.14), fruit_r=0.000),
    "ginger":     dict(arch="clump", fruit=(0.44, 0.34, 0.22), fruit_r=0.000),
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
    return _mesh_object(name, verts, faces, mat)


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
#: first node 0.22 above the soil, internode 0.11; and the branch fan, from the lowest
#: node up: angle at the base and at the tip (deg above horizontal). Half the fan
#: droops -- the lower branches leave below horizontal -- and the whole spans 130 deg.
NODE0, INTERNODE = 0.20, 0.098
#: Which layer to build (set from the command line: ``-- apple --layer stems``). Each
#: layer is rendered and read on its own before the next goes on.
LAYER = "all"

#: THE GROWTH DESIGN, per stage, decided before any geometry: (stems, height px above the
#: bed, nodes, lean of the three stems in degrees, carries). Heights are the reference's
#: measured rise; node counts are its node ladder (first node 16-18 px up, 7-10 px apart);
#: leans are read off vegetation_farming_01b_70's thin runs -- the three plants do NOT stand
#: parallel: one leans left, one is near upright, one leans right, and all bend at the top.
BUSH_GROWTH = (
    dict(stems=0, height=0,  nodes=0, lean=(0, 0, 0),      carries="bare bed"),
    dict(stems=3, height=6,  nodes=0, lean=(-4, 2, 5),     carries="two cotyledons on a hook"),
    dict(stems=3, height=11, nodes=1, lean=(-6, 3, 7),     carries="first true leaves"),
    dict(stems=3, height=27, nodes=2, lean=(-9, 4, 10),    carries="a fork, 4-5 leaves"),
    dict(stems=3, height=64, nodes=7, lean=(-12, 5, 14),   carries="flowers"),
    dict(stems=3, height=73, nodes=8, lean=(-14, 6, 16),   carries="flowers, small fruit"),
    dict(stems=3, height=73, nodes=8, lean=(-16, 7, 18),   carries="ripe fruit"),
    dict(stems=3, height=69, nodes=8, lean=(-22, 10, 24),  carries="withered, drooping"),
)
FAN = ((16, -34), (22, -24), (30, -10), (38, 2), (46, 12), (54, 22), (60, 32), (66, 42), (70, 50))


def _stem_path(base, height, lean_deg, node_zs, rng, kink_deg=11.0):
    """A main stem drawn the way the reference draws one: it LEANS as a whole (the three
    plants lean differently), it kinks a few degrees at every node -- a joint, not a
    wave -- and it bows over at the top under its own crown. Returned as points at every
    node plus midpoints so the branch bases sit exactly on the joints."""
    pts = [tuple(base)]
    x, y, z = base
    heading = math.radians(lean_deg)          # tilt of the stem in the screen plane
    zs = sorted(set([nz for nz in node_zs if nz < height] + [height]))
    prev = 0.0
    for i, nz in enumerate(zs):
        seg = nz - prev
        # the kink alternates sides and grows toward the crown; the last segment bows
        kink = math.radians(kink_deg) * (1 if i % 2 else -1) * (0.6 + 0.4 * nz / height)
        heading += kink
        if nz == height:
            heading += math.radians(lean_deg) * 0.6
        dx = math.sin(heading) * seg
        x += dx * 0.707
        y += dx * 0.707
        z = nz
        pts.append((x, y, z))
        prev = nz
    return pts


def _serpentine(base, height, r0, r1, rng, reversals=3, amp=0.075):
    """Kept for the other habits; the bush uses _stem_path."""
    pts = []
    segs = 4 * reversals
    ph = rng.uniform(0, math.tau)
    for i in range(segs + 1):
        u = i / segs
        w = amp * height * math.sin(u * math.pi * reversals + ph) * (0.4 + 0.6 * u)
        pts.append((base[0] + w * 0.707, base[1] - w * 0.707, base[2] + height * u))
    return pts, [r0 + (r1 - r0) * i / segs for i in range(segs + 1)]


def _branch(name, leaf_mats, stem_mat, base, length, yaw, rise_deg, bend_deg, leaf_size,
            rng, dead=False, fork=True, depth=0, leaves=True):
    """A cane with simple leaves on short bare petioles, forking once.

    The reference builds its foliage this way: 3.3-5 forks per plant, ~200 px of bare
    branch showing, and every blade a single lanceolate unit on a 3-5 px petiole, fanned
    over the whole elevation range. Blades are spaced so each has dark on both sides.
    """
    parts = []
    pts = _cane_path(base, length, yaw, rise_deg, bend_deg, segs=6)
    r0 = 0.0060 * (0.85 ** depth)
    parts.append(_sweep(name, stem_mat, pts, [r0 * (1.0 - 0.5 * i / 6) for i in range(7)], sides=5))
    n_leaf = (2 if length > 0.22 else 1) if leaves else 0
    for j in range(n_leaf):
        tt = 0.42 + 0.58 * j / max(1, n_leaf - 1)
        idx = min(6, max(1, int(round(tt * 6))))
        px_, py_, pz_ = pts[idx]
        side = -1 if j % 2 else 1
        lyaw = yaw + side * math.radians(rng.uniform(34.0, 62.0))
        pet = rng.uniform(0.035, 0.055)                    # the 3-5 px bare petiole
        lx, ly = px_ + math.cos(lyaw) * pet, py_ + math.sin(lyaw) * pet
        pitch = math.radians(rng.uniform(-64.0, 65.0)) if not dead else math.radians(rng.uniform(20.0, 50.0))
        parts.append(_stalk(f"{name}_pet{j}", stem_mat, (px_, py_, pz_), pet, 0.0032, 0.0026,
                            yaw=lyaw, lean=0.0))
        parts.extend(_leaf(f"{name}_lf{j}", leaf_mats[(j + depth) % len(leaf_mats)],
                           (lx, ly, pz_ + pet * 0.25), leaf_size * rng.uniform(0.80, 1.15), lyaw,
                           pitch, curl=rng.uniform(0.10, 0.42),
                           midrib=rng.uniform(0.45, 0.90),
                           width_ratio=rng.uniform(0.17, 0.27), droop=(0.22 if dead else 0.0),
                           roll=_broadside_roll(lyaw, rng.uniform(-28.0, 14.0))))
    tx, ty, tz = pts[-1]
    if leaves:
      parts.extend(_leaf(f"{name}_tip", leaf_mats[0], (tx, ty, tz), leaf_size * 0.9, yaw,
                       math.radians(-24.0 if not dead else 40.0), curl=0.22, width_ratio=0.27,
                       droop=(0.22 if dead else 0.0), roll=_broadside_roll(yaw, -8.0)))
    if fork and depth == 0 and length > 0.20:
        fi = 3
        fx, fy, fz = pts[fi]
        parts.extend(_branch(f"{name}_f", leaf_mats, stem_mat, (fx, fy, fz), length * 0.62,
                             yaw + math.radians(rng.choice((-1, 1)) * rng.uniform(28.0, 46.0)),
                             rise_deg + 22, bend_deg + 10, leaf_size * 0.9, rng, dead=dead,
                             fork=False, depth=1, leaves=leaves))
    return parts


def bush(stage, spec, mats, rng):
    """The BellPepper habit, built in layers: design -> stems -> leaves -> fruit."""
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
    for p, (px, py) in enumerate(((-0.19, -0.13), (0.04, -0.02), (0.17, 0.14))):
        ph = h * (0.92 + 0.14 * rng.random())
        maturity = min(1.0, ph / 0.86)
        # --- the node ladder, from the design ---------------------------------------
        n_nodes = design["nodes"]
        if n_nodes == 0:
            node_zs = []
        else:
            step_z = min(INTERNODE, max(0.045, (ph - 0.06) / max(1, n_nodes)))
            n0 = min(NODE0 * 0.75, ph * 0.38)
            node_zs = [n0 + i * step_z for i in range(n_nodes) if n0 + i * step_z < ph - 0.02]
        # --- LAYER 2: the stem ---------------------------------------------------------
        node_zs = [nz for nz in node_zs if rng.random() > 0.12 or nz == node_zs[0]]
        pts = _stem_path((px, py, 0.015), ph, design["lean"][p], node_zs, rng)
        r0 = 0.0125 * min(1.0, 0.55 + ph * 0.6)
        radii = [r0 * (1.0 - 0.62 * i / max(1, len(pts) - 1)) for i in range(len(pts))]
        parts.append(_sweep(f"stem_{p}", stem_mat, pts, radii, sides=6))
        # a joint bead at every node: the reference's stems thicken and darken a pixel
        # at each junction
        for i, nz in enumerate(node_zs):
            jx, jy = _at_height(pts, nz)
            parts.extend(_berry(f"joint_{p}_{i}", mats["calyx"], (jx, jy, nz), r0 * 0.9,
                                dimple=0.0))
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
        # --- branches from the joints: long, arcing, forking ----------------------------
        joints = []
        leaf_size = 0.152 * leaf_k * (0.84 + 0.16 * maturity)
        for i, nz in enumerate(node_zs):
            sx, sy = _at_height(pts, nz)
            rise_deg, bend_deg = FAN[min(i, len(FAN) - 1)]
            yaw = SCREEN_YAW + rng.uniform(-0.55, 0.55) + (math.pi if (i + p) % 2 else 0.0)
            u = nz / ph
            blen = (0.14 + 0.20 * (1.0 - u)) * leaf_k * (0.55 + 0.45 * maturity) * rng.uniform(0.75, 1.25)
            parts.extend(_branch(f"br_{p}_{i}", leaf_mats, stem_mat, (sx, sy, nz), blen, yaw,
                                 rise_deg, bend_deg, leaf_size, rng, dead=dead,
                                 fork=(u < 0.6), leaves=want_leaves))
            joints.append((sx, sy, nz, yaw, blen))
        if not node_zs and want_leaves:
            # seedling: a hook of stem with two cotyledons
            for s in (-1.0, 1.0):
                cyaw = SCREEN_YAW + s * 1.25
                parts.extend(_leaf(f"cot_{p}_{int(s > 0)}", leaf_mats[0],
                                   (px + math.cos(cyaw) * 0.02, py + math.sin(cyaw) * 0.02, ph * 0.92),
                                   leaf_size * 0.9, cyaw, math.radians(-30.0), curl=0.25,
                                   width_ratio=0.34, roll=_broadside_roll(cyaw)))
        if not want_fruit:
            continue
        # --- LAYER 4: flowers and fruit at the joints ------------------------------------
        fl = FLOWER_BY_STAGE[stage]
        if fl > 0.02 and joints:
            for k2 in range(int(round(6 * fl))):
                jx, jy, jz, jyaw, jc = joints[(k2 * 3 + 1) % len(joints)]
                parts.extend(_berry(f"flower_{p}_{k2}", mats["flower"],
                                    (jx + math.cos(jyaw) * jc * 0.40, jy + math.sin(jyaw) * jc * 0.40,
                                     jz + jc * 0.20), 0.024, dimple=0.0))
        fr = FRUIT_BY_STAGE[stage]
        if fr > 0.02 and joints:
            want = min(len(joints), max(1, int(round(spec.get("fruit_n", 3) * (0.45 + 0.55 * fr)))))
            step = max(1, len(joints) // want)
            r = 0.055 * spec.get("fruit_scale", 1.0) * (0.70 + 0.30 * fr)
            for k2 in range(want):
                jx, jy, jz, jyaw, jc = joints[min(len(joints) - 1, k2 * step)]
                out = jc * rng.uniform(0.30, 0.60)
                hx = jx + math.cos(jyaw) * out + _AWAY[0] * 0.03
                hy = jy + math.sin(jyaw) * out + _AWAY[1] * 0.03
                hz = jz - r * 0.6
                parts.append(_stalk(f"pedicel_{p}_{k2}", mats["calyx"], (hx, hy, hz + r * 0.5),
                                    r * 0.7, 0.0040, 0.0030))
                parts.extend(_berry(f"fruit_{p}_{k2}", mats["fruit"], (hx, hy, hz), r,
                                    pear=spec.get("fruit_pear", False),
                                    lobes=spec.get("fruit_lobes", 0),
                                    lobe_amp=spec.get("fruit_lobe_amp", 0.10),
                                    dimple=0.0, outline_mat=mats["edge"]))
    return parts


def _at_height(pts, z):
    """(x, y) of a stem path at height z, interpolated between its points."""
    for (x0, y0, z0), (x1, y1, z1) in zip(pts, pts[1:]):
        if z0 <= z <= z1:
            u = 0.0 if z1 == z0 else (z - z0) / (z1 - z0)
            return x0 + (x1 - x0) * u, y0 + (y1 - y0) * u
    return pts[-1][0], pts[-1][1]


def broadleaf(stage, spec, mats, rng):
    """Banana: a thick pseudostem carrying a crown of very large arching paddles.

    Reference habit: **Corn** (``vegetation_farming_01_78``), the only vanilla crop with
    this build. Measured: it rises 183 px above the bed -- nearly the whole cell -- its
    canopy is 121x203 with a fill of 0.285, it crosses background 6.90 times per row and
    a quarter of its ink is thin runs. So it is TALL and it is OPEN, and the old banana,
    at 91 px with nine leaves fanned radially round the top, was neither.
    """
    parts = []
    h = _rise(stage, 150.0)
    if h <= 0.001:
        return parts
    dead = stage == STAGES - 1
    leaf_mats = [mats["dead"]] if dead else mats["leaves"]
    stem_mat = mats["dead"] if dead else mats["stem"]
    mat = min(1.0, h / 2.0)
    # Two stools, spaced on (x+y) because that is what separates them on SCREEN.
    for p, (px, py) in enumerate(((-0.17, -0.15), (0.02, 0.01), (0.14, 0.18))):
        ph = h * (0.86 + 0.20 * rng.random()) * (0.80 + 0.13 * p)
        parts.append(_stalk(f"pstem_{p}", stem_mat, (px, py, 0.02), ph,
                            0.030 * (0.5 + 0.5 * mat), 0.017, lean=0.05,
                            yaw=SCREEN_YAW + p))
        # The crown is a LADDER, not a wheel: paddles leave the stem in alternating pairs
        # up its top half, longest at the bottom of the crown and shortening to the spike
        # at the tip, each one arched over and rolled to show its face.
        for k, (hfrac, dyaw, lscale, pdeg) in enumerate((
                (0.44, -0.55, 1.00, +20), (0.53, +0.42, 0.98, +13),
                (0.61, -0.30, 0.94, +6), (0.69, +0.62, 0.88, -2),
                (0.77, -0.46, 0.80, -11), (0.84, +0.24, 0.70, -21),
                (0.91, -0.62, 0.58, -31), (0.97, +0.36, 0.46, -42),
                (1.00, -0.12, 0.34, -54))):
            yaw = SCREEN_YAW + dyaw + (math.pi if k % 2 else 0.0)
            size = 0.315 * lscale * (0.42 + 0.58 * mat)
            parts.extend(_leaf(f"bleaf_{p}_{k}", leaf_mats[k % len(leaf_mats)],
                               (px + math.cos(yaw) * 0.024, py + math.sin(yaw) * 0.024,
                                0.02 + ph * hfrac),
                               size, yaw, math.radians(pdeg if not dead else pdeg + 40.0),
                               curl=0.40, width_ratio=0.24,
                               droop=(0.22 if dead else 0.0),
                               roll=_broadside_roll(yaw, -11.0 * (k % 3)),
                               outline_mat=mats["edge"]))
        if FRUIT_BY_STAGE[stage] > 0.4 and not dead:
            # A HAND of bananas hanging clear of the stem on a rachis, fingers curving up
            # -- two tiers of five. Stacked spheres on the trunk read as a painted band,
            # which is what the first pass produced.
            bz = 0.02 + ph * 0.42
            byaw = SCREEN_YAW + math.pi
            parts.append(_sweep(f"bunch_{p}", stem_mat,
                                _cane_path((px, py, bz + 0.09), 0.10, byaw, -20, -70,
                                           segs=4),
                                [0.011, 0.0098, 0.0086, 0.0074, 0.0062], sides=5))
            hx = px + math.cos(byaw) * 0.055
            hy = py + math.sin(byaw) * 0.055
            for tier in range(2):
                for g in range(5):
                    a = byaw + (g - 2) * 0.30
                    parts.append(_sweep(
                        f"nana_{p}_{tier}_{g}", mats["fruit"],
                        _cane_path((hx, hy, bz - 0.012 - tier * 0.055), 0.085, a,
                                   -34, 28, segs=4),
                        [spec["fruit_r"] * s for s in (0.55, 1.00, 1.05, 0.85, 0.35)],
                        sides=5))
    return parts


def rosette(stage, spec, mats, rng):
    """Pineapple: a ground rosette of stiff blades with one crowned fruit in the middle.

    Reference habit: **Cabbages** (``vegetation_farming_01_21``). Measured, and it is the
    opposite of the bush in every way that matters: fill 0.582 (not 0.232), 1.76 background
    crossings per row (not 4.91), and only 1.7% thin ink -- a leafy head shows no stem at
    all. Its tonal range is correspondingly WIDE (v 0.180/0.365/0.580) because with no
    background between the leaves the only thing separating them is value. So this
    archetype is deliberately dense and high contrast, and it would be a mistake to open it
    up the way the bush had to be opened.
    """
    parts = []
    h = _rise(stage, 62.0)
    if h <= 0.001:
        return parts
    dead = stage == STAGES - 1
    leaf_mats = [mats["dead"]] if dead else mats["leaves"]
    mat = min(1.0, h / 0.98)
    for p, (px, py) in enumerate(((-0.26, -0.18), (0.08, -0.16), (0.15, 0.24))):
        # Blades in three tiers: a low sprawling skirt, a mid ring, and a steep heart.
        # The tiers are what give a rosette its dome; one radial fan gave a starfish.
        for tier, (n, lift, pdeg, lscale) in enumerate((
                (7, 0.10, +26, 1.00), (6, 0.36, -18, 0.86), (5, 0.62, -52, 0.62))):
            for k in range(n):
                yaw = (k + 0.5 * tier) * math.tau / n + 0.30 * tier
                size = 0.215 * lscale * (0.40 + 0.60 * mat)
                parts.extend(_leaf(
                    f"blade_{p}_{tier}_{k}",
                    leaf_mats[(k + tier) % len(leaf_mats)],
                    (px + math.cos(yaw) * size * 0.20,
                     py + math.sin(yaw) * size * 0.20, 0.02 + h * lift),
                    size, yaw, math.radians(pdeg if not dead else pdeg + 44.0),
                    curl=0.30, width_ratio=0.20,
                    roll=_broadside_roll(yaw, -14.0 * tier),
                    outline_mat=mats["edge"]))
        if FRUIT_BY_STAGE[stage] > 0.4 and not dead:
            r = spec["fruit_r"]
            parts.extend(_berry(f"pine_{p}", mats["fruit"], (px, py, 0.05 + h * 0.52), r,
                                pear=True, lobes=8, lobe_amp=0.09, dimple=0.10,
                                outline_mat=mats["edge"]))
            # the crown is the pineapple's whole silhouette cue
            for c in range(5):
                yaw = c * math.tau / 5.0 + 0.4
                parts.extend(_leaf(f"crown_{p}_{c}", leaf_mats[c % len(leaf_mats)],
                                   (px, py, 0.05 + h * 0.52 + r * 1.15),
                                   r * 1.5, yaw, math.radians(-64.0),
                                   curl=0.22, width_ratio=0.16,
                                   roll=_broadside_roll(yaw), outline_mat=mats["edge"]))
    return parts


def trellis(stage, spec, mats, rng):
    """Grapes: vanilla builds trellised crops (Greenpeas, Tomato) with the wooden frame
    present from stage 0 and the vine climbing it.

    Reference habit: **Greenpeas** (``vegetation_farming_01_110``) -- canopy 107x145, fill
    0.232, 7.24 crossings per row and 44.6% thin ink. A trellised crop is mostly FRAME and
    CANE with leaves clipped to it; scattering leaves at random heights and yaws, as this
    did, produces a hedge draped over a fence instead.
    """
    parts = []
    dead = stage == STAGES - 1
    for i, y in enumerate((-0.24, 0.22)):
        for j, x in enumerate((-0.32, 0.32)):
            parts.append(_stalk(f"post_{i}_{j}", mats["wood"], (x, y, 0.0), 1.05,
                                0.018, 0.016))
        for k, z in enumerate((0.38, 0.70, 1.02)):
            bpy.ops.mesh.primitive_cylinder_add(vertices=6, radius=0.011, depth=0.70,
                                                location=(0.0, y, z))
            rail = bpy.context.active_object
            rail.name = f"rail_{i}_{k}"
            rail.rotation_euler = (0.0, math.radians(90.0), 0.0)
            rail.data.materials.append(mats["wood"])
            parts.append(rail)
    h = _rise(stage, 102.0)
    if h <= 0.001:
        return parts
    leaf_mats = [mats["dead"]] if dead else mats["leaves"]
    stem_mat = mats["dead"] if dead else mats["stem"]
    mat = min(1.0, h / 1.35)
    for i, y in enumerate((-0.26, 0.24)):
        for c, cx in enumerate((-0.22, 0.01, 0.23)):
            climb = min(1.05, h * 0.92)
            parts.append(_stalk(f"vine_{i}_{c}", stem_mat, (cx, y, 0.01), climb,
                                0.010, 0.006, tilt=0.07 * (1 if c % 2 else -1)))
            # TENDRILS. Mask the leaf regions out of Greenpeas with ``pzforge refsheet``
            # and what is left is hooks and spirals clinging to the frame -- the single
            # strongest read that a crop climbs rather than just standing next to a fence.
            if mat > 0.25:
                for tk, (tz, tyaw) in enumerate(((0.34, -0.5), (0.62, 2.3),
                                                 (0.86, 0.7))):
                    if tz > 0.10 + 0.95 * mat:
                        continue
                    parts.append(_tendril(
                        f"tendril_{i}_{c}_{tk}", stem_mat,
                        (cx, y, 0.02 + climb * tz), 0.16 * (0.6 + 0.4 * mat), 0.0052,
                        SCREEN_YAW + tyaw, turns=1.7))
            # leaves clipped to the cane at regular internodes, alternating sides, the
            # lower ones large and the tip ones small -- a vine, not a shrub
            for k, (hfrac, dyaw, lscale) in enumerate((
                    (0.16, -0.50, 1.00), (0.31, +0.66, 1.04), (0.46, -0.72, 0.98),
                    (0.60, +0.38, 0.92), (0.73, -0.44, 0.84), (0.85, +0.58, 0.72),
                    (0.96, -0.26, 0.58))):
                if hfrac > 0.10 + 0.95 * mat:
                    continue
                yaw = SCREEN_YAW + dyaw + (math.pi if k % 2 else 0.0)
                size = 0.20 * lscale * (0.50 + 0.50 * mat)
                parts.extend(_leaf(f"vleaf_{i}_{c}_{k}",
                                   leaf_mats[(k + c) % len(leaf_mats)],
                                   (cx + math.cos(yaw) * 0.055,
                                    y + math.sin(yaw) * 0.055, 0.02 + climb * hfrac),
                                   size, yaw,
                                   math.radians((-6.0 - 4.0 * k) if not dead else 30.0),
                                   curl=0.26, width_ratio=0.30,
                                   roll=_broadside_roll(yaw, -8.0 * (k % 2)),
                                   outline_mat=mats["edge"]))
            if FRUIT_BY_STAGE[stage] > 0.3 and not dead and c != 1:
                # a bunch is a CONE of berries on a short peduncle, widest at the top
                bz = 0.02 + climb * 0.56
                bx = cx + 0.045 * (1 if c else -1)
                parts.append(_stalk(f"pedu_{i}_{c}", stem_mat, (bx, y, bz), 0.05,
                                    0.007, 0.005))
                r = spec["fruit_r"] * 1.55
                tiers = ((5, 0.000, 1.00), (4, -0.034, 0.95), (4, -0.066, 0.88),
                         (3, -0.096, 0.80), (2, -0.122, 0.70), (1, -0.144, 0.58))
                for t, (n, dz, rs) in enumerate(tiers):
                    for g in range(n):
                        a = g * math.tau / n + 0.6 * t
                        rad = 0.0 if n == 1 else r * 1.35 * rs
                        parts.extend(_berry(
                            f"grape_{i}_{c}_{t}_{g}", mats["fruit"],
                            (bx + math.cos(a) * rad, y + math.sin(a) * rad * 0.7,
                             bz + dz - r * 0.6), r * rs, dimple=0.0))
    return parts


def grain(stage, spec, mats, rng):
    """Rice: the Barley habit -- rows of slim culms, each with a pair of blades and a
    drooping ear.

    Reference habit: **Barley** (``vegetation_farming_01b_6``). Measured: canopy 107x145,
    fill only 0.190, 6.43 crossings per row and **48.9% thin ink** -- half of a cereal
    sprite is line work. It rises 118 px above the bed. The job here is therefore the
    opposite of filling space: many thin culms, standing apart, read against the dark.
    """
    parts = []
    h = _rise(stage, 102.0)
    if h <= 0.001:
        return parts
    dead = stage == STAGES - 1
    # A cereal RIPENS: culm, blade and ear all turn straw together at the fruiting
    # stages, which is what the reference's mature sheet is painted in end to end.
    ripe = FRUIT_BY_STAGE[stage] > 0.3 and not dead
    leaf_mats = ([mats["dead"]] if dead else
                 (mats["straw"] if ripe else mats["leaves"]))
    stem_mat = (mats["dead"] if dead else
                (mats["strawstem"] if ripe else mats["stem"]))
    mat = min(1.0, h / 1.70)
    # Culms are placed on a fixed lattice and then nudged, so the paddy reads as SOWN
    # ROWS. Purely random x gave clumps and bald patches, which no cereal field has.
    for i, y in enumerate((-0.28, -0.02, 0.24)):
        for k in range(6):
            x = -0.26 + k * 0.104 + (0.024 if i == 1 else 0.0) + rng.uniform(-0.010, 0.010)
            ph = h * (0.84 + 0.30 * rng.random())
            tilt = rng.uniform(-0.10, 0.10)
            parts.append(_stalk(f"culm_{i}_{k}", stem_mat, (x, y, 0.02), ph, 0.0135,
                                0.0075, tilt=tilt))
            # Blades hug the culm and rise steeply; they do not radiate like a bush's.
            for b in range(2):
                yaw = SCREEN_YAW + (0.0 if b else math.pi) + rng.uniform(-0.14, 0.14)
                parts.extend(_leaf(f"blade_{i}_{k}_{b}",
                                   leaf_mats[(k + b) % len(leaf_mats)],
                                   (x + 0.010 * (1 if b else -1), y,
                                    ph * (0.26 + 0.30 * b)),
                                   0.185 * (0.45 + 0.55 * mat), yaw,
                                   math.radians(-78.0 if not dead else -34.0),
                                   curl=0.34, width_ratio=0.085,
                                   roll=_broadside_roll(yaw),
                                   outline_mat=mats["edge"]))
            if FRUIT_BY_STAGE[stage] > 0.3:
                # The seed head is the crop's whole silhouette cue, and in the reference
                # it is not a smooth cone: it is a spray of AWNS, a little starburst of
                # bristles at the top of each culm. That spray is most of what makes a
                # cereal field read as a cereal field at 2x.
                ez = ph * 0.90
                parts.append(_stalk(f"ear_{i}_{k}", mats["fruit"], (x, y, ez),
                                    0.13, 0.021, 0.006, tilt=tilt + 0.30, sides=6))
                for aw in range(5):
                    ayaw = SCREEN_YAW + (aw - 2) * 0.42
                    parts.append(_sweep(
                        f"awn_{i}_{k}_{aw}", mats["fruit"],
                        _cane_path((x + tilt * 0.10, y, ez + 0.10), 0.12, ayaw,
                                   78 - 6 * abs(aw - 2), 52 - 9 * abs(aw - 2), segs=4),
                        [0.0060, 0.0048, 0.0036, 0.0024, 0.0012], sides=4))
    return parts


def clump(stage, spec, mats, rng):
    """Ginger: a low clump of upright strap leaves, the SweetPotato habit.

    Reference habit: **SweetPotato** (``vegetation_farming_01_102``) -- canopy 109x101,
    fill 0.296, 6.03 crossings per row, 21.9% thin ink. A strap-leaved clump is open, and
    every strap runs from a visible sheath at the ground, which is where the thin ink comes
    from; the old version grew leaves out of thin air at random yaws.
    """
    parts = []
    h = _rise(stage, 86.0)
    if h <= 0.001:
        return parts
    dead = stage == STAGES - 1
    leaf_mats = [mats["dead"]] if dead else mats["leaves"]
    stem_mat = mats["dead"] if dead else mats["stem"]
    mat = min(1.0, h / 1.29)
    for p, (px, py) in enumerate(((-0.22, -0.17), (0.09, -0.15), (0.12, 0.21))):
        # one short sheath, then straps leaving it in a flat fan across the screen
        parts.append(_stalk(f"sheath_{p}", stem_mat, (px, py, 0.01), h * 0.30,
                            0.019, 0.011, yaw=SCREEN_YAW, lean=0.10))
        for k, (dyaw, lscale, pdeg, lift) in enumerate((
                (-0.86, 1.00, -34, 0.10), (+0.70, 0.94, -44, 0.16),
                (-0.50, 1.04, -54, 0.22), (+0.34, 0.98, -62, 0.28),
                (-0.18, 0.90, -70, 0.33), (+0.54, 0.86, -50, 0.19),
                (-0.68, 0.80, -40, 0.13), (+0.14, 0.74, -76, 0.36))):
            if k > 1 + int(6.5 * mat):
                continue
            yaw = SCREEN_YAW + dyaw + (math.pi if k % 2 else 0.0)
            size = 0.29 * lscale * (0.38 + 0.62 * mat)
            parts.extend(_leaf(f"strap_{p}_{k}", leaf_mats[k % len(leaf_mats)],
                               (px + math.cos(yaw) * 0.022, py + math.sin(yaw) * 0.022,
                                0.02 + h * lift),
                               size, yaw,
                               math.radians(pdeg if not dead else pdeg + 58.0),
                               curl=0.38, width_ratio=0.135,
                               droop=(0.20 if dead else 0.0),
                               roll=_broadside_roll(yaw, -10.0 * (k % 3)),
                               outline_mat=mats["edge"]))
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
    (0.30, 0.016, 0.34, 0.11, 0.13, 3, 0, 0),
    (0.48, 0.028, 0.56, 0.24, 0.30, 10, 0, 0),
    (0.58, 0.058, 0.96, 0.45, 0.66, 40, 0, 0),
    (0.66, 0.075, 1.38, 0.47, 1.02, 66, 0, 0),
    (0.66, 0.075, 1.38, 0.47, 1.02, 66, 24, 0),
    (0.66, 0.075, 1.38, 0.47, 1.02, 66, 24, 0),
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


def tree(stage, spec, mats, rng):
    """Fruit tree: trunk, limbs, a dabbed canopy, blossom then green then ripe fruit."""
    parts = []
    dead = False
    if stage == 0:
        bpy.ops.mesh.primitive_uv_sphere_add(segments=8, ring_count=5, radius=0.5,
                                             location=(0.0, 0.0, 0.035))
        seed = bpy.context.active_object
        seed.name = "seed"
        seed.scale = (0.072, 0.055, 0.045)
        seed.data.materials.append(mats["bark"])
        bpy.ops.object.shade_smooth()
        parts.append(F.tag_family(seed, "wood"))
        return parts
    if stage == 1:
        for k in range(3):
            yaw = SCREEN_YAW + (k - 1) * 0.9 + (math.pi if k == 1 else 0.0)
            parts.extend(F.tag_family(_leaf(
                f"sprout_{k}", mats["leaves"][k % len(mats["leaves"])],
                (0.0, 0.0, 0.02), 0.13, yaw, math.radians(-40.0), curl=0.30,
                width_ratio=0.30, roll=_broadside_roll(yaw)), "foliage"))
        parts.append(F.tag_family(_stalk("sprout_stem", mats["stem"], (0.0, 0.0, 0.0),
                                         0.06, 0.008, 0.006), "wood"))
        return parts

    th, tr, cz, rx, rz, n_cl, n_fruit, n_bloom = TREE_STAGES[stage]
    # ground: the painted sheets carry a small soft shadow under the trunk, not the
    # 0.87-tile diamond furniture gets; a flat dark disc does it
    bpy.ops.mesh.primitive_circle_add(vertices=14, radius=1.0, fill_type="NGON",
                                      location=(0.0, 0.0, 0.002))
    disc = bpy.context.active_object
    disc.name = "shadow"
    disc.scale = (0.20 + 0.12 * rx, 0.20 + 0.12 * rx, 1.0)
    disc.data.materials.append(mats["shadow"])
    parts.append(F.tag_family(disc, "soil"))
    # trunk: a serpentine sweep (vanilla stems reverse direction ~3 times over 80 px),
    # tapering to 0.6 of its base radius, then three limbs into the canopy
    pts = []
    segs = 8
    for i in range(segs + 1):
        u = i / segs
        wob = 0.035 * th * math.sin(u * math.pi * 3.0)
        pts.append((wob * 0.707, -wob * 0.707, th * u))
    parts.append(F.tag_family(_sweep("trunk", mats["bark"], pts,
                                     [tr * (1.0 - 0.4 * i / segs) for i in range(segs + 1)],
                                     sides=7), "wood"))
    for k in range(3):
        yaw = k * math.tau / 3.0 + 0.4
        limb = _cane_path((0.0, 0.0, th * 0.78), rz * 0.75, yaw, 62, 44, segs=5)
        parts.append(F.tag_family(_sweep(f"limb_{k}", mats["bark"], limb,
                                         [tr * 0.55 * (1.0 - 0.5 * i / 5) for i in range(6)],
                                         sides=5), "wood"))
    # canopy: a dark core cluster and a leaf shell per site. Sites are biased toward
    # the outside so the outline is lumpy and light shows through the middle (the
    # reference's canopies are ~75% filled), and every site is kept inside the tile:
    # centre distance + 1.2 x cluster radius <= 0.47, because the packer cuts at 0.5.
    leaf_mats = mats["blossom"] if stage == 5 else mats["canopy_leaves"]
    # The crown fills the tile's SQUARE, not a circle inside it: screen-horizontal is
    # the tile's (x+y) diagonal, so a crown that only reaches radius 0.47 is 60 px wide
    # on screen while the reference's is 120-128. Sites are drawn in the square
    # |x|,|y| <= 0.47 - 1.2 cr, biased to its edge, and the vertical profile is an
    # ellipse so the crown stays round in elevation.
    sites = []
    for k in range(n_cl):
        cr = rx * rng.uniform(0.20, 0.32)
        lim = max(0.02, 0.47 - cr * 1.85)
        # Sampled in the SCREEN frame. Screen-horizontal is the tile's (x+y) diagonal
        # and screen-depth its (x-y) diagonal; the square |x|,|y| <= lim is the diamond
        # |h| + |d| <= lim*sqrt(2) in those. Drawing x and y independently put most
        # sites on the depth diagonal, where the camera foreshortens them, and the
        # crown came out 90 px wide against the reference's 125. Here h is drawn wide
        # with an edge bias and d takes what the diamond leaves.
        span = lim * math.sqrt(2.0)
        h = span * (rng.random() ** 0.55) * (1.0 if rng.random() < 0.5 else -1.0)
        d = (span - abs(h)) * rng.uniform(-0.9, 0.9)
        cx = (h + d) / math.sqrt(2.0)
        cy = (h - d) / math.sqrt(2.0)
        h = (abs(h) / span) ** 2                          # 0 centre .. 1 at the sides
        vz = ((rz - cr * 0.6) * math.sqrt(max(0.0, 1.0 - h))
              * (rng.random() ** 0.7) * (1.0 if rng.random() < 0.56 else -0.85))
        sites.append((cx, cy, cz + vz, cr))
    # twigs: dark sweeps from the limb tips out to the shells, the visible wood the
    # reference threads through its canopy (4.3% thin structure)
    for k, (cx, cy, czz, cr) in enumerate(sites[::3]):
        base = (cx * 0.35, cy * 0.35, th * 0.85 + (czz - th * 0.85) * 0.35)
        pts = [base, ((base[0] + cx) * 0.5, (base[1] + cy) * 0.5, (base[2] + czz) * 0.5 + 0.03),
               (cx, cy, czz)]
        parts.append(F.tag_family(_sweep(f"twig_{k}", mats["bark"], pts,
                                         [tr * 0.30, tr * 0.22, tr * 0.12], sides=4), "wood"))
    for k, (cx, cy, czz, cr) in enumerate(sites):
        parts.append(F.tag_family(_cluster(
            f"core_{k}", mats["blossom_core"] if stage == 5 else mats["canopy"],
            (cx, cy, czz), cr * 0.80, rng), "foliage"))
        parts.append(F.tag_family(_leaf_shell(f"shell_{k}", leaf_mats, (cx, cy, czz), cr,
                                              int(12 + 18 * cr / rx), rng), "foliage"))
    for k in range(n_bloom):
        x, y, z = _on_canopy(rng, cz, rx, rz, depth=0.98)
        parts.extend(F.tag_family(_berry(f"bloom_{k}", mats["bloom"], (x, y, z), 0.024,
                                         dimple=0.0), "flower"))
    # fruit: 7-8 px across in the reference (r 0.04 world), 21-24 per crown, on the
    # camera-facing shell and only a little buried, each with the drawn rim
    fr_mat = mats["unripe"] if stage == 6 else mats["fruit"]
    for k in range(n_fruit):
        x, y, z = _on_canopy(rng, cz, rx, rz, depth=rng.uniform(0.86, 0.98), spread=1.45)
        # the crown is a square on the ground, so the face point is scaled out along the
        # screen's horizontal diagonal and clamped to the tile
        s = rng.uniform(1.0, 1.30)
        hx = (x + y) * 0.5 * s
        dd = (x - y) * 0.5
        x, y = max(-0.42, min(0.42, hx + dd)), max(-0.42, min(0.42, hx - dd))
        r = 0.040 * rng.uniform(0.85, 1.15) * spec.get("fruit_scale", 1.0)
        pair = spec.get("fruit_pairs", False)
        for j in range(2 if pair else 1):
            ox = (j - 0.5) * r * 1.6 * 0.707 if pair else 0.0
            oy = -(j - 0.5) * r * 1.6 * 0.707 if pair else 0.0
            parts.extend(F.tag_family(_berry(f"fruit_{k}_{j}", fr_mat, (x + ox, y + oy, z), r,
                                             pear=spec.get("fruit_pear", False),
                                             dimple=0.0, outline_mat=mats["edge"]), "fruit"))
            # the painter's highlight dot, up and toward the light
            parts.extend(F.tag_family(_berry(f"gleam_{k}_{j}", mats["gleam"],
                                             (x + ox + 0.35 * r, y + oy - 0.35 * r, z + 0.45 * r),
                                             r * 0.30, dimple=0.0), "fruit"))
        # the stem the pair hangs from
        parts.append(F.tag_family(_stalk(f"pedicel_{k}", mats["bark"], (x, y, z + r * 0.6),
                                         r * 1.4, 0.006, 0.004, lean=0.6), "wood"))
    return parts


ARCHETYPES = {"tree": tree, "bush": bush, "broadleaf": broadleaf, "rosette": rosette,
              "trellis": trellis, "grain": grain, "clump": clump}
ROWS = {"tree": 0, "bush": 2, "broadleaf": 2, "rosette": 3, "trellis": 2, "grain": 3, "clump": 2}


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
    leaf_paint = (0.148, 0.367, 0.152)
    # the reference's top five palette entries are five greens (#305030 #305838
    # #487048 #385838 #406040): neighbouring leaves differ in tone, so four paints
    # within the class swing, all on the reference's hue
    leaf_paints = [tuple(c * k for c in leaf_paint) for k in (1.00, 0.92, 0.85, 0.78)]
    stem_paint = tuple(c * 0.82 for c in leaf_paint)   # thin runs measure 0.967x the blades; a thin cylinder sits on the ramp's top stop, so the paint compensates
    return {
        "leaves": [F.forge_material(f"ff_leaf{i}", "foliage", pt, texture_path=str(foliage_map))
                   for i, pt in enumerate(leaf_paints)],
        "leaf": F.forge_material("ff_leaf", "foliage", leaf_paint, texture_path=str(foliage_map)),
        "stem": F.forge_material("ff_stem", "foliage", stem_paint, split=None,
                                 rim=(0.38, 0.70)),
        "dead": F.forge_material("ff_dead", "foliage", DEAD_PAINT, texture_path=str(foliage_map)),
        "straw": [F.forge_material(f"ff_straw{i}", "foliage", pt, texture_path=str(foliage_map))
                  for i, pt in enumerate(STRAW_PAINTS)],
        "strawstem": F.forge_material("ff_strawstem", "foliage", STRAW_STEM),
        "flower": F.forge_material("ff_flower", "fruit", (0.80, 0.80, 0.72)),
        "calyx": F.forge_material("ff_calyx", "foliage", tuple(c * 0.70 for c in leaf_paint)),
        # the drawn rim round a fruit: the darkest foliage tone, not black
        "edge": F.forge_material("ff_edge", "foliage", tuple(c * 0.62 for c in leaf_paint)),
        "fruit": F.forge_material("ff_fruit", "fruit", spec["fruit"]),
        "unripe": F.forge_material("ff_unripe", "fruit", UNRIPE_PAINT),
        "soil": F.forge_material("ff_soil", "soil", (0.133, 0.047, 0.0045), texture_path=str(soil_map)),
        "wood": F.forge_material("ff_trellis", "wood", (0.42, 0.26, 0.12)),
        # tree-habit extras (kept for the tree archetype)
        "canopy": F.forge_material("ff_canopy", "foliage", tuple(c * 0.80 for c in leaf_paint),
                                   texture_path=str(foliage_map)),
        "bark": F.forge_material("ff_bark", "wood", (0.120, 0.072, 0.036)),
        "canopy_leaves": [F.forge_material(f"ff_cleaf{i}", "foliage", c) for i, c in enumerate(CANOPY_PAINTS)],
        "blossom": [F.forge_material(f"ff_blossom{i}", "fruit", c) for i, c in enumerate(BLOSSOM_PAINTS)],
        "bloom": F.forge_material("ff_bloom", "fruit", BLOOM_PAINT),
        "shadow": F.forge_material("ff_shadow", "soil", (0.045, 0.040, 0.030)),
        "gleam": F.forge_material("ff_gleam", "fruit", (0.95, 0.90, 0.85)),
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
    spec = CROPS[crop]
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
        # Stable seed: Python's hash() is salted per process, which would reshuffle a
        # crop's leaves on every re-render and make pixel-diffing a refactor impossible.
        rng = random.Random(sum(ord(c) * (i + 7) for i, c in enumerate(crop)) * 1000 + stage)
        parts = soil_bed(mats["soil"], rng, rows=rows) if rows else []
        parts += build(stage, spec, mats, rng)
        for part in parts:
            part.location.x += stage * F.TILE   # grid +x, one tile per stage
            part.parent = subject

    manifest = F.render_cells(bpy.context)
    print(f"rendered {len(manifest['cells'])} cell(s) for {crop} to {out}")


if __name__ == "__main__":
    main()
