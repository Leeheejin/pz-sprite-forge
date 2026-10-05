"""Build 42 depth maps: projection, box ray casting, rendering and the fit,
without the game files.

Run with:  uv run --python 3.12 --with pillow python tests/test_depthmap.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pzforge import depthmap as dm  # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  -- ' + detail if detail else ''}")
    if not ok:
        FAILURES.append(label)


GEOMETRY = """tileGeometry
{
    VERSION = 2,

    tileset
    {
        name = test_01,

        /* test_01_0 */
        tile
        {
            xy = 0x0,

            box
            {
                translate = 0x0x0,
                rotate = 0x0x0,
                min = -5000x0x-5000,
                max = 5000x10000x5000,
            }
        }

        /* test_01_1 */
        tile
        {
            xy = 1x0,

            box
            {
                translate = 1000x0x0,
                rotate = 0x0x0,
                min = -2000x0x-2000,
                max = 2000x5000x2000,
            }

            box
            {
                translate = 0x0x0,
                rotate = 0x900000x0,
                min = -1x0x-1,
                max = 1x1x1,
            }
        }
    }
}
"""


def test_parse() -> None:
    print("\n== parse ==")
    tiles = dm.parse_boxes(GEOMETRY, "test_01")
    check("two tiles", sorted(tiles) == [0, 1], str(sorted(tiles)))
    check("box in metres", tiles[0][0]["max"] == [0.5, 1.0, 0.5])
    close = lambda a, b: all(abs(x - y) < 1e-9 for x, y in zip(a, b))  # noqa: E731
    check("translate applied", close(tiles[1][0]["min"], [-0.1, 0.0, -0.2]) and close(tiles[1][0]["max"], [0.3, 0.5, 0.2]))
    check("rotated box skipped", len(tiles[1]) == 1)


def test_projection() -> None:
    print("\n== projection ==")
    u, v = dm.project(0.0, 0.0, 0.0)
    check("floor centre at the origin pixel", (u, v) == dm.ORIGIN)
    u, v = dm.project(0.5, 0.0, 0.5)
    check("near corner straight below the centre", u == 64.0 and v == 224.0 + 32.0)
    p0, d = dm.pixel_ray(64.0, 256.0)
    check("pixel ray recovers the floor point", abs(p0[0] - 0.5) < 1e-9 and abs(p0[2] - 0.5) < 1e-9)
    check("nearer is smaller", dm.view_depth(0.5, 0.0, 0.5) < dm.view_depth(-0.5, 0.0, -0.5))
    check("higher is nearer", dm.view_depth(0.0, 1.0, 0.0) < dm.view_depth(0.0, 0.0, 0.0))


def test_raycast() -> None:
    print("\n== ray cast ==")
    box = [{"min": [-0.5, 0.0, -0.5], "max": [0.5, 1.0, 0.5]}]
    # the ray through the floor centre pixel enters the block at the floor
    # centre and leaves toward the camera through the east/south edge: along
    # (0.612, 0.5, 0.612) it reaches x = 0.5 at t = 0.817, i.e. (0.5, 0.41, 0.5)
    u, v = dm.ORIGIN
    hit = dm.nearest_hit(u, v, box)
    p0, c = dm.pixel_ray(u, v)
    t = (0.5 - p0[0]) / c[0]
    exit_point = (0.5, p0[1] + c[1] * t, p0[2] + c[2] * t)
    check("hit is the surface the ray leaves through", hit is not None and abs(hit - dm.view_depth(*exit_point)) < 1e-6)
    check("that surface is nearer than the entry point", hit is not None and hit < dm.view_depth(*p0))
    # a pixel far above the block sees nothing
    check("miss above the block", dm.nearest_hit(64.0, 10.0, box) is None)
    # a small block standing in front of the big one wins where they overlap
    front = [{"min": [0.6, 0.0, 0.6], "max": [0.9, 0.3, 0.9]}]
    u, v = dm.project(0.75, 0.15, 0.75)
    both = dm.nearest_hit(u, v, box + front)
    only_front = dm.nearest_hit(u, v, front)
    check("nearest block wins", both is not None and only_front is not None and abs(both - only_front) < 1e-9)


def test_render_and_fit() -> None:
    print("\n== render + fit ==")
    boxes = [{"min": [-0.5, 0.0, -0.5], "max": [0.5, 1.0, 0.5]}]
    cell = dm.render_cell(boxes, 100.0, 200.0)
    px = cell.load()
    u, v = dm.project(0.0, 0.5, 0.0)
    val, alpha = px[int(u), int(v)]
    expected = 100.0 * dm.nearest_hit(int(u) + 0.5, int(v) + 0.5, boxes) + 200.0
    check("pixel value is the fit applied to the ray hit", alpha == 255 and abs(val - expected) <= 1)
    check("empty where no surface", px[64, 5][1] == 0)
    sheet = dm.render({0: boxes, 9: boxes}, 100.0, 200.0)
    check("sheet lays sprites out in 8 columns", sheet.size == (8 * dm.CELL_W, 2 * dm.CELL_H))
    # a synthetic vanilla map made with a known encoding must fit back to it
    tmp = Path(tempfile.mkdtemp(prefix="pzforge_depth_"))
    tiles = dm.parse_boxes(GEOMETRY, "test_01")
    dm.render(tiles, 103.8, 190.3).save(tmp / "DEPTH_test_01.png")
    fit = dm.calibrate(GEOMETRY, tmp / "DEPTH_test_01.png", "test_01", step=2)
    check("fit recovers scale", abs(fit["scale"] - 103.8) < 0.5, f"{fit['scale']:.2f}")
    check("fit recovers offset", abs(fit["offset"] - 190.3) < 0.8, f"{fit['offset']:.2f}")
    check("fit is tight", fit["rms"] < 0.6, f"rms {fit['rms']:.2f}")


if __name__ == "__main__":
    test_parse()
    test_projection()
    test_raycast()
    test_render_and_fit()
    print(f"\n{'ALL PASS' if not FAILURES else 'FAILED: ' + ', '.join(FAILURES)}")
    raise SystemExit(1 if FAILURES else 0)
