"""Build 42 tile geometry: the writer, the file merger and the manifest pairing,
without Blender.

Run with:  uv run --python 3.12 --with pillow python tests/test_tilegeometry.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pzforge import geometry as geom  # noqa: E402
from pzforge.sheet import Cell  # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  -- ' + detail if detail else ''}")
    if not ok:
        FAILURES.append(label)


def balanced(text: str) -> bool:
    return text.count("{") == text.count("}")


def test_writer() -> None:
    print("\n== writer ==")
    tiles = [{"index": 1, "boxes": [{"min": [-0.5, 0.095, -0.46], "max": [0.5, 0.15, 0.46]}],
              "properties": {"Surface": 6, "Material": "Wood"}},
             {"index": 0, "boxes": [{"min": [-0.3225, 0.0, -0.3785], "max": [0.3225, 0.645, 0.3785]},
                                    {"min": [0.46, 0, -0.46], "max": [0.5, 1.6, -0.375]}]}]
    block = geom.tileset_text("hb_rack_01", tiles)
    text = geom.file_text([block])
    check("braces balance", balanced(text))
    check("version header", "VERSION = 2," in text)
    check("sprite 0 comes first", text.index("hb_rack_01_0") < text.index("hb_rack_01_1"))
    check("index -> grid xy", "xy = 1x0," in text and "xy = 0x0," in text)
    check("metres become 1/10000 units", "min = -5000x950x-4600," in text and "max = 5000x1500x4600," in text)
    check("only echoed properties survive", "Surface = 6," in text and "Material" not in text)
    check("two boxes on sprite 0", text.split("hb_rack_01_0")[1].split("hb_rack_01_1")[0].count("box") == 2)
    check("index 9 wraps to the second row", "xy = 1x1," in geom.tileset_text("s", [{"index": 9, "boxes": []}]))


def test_merge() -> None:
    print("\n== merge ==")
    a = geom.file_text([geom.tileset_text("hb_rack_01", [{"index": 0, "boxes": [{"min": [0, 0, 0], "max": [1, 1, 1]}]}])])
    b = geom.file_text([geom.tileset_text("hb_barrel_racked_01", [{"index": 0, "boxes": [{"min": [0, 0, 0], "max": [0.6, 0.6, 0.7]}]}])])
    merged = geom.merge(a, b)
    blocks = geom.split_tilesets(merged)
    check("both tilesets present", set(blocks) == {"hb_rack_01", "hb_barrel_racked_01"}, str(set(blocks)))
    check("merged file balances", balanced(merged))
    a2 = geom.file_text([geom.tileset_text("hb_rack_01", [{"index": 0, "boxes": [{"min": [0, 0, 0], "max": [2, 2, 2]}]}])])
    merged2 = geom.merge(merged, a2)
    blocks2 = geom.split_tilesets(merged2)
    check("same-named tileset is replaced, not duplicated", merged2.count("name = hb_rack_01,") == 1)
    check("replacement carries the new box", "max = 20000x20000x20000," in blocks2["hb_rack_01"])
    check("other tileset untouched", "max = 6000x6000x7000," in blocks2["hb_barrel_racked_01"])
    check("keyword inside tileGeometry header is not a tileset", "tileGeometry" not in blocks2)
    check("merge into nothing", set(geom.split_tilesets(geom.merge(None, b))) == {"hb_barrel_racked_01"})


def test_manifest_pairing() -> None:
    print("\n== manifest pairing ==")
    manifest = {"cells": [
        {"file": "s_S_x0_y0.png", "facing": "S", "geometry": [{"group": "cask", "min": [0, 0, 0], "max": [1, 1, 1]}]},
        {"file": "s_E_x0_y0.png", "facing": "E", "geometry": []},
    ]}
    cells = [Cell(None, "S", source="s_S_x0_y0.png"), Cell(None, "E", source="s_E_x0_y0.png")]
    cells[0].index, cells[1].index = 0, 1
    tiles = geom.tiles_from_manifest(manifest, cells, {"Surface": 6})
    check("cells with geometry are kept, empty ones skipped", [t["index"] for t in tiles] == [0])
    check("boxes and echoed props carried", tiles[0]["boxes"][0]["max"] == [1, 1, 1] and tiles[0]["properties"] == {"Surface": 6})


if __name__ == "__main__":
    test_writer()
    test_merge()
    test_manifest_pairing()
    print(f"\n{'ALL PASS' if not FAILURES else 'FAILED: ' + ', '.join(FAILURES)}")
    raise SystemExit(1 if FAILURES else 0)
