"""Nudge a Blender render toward the look of vanilla Project Zomboid tile art.

Two things separate a raw render from a tile that sits convincingly next to vanilla
art, and neither is about modelling skill:

* **Edges.** A render with a transparent film leaves colour undefined where alpha
  is near zero. The game's bilinear sampling drags that undefined colour inward and
  the sprite grows a dark halo. ``bleed_edges`` fills those pixels with nearby
  opaque colour, and ``snap_alpha`` clears the 1-2/255 dust that otherwise inflates
  the trimmed bounding box.

* **Tone.** A vanilla tile is far flatter than a default Blender render. Measured
  over 485 sprites, a single vanilla sprite's own inter-quartile value spread has a
  median of just 0.11, and 80% of sprites fall between 0.02 and 0.32.
  ``match_tone`` nudges a render into those bands.

The tone step deliberately does **not** match histograms. Matching a single sprite's
percentiles against the pooled distribution of every vanilla tile -- which spans
black shadow to white plaster -- stretches a brown crate's 30-value spread across
250 levels, turning 8-bit steps into visible speckle and draining the colour out.
Instead the sprite is left alone when its statistics already sit inside the vanilla
band, and pulled only as far as the nearest edge when they do not, with the gain
clamped so artwork is never wrecked.

Measurements come from ``reference/sprite_stats.json`` (per-sprite statistics, via
``tools/analyze_sprite_stats.py``) taken straight from the shipped packs.
"""

from __future__ import annotations

import colorsys
import json
import random
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

REFERENCE = Path(__file__).resolve().parents[1] / "reference" / "sprite_stats.json"

#: Fallback if the reference file is missing -- the same numbers, measured over 485
#: vanilla sprites from Tiles2x.pack.
FALLBACK_PROFILE = {
    "sprites_sampled": 485,
    "median_value": {"p10": 0.2549, "p25": 0.3569, "p50": 0.5176,
                     "p75": 0.6902, "p90": 0.8471},
    "value_spread": {"p10": 0.0157, "p25": 0.0471, "p50": 0.1137,
                     "p75": 0.2078, "p90": 0.3176},
    "median_saturation": {"p10": 0.0144, "p25": 0.0729, "p50": 0.2812,
                          "p75": 0.5391, "p90": 0.6890},
}

#: A sprite outside this percentile band of vanilla sprites is nudged to its edge.
BAND = ("p10", "p90")
#: Hard limits on how far tone matching may scale contrast or saturation.
MIN_GAIN, MAX_GAIN = 0.5, 2.0


def load_profile(path: Path | None = None) -> dict:
    path = path or REFERENCE
    if path.exists():
        return json.loads(path.read_text())
    return FALLBACK_PROFILE


GROUNDING_REFERENCE = (Path(__file__).resolve().parents[1] / "reference"
                       / "grounding_curve.json")

#: Measured over 675 vanilla sprites: mean luminance of each scanline divided by that
#: sprite's own median, binned by height above the bottom of the cell. Vanilla art
#: darkens to about 0.81 at the floor and is back to 1.0 by row 32.
FALLBACK_GROUNDING = {"0": 0.877, "4": 0.858, "8": 0.809, "12": 0.901, "16": 0.924,
                      "24": 0.916, "32": 0.992, "48": 1.001, "64": 0.999}


def load_grounding(path: Path | None = None) -> dict[int, float]:
    path = path or GROUNDING_REFERENCE
    raw = (json.loads(path.read_text())["ratio_by_row"] if path.exists()
           else FALLBACK_GROUNDING)
    return {int(k): float(v) for k, v in raw.items()}


@dataclass
class StyleOptions:
    """How hard to push a render toward vanilla. ``0`` disables a step entirely."""

    match_strength: float = 0.6
    alpha_floor: int = 8
    bleed_passes: int = 4
    #: Per-pixel grain, as a multiple of the vanilla median. A renderer cannot
    #: produce this band on its own.
    grain_strength: float = 1.0
    #: Fraction of pixels the grain touches. Below 1.0 the grain also ignores the
    #: flat-preservation mask, because sparse strong speckle over flats is the point.
    grain_coverage: float = 1.0
    #: Contact shading at the base. Objects want this; floor tiles do not, since a
    #: floor sprite lives entirely inside the rows the curve darkens.
    grounding_strength: float = 1.0
    #: Square off the alpha edge. Floor diamonds must interlock, and 74% of vanilla's
    #: full-size floor tiles carry a single alpha value.
    hard_alpha: bool = False
    #: Painting conversion: flat fills, stepped tones, crisp interior edges.
    #: ``paint_levels = 0`` disables it. Floors are painted far flatter than objects
    #: (levels_90 median 15 vs 68), so the floor path passes a lower level count.
    paint_levels: int = 48
    paint_passes: int = 3
    paint_threshold: float = 0.06
    paint_sharpen: float = 0.55
    #: Vertical brush dashes, the directional mid-band texture vanilla paints metal
    #: with. `stroke_amplitude = 0` disables them.
    stroke_amplitude: float = 0.0
    stroke_coverage: float = 0.12
    stroke_length: int = 8
    #: Reference sprite (PIL image in the same cell frame) whose low-frequency
    #: shading field is grafted onto the render, and how strongly. This is what
    #: makes the 2D output read as 3D the way vanilla does -- vanilla paints its
    #: form shading beyond what physical lighting produces.
    shade_reference: object = None
    shade_strength: float = 1.0
    #: One-pixel bright accents beside drawn linework, in the directions the shade
    #: reference's painter used. Needs ``shade_reference``; 0 disables.
    relief_strength: float = 1.0
    #: Per-element finishing (lit top edges, shaded undersides, small-fitting
    #: drift). Needs the element id pass; 0 disables.
    finish_strength: float = 1.0
    #: Per-element tone blocking toward the corpus tone economy (the subtracting
    #: pass). Needs the element id pass; 0 disables. Skipped when the relight
    #: path runs, which steps the light itself instead.
    block_strength: float = 0.65
    #: Relight path (needs the light pass + element ids): how far each element's
    #: recovered paint is flattened toward its paint tones, and how far the light
    #: luminance is pulled into its quantised steps.
    paint_flatten: float = 0.75
    light_steps: float = 0.8
    #: Tonal silhouette: surfaces grazing the view darken toward the measured 0.86,
    #: expressing the form edge as shading rather than a drawn line. Needs the
    #: normal pass and the rig's view vector; 0 disables.
    edge_turn_strength: float = 1.0
    #: Painted contact weight: bottom silhouette rows drop to the measured 0.69x of
    #: the interior, top rows to 0.77x. Skipped for floors (hard_alpha), whose
    #: diamonds must interlock seamlessly. 0 disables.
    contour_strength: float = 1.0
    #: Top-contour ratio override; the default mirrors CONTOUR_TOP (defined below,
    #: hence the literal), and drums with dark rims pass their measured 0.77.
    contour_top: float = 0.95
    #: Painted floor shadow behind the sprite (measured: flat black at alpha 51,
    #: 0.87 tile wide). For objects standing on legs; 0 disables.
    shadow_strength: float = 0.0
    #: Shadow desaturation toward neutral dark (measured on vanilla fabric:
    #: sat 0.71 lit -> 0.33 at value 0.26). Fabric-class materials only;
    #: 0 disables.
    shadow_desat_strength: float = 0.0
    #: Crown-to-skirt light ramp over the FOLIAGE family: the top third of a canopy is
    #: lighter than its bottom third by this much value. Measured on painted fruit-tree
    #: sheets at +0.09..+0.23 (median +0.11) and on vanilla farming crops at +0.05;
    #: a per-leaf toon ramp gives 0.03-0.04 because each leaf is lit on its own and
    #: nothing darkens the underside of the mass as a whole. 0 disables.
    canopy_ramp: float = 0.0
    #: Dark rim where a foliage or fruit element meets anything else: boundary pixels
    #: are pulled to this ratio of their element's interior value. Painted sheets
    #: measure 0.68-0.91 (boundary/interior), vanilla 0.97-1.00, ours 0.90-0.97 with
    #: only the outline shell. 1.0 disables.
    rim_ratio: float = 1.0
    enabled: bool = True


