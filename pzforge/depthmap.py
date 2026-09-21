"""Build 42 depth maps: the per-pixel depth texture the renderer actually samples.

The geometry in ``tileGeometry.txt`` is the *source*; what the game reads at
render time is ``media/depthmaps/DEPTH_<tileset>.png``, a grey image laid out
exactly like the 2x tile sheet (8 columns of 128x256 cells) whose value is
the distance of the surface seen at that pixel along the view direction, and
whose alpha is 0 where the tile has no surface. A tileset without one falls
back to a billboard, and two billboards on one square that overlap on screen
are staggered against each other -- which is what put a checkerboard of the
cask through the deck above it even after the geometry shipped.

The encoding is measured, not guessed: :func:`calibrate` ray-casts a vanilla
tileset's boxes from ``tileGeometry.txt`` at every pixel of its shipped depth
map and fits ``value = scale * depth + offset`` (see ``reference/
depth_calibration.json`` for the numbers and their residual). :func:`render`
then applies the same projection and fit to a sheet's own boxes -- the ones
the rig exports into the cells manifest -- and writes ``DEPTH_<sheet>.png``.

Projection (2x cell, origin at the tile's floor centre = pixel (64, 224)):
x east -> (+64, +32) px per metre, z south -> (-64, +32), y up -> (0, -86).
The camera looks along -(0.612, 0.5, 0.612) in (x, y, z), the 2:1 dimetric
view; the depth of a point is its coordinate along that direction, signed so
that nearer is smaller. The fit absorbs the game's own scale and zero.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from PIL import Image

CELL_W, CELL_H = 128, 256
COLS = 8
#: Floor centre of the tile inside a cell, and the screen vectors of the axes.
ORIGIN = (64.0, 224.0)
AX = (64.0, 32.0)      # +1 m east
AZ = (-64.0, 32.0)     # +1 m south
AY = (0.0, -86.0)      # +1 m up (43 px/m at 1x)
#: Unit vector from the scene toward the camera (x, y, z): the true 2:1
#: dimetric view (30 degrees up), the rig's own. Measured, not derived: the
#: fit on furniture_storage_02 is 1.6 units rms with this direction and 8.7
#: with the one the pixel projection above would imply ((1, 0.744, 1)
#: normalised), so the game's baker casts true camera rays while drawing a
#: metre of height at 43 px -- and the depth maps follow the baker.
TO_CAMERA = (0.612, 0.5, 0.612)
UNITS = 10000.0
CALIBRATION_PATH = Path(__file__).resolve().parents[1] / "reference" / "depth_calibration.json"


# --------------------------------------------------------------------------- #
# geometry file parsing (boxes only; enough for calibration and for our sheets)
# --------------------------------------------------------------------------- #

_WORD = re.compile(r"[A-Za-z_]+")


def _blocks(text: str, keyword: str):
    """Yield the body of every balanced ``keyword { ... }`` block in ``text``."""
    pos = 0
    while True:
        start = text.find(keyword, pos)
        if start < 0:
            return
        end_kw = start + len(keyword)
        before = text[start - 1] if start > 0 else " "
        after = text[end_kw] if end_kw < len(text) else " "
        if before.isalnum() or before == "_" or after.isalnum() or after == "_":
            pos = end_kw
            continue
        brace = end_kw + len(text[end_kw:]) - len(text[end_kw:].lstrip())
        if brace >= len(text) or text[brace] != "{":
            pos = end_kw
            continue
        depth, i = 0, brace
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        yield text[brace + 1:i]
        pos = i + 1


def parse_boxes(text: str, tileset: str) -> dict[int, list[dict]]:
    """``{sprite index: [box, ...]}`` for one tileset of a tileGeometry file.

    Each box is ``{"min": [x, y, z], "max": [x, y, z]}`` in metres, with its
    ``translate`` folded in. Rotated boxes are skipped (none of ours rotate;
    vanilla's few are not needed for calibration).
    """
    block = None
    for body in _blocks(text, "tileset"):
        m = re.search(r"name\s*=\s*([A-Za-z0-9_]+)\s*,", body)
        if m and m.group(1) == tileset:
            block = body
            break
    if block is None:
        raise ValueError(f"tileset {tileset} not in geometry text")
    out: dict[int, list[dict]] = {}
    for body in _blocks(block, "tile"):
        xy = re.search(r"xy\s*=\s*(\d+)x(\d+)", body)
        if not xy:
            continue
        index = int(xy.group(1)) + COLS * int(xy.group(2))
        boxes = []
        for b in _blocks(body, "box"):
            rot = re.search(r"rotate\s*=\s*(-?\d+)x(-?\d+)x(-?\d+)", b)
            if rot and any(int(v) for v in rot.groups()):
                continue
            tr = re.search(r"translate\s*=\s*(-?\d+)x(-?\d+)x(-?\d+)", b)
            lo = re.search(r"min\s*=\s*(-?\d+)x(-?\d+)x(-?\d+)", b)
            hi = re.search(r"max\s*=\s*(-?\d+)x(-?\d+)x(-?\d+)", b)
            if not (lo and hi):
                continue
            t = [int(v) / UNITS for v in tr.groups()] if tr else [0, 0, 0]
            boxes.append({"min": [int(v) / UNITS + t[k] for k, v in enumerate(lo.groups())],
                          "max": [int(v) / UNITS + t[k] for k, v in enumerate(hi.groups())]})
        out[index] = boxes
    return out


# --------------------------------------------------------------------------- #
# projection and ray casting
# --------------------------------------------------------------------------- #

def project(x: float, y: float, z: float) -> tuple[float, float]:
    return (ORIGIN[0] + AX[0] * x + AZ[0] * z + AY[0] * y,
            ORIGIN[1] + AX[1] * x + AZ[1] * z + AY[1] * y)


def view_depth(x: float, y: float, z: float) -> float:
    """Distance along the view direction; larger is farther from the camera."""
    return -(TO_CAMERA[0] * x + TO_CAMERA[1] * y + TO_CAMERA[2] * z)


def pixel_ray(u: float, v: float) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """A point on the ray through cell pixel (u, v) (at y = 0) and the ray's
    direction toward the camera."""
    dx = (u - ORIGIN[0]) / AX[0]            # x - z
    dz = (v - ORIGIN[1]) / AX[1]            # x + z  (at y = 0)
    x = (dx + dz) / 2.0
    z = (dz - dx) / 2.0
    return (x, 0.0, z), TO_CAMERA


def nearest_hit(u: float, v: float, boxes: list[dict]) -> float | None:
    """View depth of the nearest box surface on the ray through (u, v), or None."""
    p0, d = pixel_ray(u, v)
    best = None
    for box in boxes:
        t_lo, t_hi = -math.inf, math.inf
        ok = True
        for k in range(3):
            lo, hi = box["min"][k], box["max"][k]
            if abs(d[k]) < 1e-9:
                if not (lo <= p0[k] <= hi):
                    ok = False
                    break
                continue
            t1 = (lo - p0[k]) / d[k]
            t2 = (hi - p0[k]) / d[k]
            if t1 > t2:
                t1, t2 = t2, t1
            t_lo, t_hi = max(t_lo, t1), min(t_hi, t2)
        if not ok or t_lo > t_hi:
            continue
        # the largest t is the point nearest the camera on this box
        p = (p0[0] + d[0] * t_hi, p0[1] + d[1] * t_hi, p0[2] + d[2] * t_hi)
        dep = view_depth(*p)
        if best is None or dep < best:
            best = dep
    return best


# --------------------------------------------------------------------------- #
# calibration against vanilla
# --------------------------------------------------------------------------- #

def calibrate(geometry_text: str, depth_png: Path, tileset: str, *,
              indices: list[int] | None = None, step: int = 2) -> dict:
    """Fit value = scale * depth + offset on a vanilla tileset's depth map."""
    boxes_by_index = parse_boxes(geometry_text, tileset)
    im = Image.open(depth_png).convert("LA")
    xs, ys = [], []
    for index, boxes in boxes_by_index.items():
        if indices and index not in indices:
            continue
        if not boxes:
            continue
        cx, cy = (index % COLS) * CELL_W, (index // COLS) * CELL_H
        if cx + CELL_W > im.width or cy + CELL_H > im.height:
            continue
        for v in range(0, CELL_H, step):
            for u in range(0, CELL_W, step):
                val, alpha = im.getpixel((cx + u, cy + v))
                if alpha == 0:
                    continue
                dep = nearest_hit(u + 0.5, v + 0.5, boxes)
                if dep is None:
                    continue
                xs.append(dep)
                ys.append(val)
    n = len(xs)
    if n < 10:
        raise ValueError("too few samples to fit")
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    scale = sxy / sxx
    offset = my - scale * mx
    resid = [y - (scale * x + offset) for x, y in zip(xs, ys)]
    rms = math.sqrt(sum(r * r for r in resid) / n)
    return {"tileset": tileset, "samples": n, "scale": scale, "offset": offset,
            "rms": rms, "max_abs": max(abs(r) for r in resid)}


def load_calibration() -> dict:
    return json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# rendering our own
# --------------------------------------------------------------------------- #

def render_cell(boxes: list[dict], scale: float, offset: float) -> Image.Image:
    """One 128x256 LA cell: the nearest box surface's encoded depth per pixel."""
    cell = Image.new("LA", (CELL_W, CELL_H), (0, 0))
    px = cell.load()
    for v in range(CELL_H):
        for u in range(CELL_W):
            dep = nearest_hit(u + 0.5, v + 0.5, boxes)
            if dep is None:
                continue
            val = int(round(scale * dep + offset))
            px[u, v] = (max(0, min(255, val)), 255)
    return cell


def render(tiles: dict[int, list[dict]], scale: float, offset: float,
           cols: int = COLS) -> Image.Image:
    """The whole ``DEPTH_<sheet>.png``: ``tiles`` maps sprite index to boxes."""
    rows = max(1, -(-(max(tiles) + 1) // cols)) if tiles else 1
    sheet = Image.new("LA", (cols * CELL_W, rows * CELL_H), (0, 0))
    for index, boxes in tiles.items():
        if not boxes:
            continue
        sheet.paste(render_cell(boxes, scale, offset),
                    ((index % cols) * CELL_W, (index // cols) * CELL_H))
    return sheet


def tiles_from_manifest(manifest: dict, sheet_cells) -> dict[int, list[dict]]:
    by_file = {rec["file"]: rec for rec in manifest.get("cells", [])}
    out: dict[int, list[dict]] = {}
    for cell in sheet_cells:
        rec = by_file.get(cell.source)
        if not rec or not rec.get("geometry"):
            continue
        out[cell.index] = [{"min": g["min"], "max": g["max"]} for g in rec["geometry"]]
    return out
