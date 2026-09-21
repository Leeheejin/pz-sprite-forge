"""Per-element finishing: the treatment vanilla gives every fitting of a sprite.

Read at 10x, vanilla art does not shade an object once -- it finishes each element
separately: a lit top edge where a part meets sky or another part, a heavier line
along its shaded underside, and on small fittings a tonal drift across the part
itself. A render collapses all of that into one global lighting solution, which is
why recreations kept reading flat at the element level however well the whole-sprite
statistics matched.

This pass reintroduces the per-element treatment *systematically*. The renderer
writes an element id pass (``*_E.png``: each part's id colour as flat emission), so
the style pass knows which pixels belong to which part. Every boundary between two
parts, or between a part and the background, is then finished according to the rig's
own light: the key stands south of the camera, 26 degrees toward east, which on
screen puts light top-left -- so top and left boundary rows are lit, bottom and
right rows are shaded, exactly the convention the vanilla painter applies.

The amplitudes are the one set of numbers here not measured from the game files
directly: they are calibrated so the finished accents land in the 0.03-0.06 value
band the relief measurement found beside vanilla's drawn lines, and `pzforge
compare` against the reference is the check that keeps them honest.
"""

from __future__ import annotations

from PIL import Image, ImageFilter

#: Accent structure per boundary side, as multiples of the corpus-measured accent
#: energy (`recipe`'s ``line.accent_amount``, the median painted bright lip beside
#: vanilla linework). Light is top-left in screen space (rig key: south of camera,
#: 26 deg east), so top/left edges of an element catch light and bottom/right edges
#: fall into shade; the shadow side runs slightly heavier, as painted shadow does.
TOP_RATIO = 1.0
BOTTOM_RATIO = -1.2
SIDE_RATIO = 0.5
#: Tonal drift across small fittings (bolt heads, notches, bungs): lit corner to
#: shaded corner. Calibrated, not measured -- region-level drift measurements are
#: tautological under tone-based segmentation (see `recipe`).
SMALL_DRIFT = 0.06
#: Parts at or below this many opaque pixels count as small fittings.
SMALL_PART_PX = 400
#: Directional grade across up-facing elements. The vanilla drum lid runs a 4.6%
#: swing toward the key (near-left 0.435, far-right 0.416); the styled lid had
#: graded the opposite way, which read as a concave dish. The value here is
#: deliberately above the measured half-swing (0.023): the lid's own texture map
#: lands with a random tilt of its own, and the grade has to win against it --
#: 0.055 is calibrated closed-loop so the *composed output* matches vanilla's
#: quadrant relationship, which `pzforge compare` keeps honest.
TOP_GRADE = 0.055
#: Safety clamp so stacked accents can never blow out or crush a pixel.
CLAMP = (0.70, 1.35)


def _linear(v: float) -> float:
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def decode_labels(id_png: Image.Image, palette: dict[str, list[float]]
                  ) -> list[str | None]:
    """Per-pixel part name from the element id pass.

    The pass renders each part's linear id colour through the standard sRGB
    transform, so pixels are decoded back to linear and matched to the nearest
    palette entry. Antialiasing is nearly disabled in the pass, but any residual
    blend pixel simply lands on whichever of its two parts is closer -- a one-pixel
    ambiguity on some boundaries, not an error.
    """
    img = id_png.convert("RGBA")
    names = list(palette)
    colors = [tuple(palette[n]) for n in names]
    cache: dict[tuple, str | None] = {}
    out: list[str | None] = []
    for p in img.getdata():
        if p[3] < 128:
            out.append(None)
            continue
        key = p[:3]
        if key not in cache:
            lin = tuple(_linear(c / 255.0) for c in key)
            best, best_d = None, 1e9
            for name, col in zip(names, colors):
                d = sum((a - b) ** 2 for a, b in zip(lin, col))
                if d < best_d:
                    best, best_d = name, d
            cache[key] = best
        out.append(cache[key])
    return out


def tile_pass_color(i: int, j: int) -> tuple[float, float, float]:
    """Linear colour the rig's tile pass renders for footprint tile (i, j).

    Keep in sync with ``tile_pass_color`` in ``blender/pz_sprite_forge.py`` --
    the addon must stay a self-contained single file, so the formula lives on
    both sides of the render contract.
    """
    return (0.05 + 0.055 * i, 0.05 + 0.055 * j, 0.5)


