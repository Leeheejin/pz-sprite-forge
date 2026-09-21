"""Synthesise surface detail maps with the spatial statistics vanilla tiles have.

Measuring where a recreation loses its contrast (``tools/analyze_scales.py``) shows
the shortfall is not spread evenly. Against the vanilla drum the recreation matched
at 1-8 px and fell short in exactly two bands:

* **coarse, above 8 px** -- 0.044 against 0.096, which is 95% of the total gap. This
  is large tonal patching: dirt, paint wear, whole panels reading lighter or darker.
* **per-pixel, 0-1 px** -- 0.001 against 0.013. A path-traced surface is smooth; the
  painted original is not.

Shader noise struggles with the first because it has no control over how much energy
lands in which band. Generating the map directly does: each octave is drawn at its own
frequency and weighted, so the output can be aimed at the measured profile
(``reference/scale_profile.json``).

The per-pixel band is *not* handled here. Detail that fine cannot survive being
rendered -- the sampler averages it away -- so it belongs in the style pass, at sprite
resolution, where :func:`pzforge.style.add_grain` applies it.

This module writes its PNG with the standard library alone, so it runs inside
Blender's bundled Python -- which has no Pillow -- and a model script can regenerate
its own textures at render time.
"""

from __future__ import annotations

import json
import math
import random
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

SCALE_PROFILE = Path(__file__).resolve().parents[1] / "reference" / "scale_profile.json"


def load_scale_profile(path: Path | None = None) -> dict:
    path = path or SCALE_PROFILE
    if path.exists():
        return json.loads(path.read_text())
    return {"octaves": {"0->1": {"p50": 0.0078}, "coarse": {"p50": 0.0510}}}


@dataclass
class SurfaceSpec:
    """Weights per feature size, in texture pixels."""

    #: (feature size in px, amplitude) -- amplitudes are relative and normalised.
    octaves: list[tuple[int, float]] = field(default_factory=lambda: [
        (96, 1.00),   # whole-panel tonal blocks: the band vanilla has and renders lack
        (48, 0.55),
        (24, 0.30),
        (12, 0.18),
    ])
    #: >1 stretches features vertically, which is how weathering runs on a barrel.
    vertical_stretch: float = 3.0
    #: Pushes the histogram toward its ends, so patches read as patches, not haze.
    contrast: float = 1.6
    #: Brush strokes drawn over the noise field. Value noise, however stretched, still
    #: reads as blotches; vanilla's wear on metal runs in coherent vertical *streaks*
    #: with hard sides, which only actual drawn strokes produce. ``stroke_count = 0``
    #: disables the layer.
    stroke_count: int = 0
    stroke_length: int = 80
    stroke_width: int = 2
    stroke_amplitude: float = 0.16
    #: Horizontal drift per step, so streaks waver rather than ruling straight lines.
    stroke_drift: float = 0.18
    #: Knots drawn into the field: dark radial cores with a faint halo, elongated
    #: along the grain axis. ``knot_count = 0`` disables them.
    knot_count: int = 0
    knot_radius: int = 14
    knot_depth: float = 0.55
    #: Brush daubs: soft round dabs, lighter or darker, no halo and no direction.
    #: This is the hand-shaded modulation cloth carries -- painted unevenness that
    #: neither value noise (too uniform) nor strokes (directional) can make.
    #: ``daub_count = 0`` disables them.
    daub_count: int = 0
    daub_radius: int = 26
    daub_depth: float = 0.14
    #: Brick bond: horizontal courses with staggered head joints. Measured on
    #: walls_exterior_house_01: course 8 px on screen at 2x, mortar 1-2 px and
    #: ~13 levels LIGHTER than the brick, subtle per-brick tone shifts.
    #: ``brick_course = 0`` disables the layer. Sizes are texture px.
    brick_course: int = 0
    brick_length: int = 48
    brick_mortar_px: int = 4
    brick_jitter: float = 0.10
    seed: int = 7


def _value_noise(width: int, height: int, cells_x: int, cells_y: int,
                 rng: random.Random) -> list[list[float]]:
    """Smooth noise from a coarse lattice, wrapping horizontally so it can tile."""
    cells_x = max(1, cells_x)
    cells_y = max(1, cells_y)
    lattice = [[rng.random() for _ in range(cells_x)] for _ in range(cells_y + 1)]

    def smooth(t: float) -> float:
        return t * t * (3.0 - 2.0 * t)

    out = []
    for y in range(height):
        fy = y / height * cells_y
        y0 = int(fy)
        ty = smooth(fy - y0)
        row_a = lattice[min(y0, cells_y)]
        row_b = lattice[min(y0 + 1, cells_y)]
        row = []
        for x in range(width):
            fx = x / width * cells_x
            x0 = int(fx)
            tx = smooth(fx - x0)
            x1 = (x0 + 1) % cells_x
            top = row_a[x0 % cells_x] * (1 - tx) + row_a[x1] * tx
            bottom = row_b[x0 % cells_x] * (1 - tx) + row_b[x1] * tx
            row.append(top * (1 - ty) + bottom * ty)
        out.append(row)
    return out


