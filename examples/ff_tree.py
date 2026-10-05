"""FruitFarming tree crops from painted stamp kits (see pzforge.stamp).

For each crop: cut a kit out of its painted reference sheet, compose the 8 growth cells
along the measured tree structure, and build the tile pack with the derived health
variants. Crops without a painted sheet of their own borrow a kit and recolour the fruit
(apple from the peach kit, lime from the lemon kit) -- the leaves and trunk are shared
vocabulary, the fruit hue is the identity.

usage:
    uv run --python 3.12 --with pillow python examples/ff_tree.py <sheets_dir> [crop ...]
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pzforge.stamp import Bands, TreeSpec, compose_sheet, extract_kit, load_kit  # noqa: E402

SHEETS = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
KITS = ROOT / "build" / "kits"
CELLS = ROOT / "build"
OUT = ROOT / "dist"

#: crop -> (sheet stem, Bands for extraction, TreeSpec for composition, tiledef id).
#: Fruit hue bands were measured per sheet (hue x value histogram of saturated
#: non-canopy pixels in the ripe cell); leaf bands are raised above the fruit band where
#: the fruit is yellow-green (pear, avocado, olive) so a fruit is never cut as a leaf.
CROPS = {
    "cherry":     ("Tree_Cherry", Bands(), TreeSpec(fruit_count=22), 2002),
    "peach":      ("Tree_Peach", Bands(fruit=(340.0, 35.0), fruit_val=(0.40, 1.0)),
                   TreeSpec(fruit_count=20, fruit_band=(340.0, 35.0)), 2003),
    "apple":      ("Tree_Peach", Bands(fruit=(340.0, 35.0), fruit_val=(0.40, 1.0)),
                   TreeSpec(fruit_count=20, fruit_band=(340.0, 35.0),
                            ripe_recolour=((340.0, 35.0), 2.0, 1.08, 0.92)), 2004),
    "pear":       ("Tree_Pear", Bands(leaf=(76.0, 170.0), fruit=(44.0, 75.0), fruit_val=(0.30, 1.0)),
                   TreeSpec(fruit_count=16, fruit_band=(44.0, 75.0),
                            unripe_recolour=((44.0, 75.0), 95.0, 0.80, 0.80)), 2005),
    "orange":     ("Tree_Orange", Bands(fruit=(15.0, 55.0), fruit_val=(0.45, 1.0),
                                        bark_sat=(0.08, 0.95)),
                   TreeSpec(fruit_count=18, fruit_band=(15.0, 55.0)), 2006),
    "lemon":      ("Tree_Lemon", Bands(fruit=(35.0, 62.0), fruit_val=(0.45, 1.0)),
                   TreeSpec(fruit_count=18, fruit_band=(35.0, 62.0),
                            unripe_recolour=((35.0, 62.0), 90.0, 0.80, 0.80)), 2007),
    "lime":       ("Tree_Lemon", Bands(fruit=(35.0, 62.0), fruit_val=(0.45, 1.0)),
                   TreeSpec(fruit_count=18, fruit_band=(35.0, 62.0),
                            ripe_recolour=((35.0, 62.0), 84.0, 0.95, 0.82),
                            unripe_recolour=((35.0, 62.0), 100.0, 0.75, 0.72)), 2008),
    "grapefruit": ("Tree_Grapefruit", Bands(fruit=(350.0, 35.0), fruit_val=(0.45, 1.0),
                                            bark_sat=(0.08, 0.95)),
                   TreeSpec(fruit_count=16, fruit_band=(350.0, 35.0)), 2009),
    "avocado":    ("Tree_Avocado", Bands(leaf=(76.0, 170.0), fruit=(45.0, 75.0), fruit_sat=0.25,
                                         fruit_val=(0.0, 0.50)),
                   TreeSpec(fruit_count=14, fruit_band=(45.0, 75.0),
                            unripe_recolour=((45.0, 75.0), 80.0, 1.0, 1.15)), 2010),
    "mango":      ("Tree_Mango", Bands(fruit=(350.0, 50.0), fruit_val=(0.45, 1.0)),
                   TreeSpec(fruit_count=14, fruit_band=(350.0, 50.0)), 2011),
    "olive":      ("Tree_Olive", Bands(leaf=(76.0, 170.0), fruit=(55.0, 72.0), fruit_sat=0.25,
                                       fruit_val=(0.0, 0.55), bark_sat=(0.08, 0.95)),
                   TreeSpec(fruit_count=18, fruit_band=(55.0, 72.0),
                            unripe_recolour=((55.0, 72.0), 85.0, 0.9, 1.2)), 2012),
    "coffee":     ("Tree_Coffee", Bands(fruit=(340.0, 20.0), fruit_val=(0.30, 1.0)),
                   TreeSpec(fruit_count=24, fruit_band=(340.0, 20.0)), 2013),
    "banana":     ("Tree_Banana", Bands(fruit=(35.0, 65.0), fruit_val=(0.35, 1.0),
                                        bark=(35.0, 65.0), bark_sat=(0.5, 1.0), bark_val=(0.2, 0.7)),
                   TreeSpec(fruit_count=3, fruit_scale=1.0, fruit_band=(35.0, 65.0),
                            unripe_recolour=((35.0, 65.0), 85.0, 0.8, 0.85)), 2014),
}


def run(crop: str) -> bool:
    stem, bands, spec, tid = CROPS[crop]
    kit_dir = KITS / stem
    if not kit_dir.exists() or not any(kit_dir.glob("leaf_*.png")):
        counts = extract_kit(SHEETS / f"{stem}.png", kit_dir, bands)
        print(f"{crop:11s} kit {stem}: {counts}")
    kit = load_kit(kit_dir)
    name = f"ff_{crop}_01"
    cells = compose_sheet(kit, spec, CELLS / f"{name}_cells", name)
    cmd = [sys.executable, "-m", "pzforge.cli", "build", str(cells), "--out", str(OUT),
           "--mod-id", "FFCrops", "--mod-name", "FF Crops", "--sheet", name,
           "--tiledef-id", str(tid), "--no-style", "--health-variants"]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    ok = r.returncode == 0
    print(f"{crop:11s} {'built' if ok else 'FAILED'} {name} id={tid} "
          f"leaves={len(kit['leaf'])} fruit={len(kit['fruit'])} bloom={len(kit['bloom'])} "
          f"bark={len(kit['bark'])}")
    if not ok:
        print(r.stdout[-800:], r.stderr[-800:])
    return ok


if __name__ == "__main__":
    wanted = sys.argv[2:] or list(CROPS)
    results = {c: run(c) for c in wanted}
    print(f"{sum(results.values())}/{len(results)} built")