def tile_keep_mask(tile_png: Image.Image, own: str,
                   palette: dict[str, list[float]]) -> Image.Image:
    """255 where a pixel stands on footprint tile ``own``, else 0.

    A rotated multi-tile footprint can fit the whole object inside one cell's
    camera frame, so the frame no longer cuts the object between cells --
    every cell would carry a complete copy. Vanilla cuts its art along the
    tile seam planes: each sprite holds exactly the pixels whose surface point
    stands on its own tile. The rig's tile pass (``*_T.png``) encodes that
    assignment; this decodes it into an alpha mask. The silhouette
    antialiasing ring (no tile id) is kept where it touches the own-tile
    region, so the outer edge stays soft while the seam cut stays exclusive.
    """
    labels = decode_labels(tile_png.convert("RGBA"), palette)
    w, h = tile_png.size
    mask = Image.new("L", (w, h), 0)
    px = mask.load()
    for k, lab in enumerate(labels):
        if lab == own:
            px[k % w, k // w] = 255
    near = mask.filter(ImageFilter.MaxFilter(5)).load()
    for k, lab in enumerate(labels):
        if lab is None and near[k % w, k // w]:
            px[k % w, k // w] = 255
    return mask


def finish(img: Image.Image, labels: list[str | None],
           strength: float = 1.0, recipes: dict | None = None,
           top_mask: list[bool] | None = None) -> Image.Image:
    """Finish every element boundary and small fitting toward the painted look.

    All factors are computed against the original pixels first and applied once,
    so treatments compose without feedback. Alpha is never touched, and the pass
    is deliberately hue-preserving: accents scale a pixel's own colour. Accent
    amplitude comes from the corpus recipes (median painted lip beside vanilla
    linework), applied as an additive value delta the way the painter adds it.
    """
    if strength <= 0:
        return img.convert("RGBA")
    if recipes is None:
        from .recipe import load
        recipes = load()
    accent = (recipes.get("line", {}).get("accent_amount") or 0.04)
    img = img.convert("RGBA")
    w, h = img.size
    if len(labels) != w * h:
        raise ValueError(f"label map is {len(labels)} px, image is {w * h}")
    px = list(img.getdata())

    # -- per-part extents, for the drifts and grades ------------------------
    boxes: dict[str, list[int]] = {}
    counts: dict[str, int] = {}
    for i, lab in enumerate(labels):
        if lab is None or px[i][3] == 0:
            continue
        x, y = i % w, i // w
        counts[lab] = counts.get(lab, 0) + 1
        box = boxes.setdefault(lab, [x, y, x, y])
        box[0] = min(box[0], x)
        box[1] = min(box[1], y)
        box[2] = max(box[2], x)
        box[3] = max(box[3], y)

    values = [(max(p[:3]) / 255.0 if p[3] > 0 else None) for p in px]

    #: Below this value a surface counts as dark linework; boundaries where both
    #: sides are dark get no accent -- the painter does not outline dark-on-dark,
    #: and accenting there turned groove/strap intersections into confetti.
    DARK_V = 0.30

    def accent_ok(i: int, j: int) -> bool:
        vi, vj = values[i], (values[j] if 0 <= j < w * h else None)
        if vi is None:
            return False
        return not (vi < DARK_V and vj is not None and vj < DARK_V)

    factors = [1.0] * (w * h)
    s = strength
    for i, lab in enumerate(labels):
        if lab is None or px[i][3] == 0:
            continue
        x, y = i % w, i // w
        up = labels[i - w] if y > 0 else None
        down = labels[i + w] if y < h - 1 else None
        left = labels[i - 1] if x > 0 else None
        right = labels[i + 1] if x < w - 1 else None

        # A boundary side only counts as part of a drawn edge if the run continues
        # into at least one lateral neighbour of the same part -- single-pixel
        # corners at part intersections otherwise collect every accent at once.
        def run2(d_off: int, lat_off: int) -> bool:
            for lat in (-lat_off, lat_off):
                j = i + lat
                if 0 <= j < w * h and labels[j] == lab:
                    k = j + d_off
                    if not (0 <= k < w * h) or labels[k] != lab:
                        return True
            return False

        # Screen-up is world-far on an up-facing surface: lighting a lid's far rim
        # reads as a concave dish. On top faces the vertical accents flip so the
        # near rim (screen-bottom, facing the key) takes the light.
        flip = bool(top_mask and top_mask[i])
        up_ratio, down_ratio = ((BOTTOM_RATIO, TOP_RATIO) if flip
                                else (TOP_RATIO, BOTTOM_RATIO))
        dv = 0.0
        if up != lab and run2(-w, 1) and accent_ok(i, i - w):
            dv += accent * up_ratio * s
        if down != lab and run2(w, 1) and accent_ok(i, i + w):
            dv += accent * down_ratio * s
        if left != lab and run2(-1, w) and accent_ok(i, i - 1):
            dv += accent * SIDE_RATIO * s
        if right != lab and run2(1, w) and accent_ok(i, i + 1):
            dv -= accent * SIDE_RATIO * s

        f = 1.0
        v = values[i] or 0.0
        if dv and v > 1e-3:
            f = (v + dv) / v

        if counts.get(lab, 0) <= SMALL_PART_PX and not (top_mask and top_mask[i]):
            x0, y0, x1, y1 = boxes[lab]
            fx = (x - x0) / (x1 - x0) if x1 > x0 else 0.5
            fy = (y - y0) / (y1 - y0) if y1 > y0 else 0.5
            # lit top-left corner drifts bright, shaded bottom-right drifts dark
            f *= 1.0 + SMALL_DRIFT * s * ((0.5 - fx) + (0.5 - fy))

        factors[i] = min(max(f, CLAMP[0]), CLAMP[1])

    # -- top-face grade -----------------------------------------------------
    # Graded per connected up-facing *region*, not per element: a lid built as a
    # material slot on the body is no separate element, and an element-gated
    # grade silently never fired on it. The normal pass knows what faces up
    # regardless of how the model is structured.
    if top_mask:
        seen = [False] * (w * h)
        for start, is_top in enumerate(top_mask):
            if not is_top or seen[start] or px[start][3] == 0 \
                    or labels[start] is None:
                continue
            stack = [start]
            seen[start] = True
            region = []
            while stack:
                i = stack.pop()
                region.append(i)
                x, y = i % w, i // w
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < w and 0 <= yy < h:
                        j = yy * w + xx
                        if not seen[j] and top_mask[j] and px[j][3] > 0 \
                                and labels[j] is not None:
                            seen[j] = True
                            stack.append(j)
            if len(region) < 100:
                continue
            xs = [i % w for i in region]
            ys = [i // w for i in region]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            for i in region:
                fx = (i % w - x0) / (x1 - x0) if x1 > x0 else 0.5
                fy = (i // w - y0) / (y1 - y0) if y1 > y0 else 0.5
                g = 1.0 + TOP_GRADE * s * ((0.5 - fx) + (fy - 0.5))
                factors[i] = min(max(factors[i] * g, CLAMP[0]), CLAMP[1])

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


def _element_tone_budget(area: int, window_median: float, window_px: int) -> int:
    """How many tones an element of this size gets blocked in with.

    Vanilla runs ``window_median`` tones per ``window_px`` square. Tone count
    grows with the element's linear size, not its area -- a big panel is still a
    few blocked tones -- so the budget scales with sqrt(area) against the window
    diagonal, clamped to the painter's practical range.
    """
    linear = area ** 0.5
    budget = round(window_median * linear / (2 * window_px))
    return max(2, min(7, budget))


def tone_block(img: Image.Image, labels: list[str | None],
               strength: float = 0.65, recipes: dict | None = None
               ) -> Image.Image:
    """Block each element in with its measured tone budget -- the subtracting pass.

    Vanilla's cleanliness is economy: a median of 3 tones per 12 px window where
    the styled render ran 4+. Every other pass in the chain *adds* detail; this
    one takes it away, per element, by clustering the element's values into its
    tone budget (1D k-means seeded at quantiles) and pulling every pixel toward
    its cluster centre. ``strength`` below 1 keeps the pull partial so texture
    survives as variation around each block instead of being erased.
    """
    if strength <= 0:
        return img.convert("RGBA")
    if recipes is None:
        from .recipe import load
        recipes = load()
    wt = recipes.get("window_tones", {})
    window_median = wt.get("median", 3)
    window_px = wt.get("window_px", 12)

    img = img.convert("RGBA")
    w, h = img.size
    if len(labels) != w * h:
        raise ValueError(f"label map is {len(labels)} px, image is {w * h}")
    px = list(img.getdata())

    members: dict[str, list[int]] = {}
    for i, lab in enumerate(labels):
        if lab is not None and px[i][3] > 0:
            members.setdefault(lab, []).append(i)

    out = px[:]
    for lab, idxs in members.items():
        values = [max(px[i][:3]) / 255.0 for i in idxs]
        k = _element_tone_budget(len(idxs), window_median, window_px)
        lo, hi = min(values), max(values)
        if hi - lo < 1e-4:
            continue
        # 1D k-means, seeded at the element's own quantiles.
        svals = sorted(values)
        centres = [svals[int((j + 0.5) / k * (len(svals) - 1))] for j in range(k)]
        for _ in range(8):
            sums = [0.0] * k
            ns = [0] * k
            for v in values:
                best = min(range(k), key=lambda j: abs(v - centres[j]))
                sums[best] += v
                ns[best] += 1
            new = [sums[j] / ns[j] if ns[j] else centres[j] for j in range(k)]
            if all(abs(a - b) < 1e-4 for a, b in zip(new, centres)):
                centres = new
                break
            centres = new
        for i, v in zip(idxs, values):
            if v <= 1e-3:
                continue
            target = min(centres, key=lambda c: abs(v - c))
            v2 = v + (target - v) * strength
            f = v2 / v
            p = px[i]
            out[i] = (min(255, round(p[0] * f)), min(255, round(p[1] * f)),
                      min(255, round(p[2] * f)), p[3])

    result = Image.new("RGBA", (w, h))
    result.putdata(out)
    return result