def _write_grey_png(path: Path, width: int, height: int, rows: list[bytes]) -> None:
    """Minimal 8-bit greyscale PNG writer, so no imaging library is needed."""
    raw = b"".join(b"\x00" + row for row in rows)  # filter type 0 per scanline

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", header)
                     + chunk(b"IDAT", zlib.compress(raw, 9))
                     + chunk(b"IEND", b""))


def surface_rows(width: int = 512, height: int = 256,
                 spec: SurfaceSpec | None = None) -> list[bytes]:
    """A tileable greyscale detail map, one bytes object per row."""
    spec = spec or SurfaceSpec()
    rng = random.Random(spec.seed)

    total = sum(amplitude for _size, amplitude in spec.octaves) or 1.0
    accumulated = [[0.0] * width for _ in range(height)]
    for size, amplitude in spec.octaves:
        cells_x = max(1, round(width / max(1, size)))
        cells_y = max(1, round(height / max(1, size * spec.vertical_stretch)))
        layer = _value_noise(width, height, cells_x, cells_y, rng)
        weight = amplitude / total
        for y in range(height):
            acc_row = accumulated[y]
            layer_row = layer[y]
            for x in range(width):
                acc_row[x] += layer_row[x] * weight

    flat = [v for row in accumulated for v in row]
    lo, hi = min(flat), max(flat)
    span = max(hi - lo, 1e-6)

    # Normalise first, then draw strokes on the 0-1 field so their amplitude means
    # what it says instead of being rescaled away.
    field01 = [[(v - lo) / span for v in row] for row in accumulated]
    if spec.stroke_count > 0:
        for _ in range(spec.stroke_count):
            fx = rng.uniform(0.0, width)
            y = rng.randrange(height)
            length = max(4, round(rng.uniform(0.5, 1.5) * spec.stroke_length))
            delta = (rng.uniform(0.45, 1.0) * spec.stroke_amplitude
                     * rng.choice((-1.0, 1.0)))
            drift = rng.uniform(-spec.stroke_drift, spec.stroke_drift)
            for step in range(length):
                yy = y + step
                if yy >= height:
                    break
                taper = 1.0 - 0.6 * (step / max(1, length - 1))
                for k in range(spec.stroke_width):
                    xx = (round(fx) + k) % width   # wraps, so the map stays tileable
                    field01[yy][xx] = min(1.0, max(0.0,
                                                   field01[yy][xx] + delta * taper))
                fx += drift
    # Knots: radial dark cores with a faint brighter halo, elongated along the
    # grain axis (V, the stretch/stroke direction). Value noise and strokes make
    # fibre; only a drawn feature makes the eye read *timber* -- the same lesson
    # as the strokes themselves, one scale up.
    if spec.knot_count > 0:
        for _ in range(spec.knot_count):
            cx = rng.uniform(0.0, width)
            cy = rng.uniform(0.0, height)
            r = max(3.0, rng.uniform(0.6, 1.4) * spec.knot_radius)
            for dy in range(-int(r * 2), int(r * 2) + 1):
                for dx in range(-int(r), int(r) + 1):
                    # elongated 2x along V, wrapping both axes for tileability
                    d = ((dx / r) ** 2 + (dy / (2.0 * r)) ** 2) ** 0.5
                    if d > 1.0:
                        continue
                    xx = int(cx + dx) % width
                    yy = int(cy + dy) % height
                    if d < 0.55:
                        delta = -spec.knot_depth * (1.0 - d / 0.55)
                    else:
                        # the halo: growth rings pushed aside read slightly light
                        delta = 0.25 * spec.knot_depth * (1.0 - abs(d - 0.75) / 0.25)
                    field01[yy][xx] = min(1.0, max(0.0, field01[yy][xx] + delta))

    if spec.daub_count > 0:
        for _ in range(spec.daub_count):
            cx = rng.uniform(0.0, width)
            cy = rng.uniform(0.0, height)
            r = max(4.0, rng.uniform(0.55, 1.5) * spec.daub_radius)
            depth = rng.uniform(0.4, 1.0) * spec.daub_depth * rng.choice((-1.0, 1.0))
            for dy in range(-int(r * 1.3), int(r * 1.3) + 1):
                for dx in range(-int(r), int(r) + 1):
                    d = ((dx / r) ** 2 + (dy / (1.3 * r)) ** 2) ** 0.5
                    if d > 1.0:
                        continue
                    xx = int(cx + dx) % width
                    yy = int(cy + dy) % height
                    fall = (1.0 - d) ** 1.5
                    field01[yy][xx] = min(1.0, max(0.0,
                                                   field01[yy][xx] + depth * fall))

    # Bricks replace the field inside each brick with a damped copy plus a
    # stable per-brick tone, and pin every mortar joint to the map's light end;
    # the paint ramp then decides what "light" means (vanilla: mortar is the
    # LIGHTER shade). Head joints stagger half a brick per course and both axes
    # wrap, so the map still tiles.
    if spec.brick_course > 0:
        course = max(2, spec.brick_course)
        blen = max(4, spec.brick_length)
        mortar = max(1, spec.brick_mortar_px)
        n_cols = max(1, width // blen)
        n_rows = max(1, height // course)
        # Every brick gets its own tone; roughly one in six is an ACCENT brick
        # pushed well off the base band -- the few clearly-darker bricks are
        # what makes a bond read as individual bricks instead of a fine mesh.
        tones = {}
        for r in range(n_rows):
            for c in range(n_cols):
                rng_b = random.Random(spec.seed * 7919 + r * 131 + c)
                tone = rng_b.uniform(-spec.brick_jitter, spec.brick_jitter)
                if rng_b.random() < 0.17:
                    tone += rng_b.choice((-1.0, 1.0)) * 1.6 * spec.brick_jitter
                tones[(r, c)] = tone
        for y in range(height):
            r = (y // course) % n_rows
            joint_y = (y % course) < mortar
            shift = blen // 2 if (y // course) % 2 else 0
            row = field01[y]
            for x in range(width):
                xs = (x + shift) % width
                c = (xs // blen) % n_cols
                if joint_y or (xs % blen) < mortar:
                    row[x] = 1.0
                else:
                    # The body stays well below the joints: sampling averages
                    # a joint with its neighbours (~3 texels land on a sprite
                    # px), so the separation must survive a ~40% blur.
                    row[x] = 0.24 + 0.18 * row[x] + tones[(r, c)]

    lo2 = min(v for row in field01 for v in row)
    hi2 = max(v for row in field01 for v in row)
    span2 = max(hi2 - lo2, 1e-6)

    rows = []
    for row in field01:
        out = bytearray(width)
        for x, v in enumerate(row):
            normalised = (v - lo2) / span2
            # Symmetric S-curve about the midpoint; contrast 1.0 leaves it alone.
            centred = normalised - 0.5
            shaped = math.copysign(abs(centred * 2.0) ** (1.0 / spec.contrast), centred)
            out[x] = max(0, min(255, round((shaped / 2.0 + 0.5) * 255)))
        rows.append(bytes(out))
    return rows


def bolden(spec: SurfaceSpec, factor: float = 1.5) -> SurfaceSpec:
    """Scale a spec's features a size class up: bigger, fewer, stronger.

    In game the tiles render far smaller than the working zoom, so material
    features drawn at parity with the reference wash out on screen -- the
    material read survives only if its elements are a size class bolder than
    a 1:1 reading suggests. Feature sizes and widths scale by ``factor``,
    counts scale down to keep coverage, and amplitudes gain a moderate boost.
    Brick bond fields are left alone -- the bond is already drawn bold.
    """
    from dataclasses import replace
    gain = 1.0 + 0.4 * (factor - 1.0)
    return replace(
        spec,
        octaves=[(max(4, round(size * factor)), amp)
                 for size, amp in spec.octaves],
        stroke_count=round(spec.stroke_count / factor),
        stroke_length=max(4, round(spec.stroke_length * factor)),
        stroke_width=max(1, round(spec.stroke_width * factor)),
        stroke_amplitude=spec.stroke_amplitude * gain,
        knot_radius=max(3, round(spec.knot_radius * factor)),
        knot_depth=min(1.0, spec.knot_depth * gain),
        daub_count=round(spec.daub_count / factor),
        daub_radius=max(4, round(spec.daub_radius * factor)),
        daub_depth=min(1.0, spec.daub_depth * gain),
    )


def material_spec(material: str, seed: int = 7) -> "SurfaceSpec":
    """The measured texture grammar for a material class.

    Derived from the corpus signatures (reference/material_signatures.json):
    metal carries directional streak wear over small octaves; wood is fibre --
    long strokes plus knots -- with the highest local gradient (0.0157) and
    saturation (0.61); fabric is the *smoothest* class (median local gradient
    0.0078, barely half of wood's), so its map is faint broad mottle with no
    strokes at all. Its wide global spread (0.347) comes from form shading over
    stuffed curvature, not from the map. Sizes assume roughly 4 texture px per
    sprite px; scale stroke widths with the actual density.
    """
    if material == "metal":
        return bolden(SurfaceSpec(
            octaves=[(64, 1.00), (32, 0.65), (16, 0.32), (8, 0.18)],
            vertical_stretch=2.0, contrast=1.9,
            stroke_count=170, stroke_length=90, stroke_width=2,
            stroke_amplitude=0.17, seed=seed))
    if material == "wood":
        return bolden(SurfaceSpec(
            octaves=[(48, 0.45), (24, 0.30), (12, 0.22)],
            vertical_stretch=7.0, contrast=1.6,
            stroke_count=300, stroke_length=220, stroke_width=8,
            stroke_amplitude=0.42, knot_count=4, knot_radius=8,
            knot_depth=0.32, seed=seed))
    if material == "brick":
        # Regular bond dominates; the noise only shifts tone brick to brick.
        # Long bricks -- 3.5 courses, ~28 screen px -- and a wide per-brick
        # tone band: short bricks with timid jitter read as a fine dense mesh
        # instead of masonry. Course height stays at the measured 8 screen px.
        return SurfaceSpec(octaves=[(91, 0.6), (26, 0.4)],
                           vertical_stretch=1.0, contrast=1.0,
                           brick_course=26, brick_length=91,
                           brick_mortar_px=10, brick_jitter=0.12, seed=seed)
    if material == "fabric":
        # Daub-dominated: cloth's feel is hand-dabbed shading unevenness plus a
        # whisper of large drift -- see the sofa reference read at 6x.
        # Boldened by DEPTH, not size: scaling the daubs up in radius smoothed
        # the mottle into gradients and the cloth read as plastic. Cloth keeps
        # its daub density and gains contrast instead.
        return SurfaceSpec(octaves=[(150, 0.24), (75, 0.12)],
                           vertical_stretch=1.1, contrast=1.15,
                           daub_count=170, daub_radius=36,
                           daub_depth=0.28, seed=seed)
    if material == "foliage":
        # Leaves: crisp small mottle (local gradient 0.020, above wood) with no
        # direction and no strokes; the tone economy is 8-10 per window, so the map
        # carries two or three plateaus per leaf-sized patch, not a gradient. Sized
        # for ~12 texels per sprite px at the class's scale: 18-40 texel features land
        # as the 1.5-3 px facets a painted leaf shows.
        return SurfaceSpec(octaves=[(72, 0.30), (38, 0.80), (20, 1.00), (11, 0.35)],
                           vertical_stretch=1.0, contrast=1.7, seed=seed)
    if material == "soil":
        # Turned earth: the highest local gradient of any class (0.035) -- a dense
        # per-pixel stipple of bright specks against near-black pockets, with only
        # weak large-scale patches. Dabs supply the specks; the fine octaves the
        # pockets. Contrast is high because the bed's p10-p90 is 0.29 with 6-8 tones
        # per window, i.e. almost no smooth shading.
        return SurfaceSpec(octaves=[(120, 0.25), (44, 0.70), (22, 1.00), (12, 0.75)],
                           vertical_stretch=1.0, contrast=2.1,
                           daub_count=900, daub_radius=9, daub_depth=0.42, seed=seed)
    raise ValueError(f"unknown material {material!r}")


def write_surface_map(path: Path, width: int = 512, height: int = 256,
                      spec: SurfaceSpec | None = None,
                      grain_axis: str = "v") -> Path:
    """Write the map; ``grain_axis`` picks which image axis the anisotropy runs on.

    The generator stretches noise and draws strokes along V (vertical). Wood
    grain has to run *along a plank* -- horizontal in texture space when the
    plank's long axis maps to U -- so ``grain_axis="u"`` transposes the field
    before writing. Rotating the generator itself would double every code path;
    transposing the finished rows is equivalent and pinned here as the one place
    orientation is decided.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = surface_rows(width, height, spec)
    if grain_axis == "u":
        cols = [bytes(rows[y][x] for y in range(height)) for x in range(width)]
        rows = cols
        width, height = height, width
    _write_grey_png(path, width, height, rows)
    return path
