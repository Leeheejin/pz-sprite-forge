"""The asset harness without Blender: stacking, measuring, spec loading and the
preview/measure steps of a run, on synthetic sprites.

Run with:  uv run --python 3.12 --with pillow python tests/test_assets.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pzforge import assets  # noqa: E402
from pzforge.cli import main as cli_main  # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  -- ' + detail if detail else ''}")
    if not ok:
        FAILURES.append(label)


def make_sheet(png: Path, sheet: str, colour: tuple, block_h: int = 40) -> None:
    """Four 2x cells (128x256) with a solid block of ``colour`` at the bottom."""
    png.mkdir(parents=True, exist_ok=True)
    for k in range(4):
        im = Image.new("RGBA", (128, 256), (0, 0, 0, 0))
        for y in range(256 - block_h, 256):
            for x in range(24, 104):
                im.putpixel((x, y), colour)
        im.save(png / f"{sheet}_{k}.png")


def test_stack(tmp: Path) -> None:
    print("\n== stack ==")
    make_sheet(tmp / "a", "aa_01", (200, 40, 40, 255))
    make_sheet(tmp / "b", "bb_01", (40, 200, 40, 255), block_h=20)
    layers = [assets.Layer(tmp / "a", "aa_01", 0), assets.Layer(tmp / "b", "bb_01", 45)]
    image = assets.stack(layers, facings=4, scale=1, labels=False)
    # cell: width 128, height 256 + 45*2 (the raised layer's headroom)
    check("four facings side by side", image.width == 4 * 128 + 5 * 12, str(image.size))
    check("headroom for the offset layer", image.height == 256 + 90 + 24, str(image.size))
    # first cell starts at x = 12, y = 12; layer a's block occupies the bottom 40 rows,
    # layer b's block sits 90 px (45 * 2x) above the bottom
    bottom = 12 + 256 + 90
    check("base layer at the bottom", image.getpixel((12 + 64, bottom - 5))[:3] == (200, 40, 40))
    check("offset layer drawn 90 px higher", image.getpixel((12 + 64, bottom - 90 - 5))[:3] == (40, 200, 40))
    check("offset layer not at the bottom", image.getpixel((12 + 64, bottom - 25))[:3] == (200, 40, 40))
    parsed = assets.Layer.parse(str(tmp / "b" / "bb_01") + ":45")
    check("Layer.parse takes DIR/SHEET:OFFSET", parsed.sheet == "bb_01" and parsed.offset == 45
          and parsed.png == tmp / "b")


def test_measure(tmp: Path) -> None:
    print("\n== measure ==")
    im = Image.new("RGB", (40, 20), (0, 0, 0))
    for x in range(20):
        for y in range(20):
            im.putpixel((x, y), (100, 100, 100))
            im.putpixel((x + 20, y), (50, 50, 50))
    path = tmp / "m.png"
    im.save(path)
    m = assets.measure(path, {"lit": (0, 0, 20, 20), "shade": (20, 0, 40, 20)})
    check("luminance of a flat grey", abs(m["lit"]["lum"] - 100) < 0.01, f"{m['lit']['lum']:.2f}")
    results = assets.check_expectations(m, [{"ratio": "shade/lit", "min": 0.45, "max": 0.55},
                                            {"patch": "lit", "min": 120, "max": 130}])
    check("ratio inside its band passes", results[0][1], results[0][2])
    check("patch outside its band fails", not results[1][1], results[1][2])
    rc = cli_main(["measure", str(path), "lit:0,0,20,20", "shade:20,0,40,20",
                   "--expect", "shade/lit=0.45,0.55"])
    check("measure command exits 0 on pass", rc == 0)
    rc = cli_main(["measure", str(path), "lit:0,0,20,20", "--expect", "lit=0,10"])
    check("measure command exits 1 on fail", rc == 1)


def test_spec() -> None:
    print("\n== homebrewing spec ==")
    spec = assets.load_spec(ROOT / "examples" / "homebrewing_assets.json")
    check("eight sheets", len(spec.assets) == 8, str([a.name for a in spec.assets]))
    check("every recipe exists", all((ROOT / a.recipe).exists() for a in spec.assets))
    check("tiledef ids unique",
          len({a.build[a.build.index("--tiledef-id") + 1] for a in spec.assets}) == 8)
    sheets = {a.sheet_name() for a in spec.assets}
    layered = {l["sheet"] for p in spec.previews for l in p["layers"]}
    check("previews only stack sheets the spec builds", layered <= sheets, str(layered - sheets))
    check("measures point at a preview",
          all(m["image"] in {p["out"] for p in spec.previews} for m in spec.measures))
    check("shipped sheets of unknown style flags are not installed by default",
          not any(a.install for a in spec.assets if a.name in ("still", "barrel")))


def test_run(tmp: Path) -> None:
    print("\n== run (preview + measure only) ==")
    make_sheet(tmp / "r", "rr_01", (120, 120, 120, 255))
    make_sheet(tmp / "s", "ss_01", (60, 60, 60, 255), block_h=20)
    out = tmp / "col.png"
    spec = {
        "blender": "blender-not-used",
        "assets": [],
        "previews": [{"name": "col", "out": str(out), "scale": 1,
                      "layers": [{"png": str(tmp / "r"), "sheet": "rr_01"},
                                 {"png": str(tmp / "s"), "sheet": "ss_01", "offset": 45}]}],
        "measures": [{"name": "col", "image": str(out),
                      "patches": {"lit": [12 + 30, 12 + 24 + 256 + 90 - 30, 12 + 90, 12 + 24 + 256 + 90 - 5],
                                  "shade": [12 + 30, 12 + 24 + 256 - 15, 12 + 90, 12 + 24 + 256 - 5]},
                      "expect": [{"ratio": "shade/lit", "min": 0.45, "max": 0.55}]}],
    }
    path = tmp / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    lines: list[str] = []
    harness = assets.Harness(assets.load_spec(path), log=lines.append)
    failures = harness.run(render=False, build=False, extract=False)
    check("preview written", out.exists())
    check("measure passes on the synthetic column", failures == 0, "; ".join(lines[-4:]))
    rc = cli_main(["assets", str(path), "--no-render", "--no-build", "--no-extract"])
    check("assets command exits 0", rc == 0)


if __name__ == "__main__":
    tmp = Path(tempfile.mkdtemp(prefix="pzforge_assets_"))
    try:
        test_stack(tmp)
        test_measure(tmp)
        test_spec()
        test_run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'ALL PASS' if not FAILURES else 'FAILED: ' + ', '.join(FAILURES)}")
    raise SystemExit(1 if FAILURES else 0)