# --------------------------------------------------------------------------- #
# Alpha hygiene
# --------------------------------------------------------------------------- #

def snap_alpha(img: Image.Image, floor: int = 8) -> Image.Image:
    """Zero out alpha below ``floor`` so near-invisible dust does not survive trimming."""
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    a = a.point(lambda v: 0 if v < floor else v)
    return Image.merge("RGBA", (r, g, b, a))


def harden_alpha(img: Image.Image, threshold: int = 128) -> Image.Image:
    """Force alpha to fully on or fully off.

    Vanilla's full-diamond floor tiles are hard-edged: 247 of the 335 in
    Tiles2x.floor.pack carry a single alpha value. That is not a stylistic detail --
    neighbouring floor diamonds have to interlock exactly, and an antialiased edge
    leaves a seam of half-transparent pixels running between every pair of tiles.
    A rendered diamond comes out antialiased, so for floors the edge is squared off.
    """
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    return Image.merge("RGBA", (r, g, b, a.point(lambda v: 255 if v >= threshold else 0)))


def bleed_edges(img: Image.Image, passes: int = 4) -> Image.Image:
    """Push opaque colour outward into transparent pixels, leaving alpha untouched.

    Prevents the dark fringe that appears once the engine samples the sprite with
    bilinear filtering.
    """
    img = img.convert("RGBA")
    w, h = img.size
    px = list(img.getdata())
    known = [p[3] > 0 for p in px]

    for _ in range(max(0, passes)):
        additions: dict[int, tuple[int, int, int]] = {}
        for y in range(h):
            base = y * w
            for x in range(w):
                i = base + x
                if known[i]:
                    continue
                rs = gs = bs = n = 0
                for dy in (-1, 0, 1):
                    yy = y + dy
                    if not 0 <= yy < h:
                        continue
                    for dx in (-1, 0, 1):
                        xx = x + dx
                        if dx == dy == 0 or not 0 <= xx < w:
                            continue
                        j = yy * w + xx
                        if known[j]:
                            rs += px[j][0]
                            gs += px[j][1]
                            bs += px[j][2]
                            n += 1
                if n:
                    additions[i] = (rs // n, gs // n, bs // n)
        if not additions:
            break
        for i, (r, g, b) in additions.items():
            px[i] = (r, g, b, px[i][3])
            known[i] = True

    out = Image.new("RGBA", (w, h))
    out.putdata(px)
    return out


# --------------------------------------------------------------------------- #
# Tone matching
# --------------------------------------------------------------------------- #

def _quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1,
              max(0, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[idx]


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def measure(img: Image.Image) -> dict[str, float] | None:
    """The same statistics vanilla was profiled with, for one sprite."""
    values, sats = [], []
    for r, g, b, a in img.convert("RGBA").getdata():
        if a < 200:
            continue
        _h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        values.append(v)
        sats.append(s)
    if len(values) < 50:
        return None
    values.sort()
    sats.sort()
    return {
        "median_value": _quantile(values, 0.5),
        "value_spread": _quantile(values, 0.75) - _quantile(values, 0.25),
        "median_saturation": _quantile(sats, 0.5),
    }


def _pull_into_band(value: float, band: tuple[float, float], strength: float) -> float:
    """Target for a statistic: unchanged inside the band, nearest edge outside it."""
    target = _clamp(value, *band)
    return value + (target - value) * strength


def match_tone(img: Image.Image, strength: float = 0.6,
               profile: dict | None = None) -> Image.Image:
    """Bring a render's brightness, contrast and saturation into the vanilla band.

    A sprite already inside the band comes back untouched, which is the point: a
    good render should not be "corrected" into something else.
    """
    if strength <= 0:
        return img.convert("RGBA")
    profile = profile or load_profile()
    stats = measure(img)
    if stats is None:
        return img.convert("RGBA")

    def band(key: str) -> tuple[float, float]:
        entry = profile[key]
        return entry[BAND[0]], entry[BAND[1]]

    median = stats["median_value"]
    spread = stats["value_spread"]
    saturation = stats["median_saturation"]

    target_median = _pull_into_band(median, band("median_value"), strength)
    target_spread = _pull_into_band(spread, band("value_spread"), strength)
    target_sat = _pull_into_band(saturation, band("median_saturation"), strength)

    value_gain = (_clamp(target_spread / spread, MIN_GAIN, MAX_GAIN)
                  if spread > 1e-4 else 1.0)
    sat_gain = (_clamp(target_sat / saturation, MIN_GAIN, MAX_GAIN)
                if saturation > 1e-4 else 1.0)

    if abs(value_gain - 1) < 1e-3 and abs(sat_gain - 1) < 1e-3 \
            and abs(target_median - median) < 1e-3:
        return img.convert("RGBA")

    img = img.convert("RGBA")
    out = []
    for r, g, b, a in img.getdata():
        if a == 0:
            out.append((r, g, b, a))
            continue
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        v2 = _clamp(target_median + (v - median) * value_gain, 0.0, 1.0)
        s2 = _clamp(s * sat_gain, 0.0, 1.0)
        rr, gg, bb = colorsys.hsv_to_rgb(h, s2, v2)
        out.append((round(rr * 255), round(gg * 255), round(bb * 255), a))

    result = Image.new("RGBA", img.size)
    result.putdata(out)
    return result


#: Kept so existing calls keep working; the histogram version was removed because
#: it amplified 8-bit quantisation into speckle on low-contrast sprites.
match_vanilla = match_tone


def grounding_multipliers(cell_height: int, strength: float,
                          curve: dict[int, float] | None = None) -> list[float]:
    """Per-row brightness multiplier, indexed by row counted up from the cell bottom.

    The measured curve is sampled at bin starts, so it is interpolated between them
    rather than stepped -- a stepped curve leaves visible bands across the sprite.
    """
    curve = curve or load_grounding()
    anchors = sorted(curve.items())
    out = []
    for row in range(cell_height):
        if row <= anchors[0][0]:
            ratio = anchors[0][1]
        elif row >= anchors[-1][0]:
            ratio = 1.0
        else:
            ratio = 1.0
            for (r0, v0), (r1, v1) in zip(anchors, anchors[1:]):
                if r0 <= row <= r1:
                    t = (row - r0) / (r1 - r0) if r1 > r0 else 0.0
                    ratio = v0 + t * (v1 - v0)
                    break
        out.append(1.0 + (ratio - 1.0) * strength)
    return out


def ground_shading(img: Image.Image, strength: float = 1.0,
                   curve: dict[int, float] | None = None) -> Image.Image:
    """Darken a sprite toward the tile floor, the way vanilla art is painted.

    A rendered object standing on a ground plane does *not* pick this up: measured
    directly, a plane at a realistic albedo shifts the base of a sprite by under one
    luminance unit, because the ambient it blocks and the light it bounces cancel.
    Vanilla's grounding is painted, so this reproduces it from the measurement.
    """
    if strength <= 0:
        return img.convert("RGBA")
    img = img.convert("RGBA")
    w, h = img.size
    multipliers = grounding_multipliers(h, strength, curve)
    px = list(img.getdata())
    out = []
    for i, (r, g, b, a) in enumerate(px):
        if a == 0:
            out.append((r, g, b, a))
            continue
        m = multipliers[h - 1 - (i // w)]
        out.append((min(255, round(r * m)), min(255, round(g * m)),
                    min(255, round(b * m)), a))
    result = Image.new("RGBA", img.size)
    result.putdata(out)
    return result


# --------------------------------------------------------------------------- #
# Painting signature
# --------------------------------------------------------------------------- #

#: Local 3x3 value range below which a pixel counts as part of a flat fill, and
#: above which it counts as a drawn edge -- thresholds shared with
#: tools/analyze_painting.py so the measurements and the transform agree.
PAINT_FLAT_T = 0.02
PAINT_EDGE_T = 0.15


def _value_of(r: int, g: int, b: int) -> float:
    return max(r, g, b) / 255.0


def paintify(img: Image.Image, passes: int = 3, threshold: float = 0.06,
             levels: int = 48, sharpen: float = 0.55) -> Image.Image:
    """Convert render shading into painting: flat fills, stepped tones, crisp edges.

    Measured over 305 vanilla sprites, hand-painted art has a signature a renderer
    never produces: large runs of *identical* pixels (median 26% of the interior,
    floors 66%), tone in discrete steps, and faces meeting at 1-2 px drawn lines.
    A path-traced sprite is the opposite -- everything varies slightly, nothing is
    drawn. Three sub-steps close that:

    * **edge-preserving flattening** -- each pixel averages only the neighbours
      within ``threshold`` of its own value, repeated ``passes`` times. Gentle
      gradients collapse into plateaus; real boundaries survive untouched.
    * **sharpening** -- what survives as a boundary is steepened, concentrating a
      soft 3 px render edge into the hard line a painter would draw.
    * **tone quantisation** -- value snaps to ``levels`` steps across the sprite's
      own range, which is what makes plateaus *identical* rather than merely close.

    Alpha is never touched.
    """
    img = img.convert("RGBA")
    w, h = img.size
    px = list(img.getdata())
    opaque = [p[3] > 0 for p in px]

    def neighbours(i: int) -> list[int]:
        x, y = i % w, i // w
        out = []
        for dy in (-1, 0, 1):
            yy = y + dy
            if not 0 <= yy < h:
                continue
            for dx in (-1, 0, 1):
                if dx == dy == 0:
                    continue
                xx = x + dx
                if 0 <= xx < w:
                    j = yy * w + xx
                    if opaque[j]:
                        out.append(j)
        return out

    # -- edge-preserving flatten ------------------------------------------
    for _ in range(max(0, passes)):
        nxt = px[:]
        for i, p in enumerate(px):
            if not opaque[i]:
                continue
            v = _value_of(*p[:3])
            rs, gs, bs, n = p[0], p[1], p[2], 1
            for j in neighbours(i):
                q = px[j]
                if abs(_value_of(*q[:3]) - v) <= threshold:
                    rs += q[0]
                    gs += q[1]
                    bs += q[2]
                    n += 1
            nxt[i] = (rs // n, gs // n, bs // n, p[3])
        px = nxt

    # -- sharpen surviving boundaries -------------------------------------
    if sharpen > 0:
        values = [(_value_of(*p[:3]) if opaque[i] else 0.0) for i, p in enumerate(px)]
        nxt = px[:]
        for i, p in enumerate(px):
            if not opaque[i]:
                continue
            ns = neighbours(i)
            if not ns:
                continue
            local = [values[j] for j in ns]
            mean = (sum(local) + values[i]) / (len(local) + 1)
            spread = max(local + [values[i]]) - min(local + [values[i]])
            if spread <= PAINT_FLAT_T:
                continue  # flats stay flat; only boundaries get steeper
            v = values[i]
            v2 = _clamp(v + sharpen * (v - mean), 0.0, 1.0)
            if v > 1e-4:
                k = v2 / v
                nxt[i] = (min(255, round(p[0] * k)), min(255, round(p[1] * k)),
                          min(255, round(p[2] * k)), p[3])
        px = nxt

    # -- quantise tone -----------------------------------------------------
    if levels > 0:
        vals = sorted(_value_of(*p[:3]) for i, p in enumerate(px) if opaque[i])
        if vals:
            lo = vals[max(0, int(len(vals) * 0.01))]
            hi = vals[min(len(vals) - 1, int(len(vals) * 0.99))]
            span = max(hi - lo, 1e-4)
            step = span / levels
            for i, p in enumerate(px):
                if not opaque[i]:
                    continue
                v = _value_of(*p[:3])
                v2 = _clamp(lo + round((v - lo) / step) * step, 0.0, 1.0)
                if v > 1e-4 and abs(v2 - v) > 1e-4:
                    k = v2 / v
                    px[i] = (min(255, round(p[0] * k)), min(255, round(p[1] * k)),
                             min(255, round(p[2] * k)), p[3])

    out = Image.new("RGBA", (w, h))
    out.putdata(px)
    return out


SCALE_PROFILE_PATH = (Path(__file__).resolve().parents[1] / "reference"
                      / "scale_profile.json")
#: Median per-pixel octave across 312 vanilla sprites: the spread that survives at
#: full resolution but not one pixel of blur.
FALLBACK_GRAIN_OCTAVE = 0.0078


def target_grain_octave(path: Path | None = None) -> float:
    path = path or SCALE_PROFILE_PATH
    if not path.exists():
        return FALLBACK_GRAIN_OCTAVE
    octaves = json.loads(path.read_text()).get("octaves", {})
    return float(octaves.get("0->1", {}).get("p50", FALLBACK_GRAIN_OCTAVE))


def add_grain(img: Image.Image, octave: float | None = None,
              seed: int = 11, preserve_flat: bool = True,
              coverage: float = 1.0) -> Image.Image:
    """Add per-pixel value noise, the band a renderer cannot deliver.

    Detail one pixel wide does not survive being rendered -- the sampler averages it
    across the pixel -- so a path-traced sprite comes out smooth where painted art is
    not. Measured against the vanilla drum the recreation carried 0.001 of spread in
    this band against 0.013.

    ``octave`` is the spread this should add between full resolution and one pixel of
    blur. Blurring over a 3x3 window cuts independent noise to a third of its
    amplitude, so the noise is scaled by 1 / (1 - 1/3) to land on the target.
    """
    octave = target_grain_octave() if octave is None else octave
    if octave <= 0:
        return img.convert("RGBA")
    half_range = octave / (1.0 - 1.0 / 3.0)

    img = img.convert("RGBA")
    w, h = img.size
    px = list(img.getdata())

    # Vanilla carries grain *and* flat fills at once, because its grain is local:
    # blocked-in areas stay blocked in. Uniform noise would erase every identical
    # run the paint pass produced, so grain is masked away from flat plateaus.
    skip = [False] * len(px)
    if preserve_flat:
        values = [(_value_of(*p[:3]) if p[3] > 0 else None) for p in px]
        for i, p in enumerate(px):
            if p[3] == 0:
                continue
            x, y = i % w, i // w
            lo = hi = values[i]
            for dy in (-1, 0, 1):
                yy = y + dy
                if not 0 <= yy < h:
                    continue
                for dx in (-1, 0, 1):
                    xx = x + dx
                    if 0 <= xx < w:
                        v = values[yy * w + xx]
                        if v is not None:
                            lo = min(lo, v)
                            hi = max(hi, v)
            skip[i] = (hi - lo) < PAINT_FLAT_T

    # coverage < 1 grains only a random subset, the way vanilla's floors carry
    # sparse strong speckle over otherwise blocked-in fills.
    rng = random.Random(seed)
    out = []
    for i, (r, g, b, a) in enumerate(px):
        if a == 0:
            out.append((r, g, b, a))
            continue
        delta = rng.uniform(-half_range, half_range) * 255.0
        covered = coverage >= 1.0 or rng.random() < coverage
        if skip[i] or not covered:
            out.append((r, g, b, a))
            continue
        out.append((min(255, max(0, round(r + delta))),
                    min(255, max(0, round(g + delta))),
                    min(255, max(0, round(b + delta))), a))
    result = Image.new("RGBA", img.size)
    result.putdata(out)
    return result


def _blur_value_field(values: list, w: int, h: int, radius: int) -> list:
    """Opaque-aware separable box blur over a value field (None = transparent)."""
    def pass_1d(src, stride, count, line_count, line_stride):
        out = src[:]
        for line in range(line_count):
            base = line * line_stride
            for k in range(count):
                i = base + k * stride
                if src[i] is None:
                    continue
                total = n = 0.0
                for d in range(-radius, radius + 1):
                    kk = k + d
                    if 0 <= kk < count:
                        v = src[base + kk * stride]
                        if v is not None:
                            total += v
                            n += 1
                out[i] = total / n if n else None
        return out

    horizontal = pass_1d(values, 1, w, h, w)
    return pass_1d(horizontal, w, h, w, 1)


def form_shading(img: Image.Image, reference: Image.Image, strength: float = 1.0,
                 radius: int = 7, clamp: tuple[float, float] = (0.55, 1.8),
                 skip: list[bool] | None = None) -> Image.Image:
    """Graft the reference's large-scale shading structure onto the render.

    What makes painted 2D read as 3D is the low-frequency luminance field across the
    form -- and vanilla paints it *beyond* what physical lighting produces: the
    drum's left-to-right falloff is 1.42 where zero-ambient rendering of a cylinder
    tops out near 1.14. Texture work cannot supply that, and a renderer will not.

    So it is transferred, not invented: blur both sprites' value channels heavily
    (killing texture, keeping form), take the per-pixel ratio reference / render,
    and multiply the render by it. High-frequency detail -- strokes, grain, drawn
    edges, flat fills -- rides along unchanged, because a smooth multiplier moves a
    3x3 neighbourhood together. Where the silhouettes disagree the ratio falls back
    to 1. Requires both sprites in the same cell frame, which the rig guarantees.
    """
    if strength <= 0:
        return img.convert("RGBA")
    img = img.convert("RGBA")
    reference = reference.convert("RGBA")
    if reference.size != img.size:
        raise ValueError(f"cell mismatch: render {img.size}, reference {reference.size}")
    w, h = img.size
    px = list(img.getdata())
    ref_px = list(reference.getdata())

    mine = [(max(p[:3]) / 255.0 if p[3] > 0 else None) for p in px]
    ref = [(max(p[:3]) / 255.0 if p[3] > 200 else None) for p in ref_px]
    mine_blur = _blur_value_field(mine, w, h, radius)
    ref_blur = _blur_value_field(ref, w, h, radius)

    out = []
    lo, hi = clamp
    for i, p in enumerate(px):
        # Up-facing pixels are excluded when a mask is given: a lid ellipse
        # rarely registers exactly against the reference's (rim thickness differs
        # by a pixel or two), and a misregistered ratio field tilts the lid in an
        # arbitrary direction -- measured to grade the drum lid *opposite* to
        # vanilla. Top faces take their form from the finish grade instead.
        if p[3] == 0 or mine_blur[i] is None or ref_blur[i] is None \
                or mine_blur[i] < 1e-3 or (skip and skip[i]):
            out.append(p)
            continue
        ratio = _clamp(ref_blur[i] / mine_blur[i], lo, hi)
        k = 1.0 + (ratio - 1.0) * strength
        out.append((min(255, round(p[0] * k)), min(255, round(p[1] * k)),
                    min(255, round(p[2] * k)), p[3]))
    result = Image.new("RGBA", (w, h))
    result.putdata(out)
    return result


#: How much darker than the 5x5 neighbourhood mean a pixel must be to count as drawn
#: linework, shared between the relief measurement and the relief transform.
RELIEF_DARK_T = 0.06
#: Directions fewer than this many samples in the reference are applied as zero --
#: a six-pixel average is noise, not a convention.
RELIEF_MIN_SAMPLES = 12


def _relief_scan(img: Image.Image):
    """Yield (index, orientation, neighbour-slots) for every oriented dark-line pixel.

    Shared between measuring a reference and accenting a render so both agree on
    what counts as a line. A slot is the first non-dark pixel within two steps in
    each of the four directions.
    """
    img = img.convert("RGBA")
    w, h = img.size
    px = list(img.getdata())
    val = [(max(p[:3]) / 255.0 if p[3] > 128 else None) for p in px]

    def local_baseline(x: int, y: int, r: int = 2):
        """Median of the non-dark neighbourhood.

        A plain mean is pulled down by the line itself and up by its painted
        accent, so both sides of a line measure as slightly bright against it.
        The median of the pixels that are not part of the line is the fill value
        the painter worked against.
        """
        window = []
        for dy in range(-r, r + 1):
            yy = y + dy
            if not 0 <= yy < h:
                continue
            for dx in range(-r, r + 1):
                xx = x + dx
                if 0 <= xx < w and val[yy * w + xx] is not None:
                    window.append(val[yy * w + xx])
        if not window:
            return None
        rough = sum(window) / len(window)
        light = sorted(v for v in window if v > rough - RELIEF_DARK_T * 0.7)
        if not light:
            return rough
        return light[len(light) // 2]

    for y in range(h):
        for x in range(w):
            v = val[y * w + x]
            if v is None:
                continue
            lm = local_baseline(x, y)
            if lm is None or v > lm - RELIEF_DARK_T:
                continue

            def dark(dx: int, dy: int) -> bool:
                xx, yy = x + dx, y + dy
                if not (0 <= xx < w and 0 <= yy < h):
                    return False
                vv = val[yy * w + xx]
                return vv is not None and vv < lm - RELIEF_DARK_T * 0.7

            horiz = dark(-1, 0) and dark(1, 0)
            vert = dark(0, -1) and dark(0, 1)
            if horiz == vert:
                continue  # dot, corner or blob -- no orientation to accent
            slots = {}
            for d, (dx, dy) in (("U", (0, -1)), ("D", (0, 1)),
                                ("L", (-1, 0)), ("R", (1, 0))):
                for step in (1, 2):
                    xx, yy = x + dx * step, y + dy * step
                    if not (0 <= xx < w and 0 <= yy < h):
                        break
                    vv = val[yy * w + xx]
                    if vv is not None and vv > lm - RELIEF_DARK_T * 0.7:
                        slots[d] = (yy * w + xx, vv, lm)
                        break
            yield "horiz" if horiz else "vert", slots


def measure_relief(reference: Image.Image) -> dict[tuple[str, str], float]:
    """Where this sprite's painter puts the bright accent beside dark linework.

    Returns ``{(orientation, direction): value delta}`` -- the mean value of the
    first non-dark neighbour relative to the local mean. Vanilla is not consistent
    about it: the drum accents below and right of its lines (incised grooves), the
    metal crate accents all four sides evenly (raised panel edges). So the deltas
    are measured from the same reference the form shading is grafted from rather
    than fixed as constants.
    """
    acc: dict[tuple[str, str], list[float]] = {}
    for orient, slots in _relief_scan(reference):
        for d, (_i, v, lm) in slots.items():
            acc.setdefault((orient, d), []).append(v - lm)
    return {key: (sum(vs) / len(vs) if len(vs) >= RELIEF_MIN_SAMPLES else 0.0)
            for key, vs in acc.items()}


def edge_relief(img: Image.Image, deltas: dict[tuple[str, str], float],
                strength: float = 1.0) -> Image.Image:
    """Give every drawn line the lit side the reference's painter gave theirs.

    Form shading is a smooth field, so it moves a line and its surroundings
    together and the small elements -- rim rings, seam straps, rolling grooves --
    stay flat even once the body reads as 3D. Vanilla paints those elements'
    volume as a one-pixel bright accent along one side of the dark line. This
    applies the measured accents to the render's own linework.
    """
    if strength <= 0 or not deltas:
        return img.convert("RGBA")
    img = img.convert("RGBA")
    accents: dict[int, float] = {}
    for orient, slots in _relief_scan(img):
        for d, (i, _v, _lm) in slots.items():
            delta = deltas.get((orient, d), 0.0) * strength
            if abs(delta) < 1e-4:
                continue
            # A 2px-thick line tags the same neighbour from both of its rows;
            # accents replace rather than stack.
            if abs(delta) > abs(accents.get(i, 0.0)):
                accents[i] = delta
    if not accents:
        return img
    px = list(img.getdata())
    for i, delta in accents.items():
        r, g, b, a = px[i]
        v = max(r, g, b) / 255.0
        if v <= 1e-3:
            continue
        k = _clamp((v + delta) / v, 0.5, 1.8)
        px[i] = (min(255, round(r * k)), min(255, round(g * k)),
                 min(255, round(b * k)), a)
    out = Image.new("RGBA", img.size)
    out.putdata(px)
    return out


def upward_mask(normal_png: Image.Image, threshold: float = 0.55) -> list[bool]:
    """Per-pixel "this surface faces up" from a rendered world-normal pass.

    The pass stores ``normal * 0.5 + 0.5`` through the standard sRGB transform, so
    the blue channel is decoded back to linear before recovering ``normal.z``.
    """
    px = normal_png.convert("RGBA")

    def linear(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    out = []
    for p in px.getdata():
        if p[3] < 128:
            out.append(False)
            continue
        nz = linear(p[2] / 255.0) * 2.0 - 1.0
        out.append(nz > threshold)
    return out


#: Silhouette boundary pixels over the interior value three rows in, measured on
#: three references. The bottom contour is universal -- drum 0.689, crate 0.708,
#: table 0.760 -- it is what seats the object on the floor tile. The TOP contour
#: is not: the drum's dark rim measures 0.771 but the crate's bright ribs 0.954
#: and the table edge 1.045, so the default stays near-neutral and a reference
#: with a dark top edge overrides it per build (--contour-top).
CONTOUR_BOTTOM = 0.71
CONTOUR_TOP = 0.95
#: Side silhouettes measured the same way: vanilla does NOT darken them -- the
#: left edge is neutral (crate 1.017, drum 0.950) and the shadow-side right edge
#: carries a subtle BRIGHT rim accent (crate 1.094, drum 1.139). The styled
#: renders drifted the other way (left 1.16 from edge bleed, right flat), so both
#: get pinned like the vertical contours.
CONTOUR_LEFT = 1.0
CONTOUR_RIGHT = 1.10


def contour_weight(img: Image.Image, strength: float = 1.0,
                   bottom: float = CONTOUR_BOTTOM, top: float = CONTOUR_TOP,
                   ) -> Image.Image:
    """Draw the silhouette with tone: heavy under, lighter above.

    Vanilla does not outline an object against the floor -- it drops the bottom
    silhouette rows to 0.69x of the interior, a painted contact weight that
    separates the sprite from the tile beneath it. The styled render measured
    *brighter* at the bottom edge than inside (1.06x: antialiasing plus ambient
    reaching the underside), which reads as floating. This pass pins each
    silhouette row to the measured ratio against the interior three rows away,
    full effect at the boundary pixel and half one pixel further in.
    """
    if strength <= 0:
        return img.convert("RGBA")
    img = img.convert("RGBA")
    w, h = img.size
    px = list(img.getdata())
    opaque = [p[3] > 128 for p in px]
    factors = [1.0] * (w * h)

    def value(p) -> float:
        return max(p[:3]) / 255.0

    for x in range(w):
        for y in range(h):
            i = y * w + x
            if not opaque[i]:
                continue
            below = opaque[i + w] if y + 1 < h else False
            above = opaque[i - w] if y > 0 else False
            left_open = not (opaque[i - 1] if x > 0 else False)
            right_open = not (opaque[i + 1] if x + 1 < w else False)
            for ratio_, boundary, ref_off in ((CONTOUR_LEFT, left_open, 1),
                                              (CONTOUR_RIGHT, right_open, -1)):
                if not boundary:
                    continue
                ref_i = i + 3 * ref_off
                if not (0 <= ref_i < w * h) or not opaque[ref_i]:
                    continue
                v = value(px[i])
                ref = value(px[ref_i])
                if v < 1e-3 or ref < 1e-3:
                    continue
                f = 1.0 + (ref * ratio_ / v - 1.0) * strength
                f = max(0.5, min(1.3, f))
                factors[i] = min(factors[i], f) if f < 1 else max(factors[i], f)
            for is_bottom, boundary, ref_off in ((True, not below, -w),
                                                 (False, not above, w)):
                if not boundary:
                    continue
                ref_i = i + 3 * ref_off
                if not (0 <= ref_i < w * h) or not opaque[ref_i]:
                    continue
                ratio = bottom if is_bottom else top
                v = value(px[i])
                ref = value(px[ref_i])
                if v < 1e-3 or ref < 1e-3:
                    continue
                target = ref * ratio
                f = 1.0 + (target / v - 1.0) * strength
                f = max(0.5, min(1.3, f))
                factors[i] = min(factors[i], f) if f < 1 else max(factors[i], f)
                # The vanilla contact band is ~3 px deep, not a single row.
                for depth, share in ((1, 0.7), (2, 0.4)):
                    inner = i + depth * ref_off
                    if 0 <= inner < w * h and opaque[inner]:
                        f2 = 1.0 + (f - 1.0) * share
                        factors[inner] = (min(factors[inner], f2) if f2 < 1
                                          else max(factors[inner], f2))

    out = []
    for p, f in zip(px, factors):
        if p[3] == 0 or f == 1.0:
            out.append(p)
            continue
        out.append((min(255, round(p[0] * f)), min(255, round(p[1] * f)),
                    min(255, round(p[2] * f)), p[3]))
    result = Image.new("RGBA", (w, h))
    result.putdata(out)
    return result


def edge_turn_shading(img: Image.Image, normal_png: Image.Image,
                      view: tuple[float, float, float], strength: float = 1.0,
                      dark: float = 0.86, threshold: float = 0.35) -> Image.Image:
    """Darken surfaces that turn away from the camera -- shading instead of outline.

    The vanilla drum's silhouette columns run about 14% darker than the adjacent
    body on *both* sides, including the lit one where physical shading cannot put
    them (the left silhouette faces the key almost head-on). The painter expresses
    the form's edge tonally rather than with a drawn line. The renderer cannot do
    it, but the normal pass knows exactly where the surface grazes the view, so
    this pass paints it: pixels whose normal is near-perpendicular to the view
    direction slide toward ``dark``, easing back to 1 by ``threshold``. Flat
    camera-facing surfaces (crate walls sit well above the threshold) are
    untouched -- boxes keep their drawn outlines, curves get their tonal edges.
    """
    if strength <= 0:
        return img.convert("RGBA")
    img = img.convert("RGBA")
    normal_png = normal_png.convert("RGBA")
    if normal_png.size != img.size:
        raise ValueError(f"pass mismatch: image {img.size}, normals {normal_png.size}")

    def linear(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    px = list(img.getdata())
    npx = list(normal_png.getdata())
    out = []
    vx, vy, vz = view
    for p, n in zip(px, npx):
        if p[3] == 0 or n[3] < 128:
            out.append(p)
            continue
        nx = linear(n[0] / 255.0) * 2.0 - 1.0
        ny = linear(n[1] / 255.0) * 2.0 - 1.0
        nz = linear(n[2] / 255.0) * 2.0 - 1.0
        norm = (nx * nx + ny * ny + nz * nz) ** 0.5
        if norm < 0.5:
            out.append(p)
            continue
        facing = abs(nx * vx + ny * vy + nz * vz) / norm
        if facing >= threshold:
            out.append(p)
            continue
        t = facing / threshold
        f = 1.0 + ((dark + (1.0 - dark) * t) - 1.0) * strength
        out.append((min(255, round(p[0] * f)), min(255, round(p[1] * f)),
                    min(255, round(p[2] * f)), p[3]))
    result = Image.new("RGBA", img.size)
    result.putdata(out)
    return result


def shadow_desat(img: Image.Image, strength: float = 1.0,
                 pivot: float = 0.55, floor: float = 0.47) -> Image.Image:
    """Drain chroma out of the shadows, the way vanilla paints dyed cloth.

    Measured on the red couch: lit faces sit at saturation ~0.71 but the shaded
    arm side drops to 0.33 at value 0.26 -- vanilla's fabric shadows converge
    toward neutral dark instead of staying vivid. A multiplicative paint-x-light
    model preserves saturation at every level, so shaded faces glow like a
    different, *more* vivid colour -- exactly the "each part is a different
    colour" read. Material-specific: wood keeps (even gains) saturation in its
    shadows, so this stays off unless the build asks for it (--shadow-desat).
    """
    if strength <= 0:
        return img.convert("RGBA")
    img = img.convert("RGBA")
    out = []
    for p in img.getdata():
        if p[3] == 0:
            out.append(p)
            continue
        r, g, b = p[0] / 255.0, p[1] / 255.0, p[2] / 255.0
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        if v < pivot and s > 1e-3:
            t = max(0.0, (v - 0.2) / (pivot - 0.2))
            scale = floor + (1.0 - floor) * t
            s2 = s * (1.0 + (scale - 1.0) * strength)
            rr, gg, bb = colorsys.hsv_to_rgb(h, s2, v)
            out.append((round(rr * 255), round(gg * 255), round(bb * 255), p[3]))
        else:
            out.append(p)
    result = Image.new("RGBA", img.size)
    result.putdata(out)
    return result


def add_strokes(img: Image.Image, amplitude: float = 0.05, coverage: float = 0.12,
                mean_length: int = 8, seed: int = 17,
                skip: list[bool] | None = None) -> Image.Image:
    """Draw short vertical brush dashes over the sprite.

    The one painting statistic isotropic treatment cannot reach is directional
    *mid*-band texture: vanilla's metal wear runs in vertical strokes, which read as
    smooth-shading pixels (local range 0.02-0.15) organised into columns. Random
    grain makes speckle; flattening makes plateaus; neither makes strokes. So this
    draws them -- each dash is a few pixels long, one pixel wide, uniformly lighter
    or darker by less than the edge threshold, tapering toward its tail.

    ``coverage`` is the fraction of opaque pixels the dashes touch. Alpha is never
    modified, and dashes stop at the silhouette.

    ``skip`` is a per-pixel exclusion mask (typically :func:`upward_mask` from the
    rig's normal pass): strokes are metal wear running down gravity, so a drum lid
    or crate top streaked with them reads wrong -- vertical texture on a horizontal
    surface. Dashes neither start on nor cross masked pixels.
    """
    if amplitude <= 0 or coverage <= 0:
        return img.convert("RGBA")
    img = img.convert("RGBA")
    w, h = img.size
    px = list(img.getdata())
    opaque_idx = [i for i, p in enumerate(px)
                  if p[3] > 0 and not (skip and skip[i])]
    if not opaque_idx:
        return img

    rng = random.Random(seed)
    target = int(len(opaque_idx) * coverage)
    strokes = max(1, round(target / (mean_length * 0.75)))
    painted = 0
    for _ in range(strokes * 3):          # bounded retry budget
        if painted >= target:
            break
        i = opaque_idx[rng.randrange(len(opaque_idx))]
        x, y = i % w, i // w
        length = max(2, round(rng.uniform(0.5, 1.6) * mean_length))
        # Under the edge threshold on purpose: a stroke is shading, not a drawn line.
        delta = (rng.uniform(0.35, 1.0) * amplitude
                 * rng.choice((-1.0, 1.0)) * 255.0)
        drift = rng.uniform(-0.35, 0.35)
        fx = float(x)
        for step in range(length):
            yy = y + step
            xx = round(fx)
            if yy >= h or not 0 <= xx < w:
                break
            j = yy * w + xx
            r, g, b, a = px[j]
            if a == 0 or (skip and skip[j]):
                break
            d = delta * (1.0 - 0.5 * step / max(1, length - 1))
            px[j] = (min(255, max(0, round(r + d))), min(255, max(0, round(g + d))),
                     min(255, max(0, round(b + d))), a)
            painted += 1
            fx += drift

    out = Image.new("RGBA", (w, h))
    out.putdata(px)
    return out


def ground_shadow(img: Image.Image, strength: float = 1.0,
                  scale: float = 0.87, offset: tuple[int, int] = (-4, 0),
                  alpha: int = 51) -> Image.Image:
    """Composite the painted floor shadow behind the sprite.

    Objects that stand on legs float without one. Measured on the vanilla wooden
    table (carpentry_01_29): the shadow is a flat black tile diamond at exactly
    alpha 51, 0.87 of the full tile wide, its centre 4 px left of the cell's
    floor-diamond centre. It is drawn *behind* the sprite -- vanilla never
    shadows the object itself with it.
    """
    if strength <= 0:
        return img.convert("RGBA")
    img = img.convert("RGBA")
    w, h = img.size
    cx = w / 2 + offset[0]
    cy = h - w / 4 + offset[1]
    half_w = (w * scale) / 2
    half_h = half_w / 2
    a = round(alpha * min(1.0, strength))
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    spx = shadow.load()
    for y in range(int(cy - half_h), int(cy + half_h) + 1):
        if not 0 <= y < h:
            continue
        span = (1.0 - abs(y - cy) / half_h) * half_w
        for x in range(int(cx - span), int(cx + span) + 1):
            if 0 <= x < w:
                spx[x, y] = (0, 0, 0, a)
    shadow.alpha_composite(img)
    return shadow


def _family_mask(labels, families, wanted: tuple[str, ...]) -> list[bool]:
    return [lab is not None and families.get(lab, "") in wanted for lab in labels]


def _clamp8(v: float) -> int:
    return max(0, min(255, int(v + 0.5)))


def canopy_ramp(img: Image.Image, labels: list, families: dict,
                delta: float) -> Image.Image:
    """Lighten the crown and darken the skirt of the foliage mass as one body.

    The ramp runs over the foliage family's own vertical extent (top row to bottom
    row of every foliage pixel), linear, centred so the middle is untouched: a
    pixel at the very top gains ``delta/2`` in value and one at the very bottom
    loses it. Applied multiplicatively so dark leaves stay darker than light ones.
    """
    if delta <= 0 or not families:
        return img
    img = img.convert("RGBA")
    w, h = img.size
    mask = _family_mask(labels, families, ("foliage",))
    rows = [i // w for i, m in enumerate(mask) if m]
    if not rows:
        return img
    y0, y1 = min(rows), max(rows)
    span = max(1, y1 - y0)
    px = img.load()
    for i, m in enumerate(mask):
        if not m:
            continue
        x, y = i % w, i // w
        r, g, b, a = px[x, y]
        if a == 0:
            continue
        t = 0.5 - (y - y0) / span          # +0.5 at the crown, -0.5 at the skirt
        k = 1.0 + delta * t / max(0.05, max(r, g, b) / 255.0)
        px[x, y] = (_clamp8(r * k), _clamp8(g * k), _clamp8(b * k), a)
    return img


def family_rim(img: Image.Image, labels: list, families: dict,
               ratio: float) -> Image.Image:
    """Darken the boundary pixels of foliage and fruit elements to ``ratio`` of their
    element's interior median value -- the drawn edge a painter puts round every leaf
    and every fruit, which a renderer only produces by accident."""
    if ratio >= 1.0 or not families:
        return img
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()
    wanted = _family_mask(labels, families, ("foliage", "fruit"))
    interior_vals: dict[str, list[float]] = {}
    boundary: list[int] = []
    for i, m in enumerate(wanted):
        if not m:
            continue
        x, y = i % w, i // w
        if px[x, y][3] == 0:
            continue
        lab = labels[i]
        edge = False
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if not (0 <= nx < w and 0 <= ny < h):
                continue
            if labels[ny * w + nx] != lab or px[nx, ny][3] == 0:
                edge = True
                break
        if edge:
            boundary.append(i)
        else:
            interior_vals.setdefault(lab, []).append(max(px[x, y][:3]) / 255.0)
    medians = {lab: sorted(v)[len(v) // 2] for lab, v in interior_vals.items()}
    for i in boundary:
        target = medians.get(labels[i])
        if target is None:
            continue
        x, y = i % w, i // w
        r, g, b, a = px[x, y]
        v = max(r, g, b) / 255.0
        if v <= 1e-4:
            continue
        k = min(1.0, (target * ratio) / v)
        px[x, y] = (_clamp8(r * k), _clamp8(g * k), _clamp8(b * k), a)
    return img


def apply(img: Image.Image, options: StyleOptions | None = None,
          top_mask: list[bool] | None = None,
          element_labels: list | None = None,
          light: Image.Image | None = None,
          normal: Image.Image | None = None,
          view: tuple[float, float, float] | None = None,
          families: dict | None = None) -> Image.Image:
    """Run the full style pass in the order the steps depend on each other.

    ``top_mask`` marks pixels whose surface faces up (from the rig's normal pass);
    orientation-bound treatments -- currently the stroke pass -- skip them.
    ``element_labels`` is the per-pixel part map (from the rig's element id pass);
    with it, every element boundary is finished individually before the painting
    conversion crisps the result. ``light`` is the rig's light pass; with it (and
    the labels) the sprite is first rebuilt as flat paint under stepped light --
    the relight path -- and the later passes decorate that painted base.
    """
    options = options or StyleOptions()
    if not options.enabled:
        return img.convert("RGBA")
    img = snap_alpha(img, options.alpha_floor)
    if options.hard_alpha:
        img = harden_alpha(img)
    relit = False
    if light is not None and element_labels is not None \
            and (options.paint_flatten > 0 or options.light_steps > 0):
        from . import relight as relightmod
        from .recipe import load as load_recipes
        img = relightmod.relight(img, light, element_labels, load_recipes(),
                                 options.paint_flatten, options.light_steps)
        relit = True
    img = match_tone(img, options.match_strength)
    img = ground_shading(img, options.grounding_strength)
    if options.contour_strength > 0 and not options.hard_alpha:
        img = contour_weight(img, options.contour_strength,
                             top=options.contour_top)
    if normal is not None and view is not None and options.edge_turn_strength > 0:
        img = edge_turn_shading(img, normal, view, options.edge_turn_strength)
    if element_labels is not None and options.finish_strength > 0:
        from . import finish as finishmod
        img = finishmod.finish(img, element_labels, options.finish_strength,
                               top_mask=top_mask)
    if element_labels is not None and families:
        # Family passes sit after the per-element finish and before the painting
        # conversion, so the ramp is applied to finished tones and the rim is what
        # paintify then crisps.
        img = canopy_ramp(img, element_labels, families, options.canopy_ramp)
        img = family_rim(img, element_labels, families, options.rim_ratio)
    if options.paint_levels > 0:
        img = paintify(img, options.paint_passes, options.paint_threshold,
                       options.paint_levels, options.paint_sharpen)
    if options.shade_reference is not None and options.shade_strength > 0:
        img = form_shading(img, options.shade_reference, options.shade_strength,
                           skip=top_mask)
    if options.shade_reference is not None and options.relief_strength > 0:
        img = edge_relief(img, measure_relief(options.shade_reference),
                          options.relief_strength)
    if element_labels is not None and options.block_strength > 0 and not relit:
        from . import finish as finishmod
        img = finishmod.tone_block(img, element_labels, options.block_strength)
    if options.shadow_desat_strength > 0:
        img = shadow_desat(img, options.shadow_desat_strength)
    if options.stroke_amplitude > 0:
        img = add_strokes(img, options.stroke_amplitude, options.stroke_coverage,
                          options.stroke_length, skip=top_mask)
    if options.grain_strength > 0:
        img = add_grain(img, target_grain_octave() * options.grain_strength,
                        preserve_flat=options.grain_coverage >= 1.0,
                        coverage=options.grain_coverage)
    img = bleed_edges(img, options.bleed_passes)
    if options.shadow_strength > 0:
        img = ground_shadow(img, options.shadow_strength)
    return img





