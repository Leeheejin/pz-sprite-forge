"""Render, build and review the FruitFarming crop sheets (examples/ff_crop.py).

One driver for the whole crop pack, so every crop goes through the same designed path:
render the 8 growth stages headless -> ``pzforge build`` with the crop's own vanilla habit
as the shade reference (trees: vanilla ornamental trees) and the derived health variants
-> extract -> compare against the reference -> review sheets.

usage (from the repo root):
    uv run --python 3.12 --with pillow python examples/ff_build.py all apple pear -j 2
    uv run --python 3.12 --with pillow python examples/ff_build.py render cherry --layer stems
    uv run --python 3.12 --with pillow python examples/ff_build.py build cherry
    uv run --python 3.12 --with pillow python examples/ff_build.py strip cherry --tag v21
    uv run --python 3.12 --with pillow python examples/ff_build.py painted cherry --tag v21
    uv run --python 3.12 --with pillow python examples/ff_build.py measure cherry
    uv run --python 3.12 --with pillow python examples/ff_build.py sheet --tag final

Review images go to build/ff_review/. Machine paths: $BLENDER (the Blender executable)
and $FF_PAINTED (the folder of the mod user's painted Tree_*.png reference sheets, which
`painted`, `measure` and `fruitcal` read; the sheets are not in this repository).
"""
from __future__ import annotations

import argparse
import colorsys
import io
import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BLENDER = Path(os.environ.get("BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"))
PAINTED = Path(os.environ.get(
    "FF_PAINTED", r"C:\Users\leina\Downloads\PZ_FruitFarming-20260921T042745Z-1-001\PZ_FruitFarming"))
DIST = ROOT / "dist"
MOD_ID = "FFCrops"
REVIEW = ROOT / "build" / "ff_review"

#: tiledef ids TILEDEF_BASE upward in this order -- the same table examples/ff_wire_mod.py uses
#: tiledef ids start here: HomeBrewing claims 2000-2007 (still, barrels, the 3-tier rack),
#: so the crops' first numbering (2002 up) would have overwritten its tiles in any game
#: running both mods; 2100-2117 is free across installed and workshop mods
TILEDEF_BASE = 2100
ORDER = ("apple", "pear", "peach", "cherry", "orange", "lemon", "lime", "grapefruit",
         "avocado", "mango", "olive", "coffee", "peanut", "rice", "banana", "pineapple",
         "grape", "ginger")

#: the vanilla sprite each stage is shaded like (one per growth stage)
HABIT_REFS = {
    "rice": [f"vegetation_farming_01b_{i}" for i in range(0, 8)],             # Barley
    "banana": [f"vegetation_farming_01_{i}" for i in range(72, 80)],          # Corn
    "pineapple": [f"vegetation_farming_01_{i}" for i in (16, 17, 18, 19, 20, 22, 21, 23)],  # Cabbages
    "grape": [f"vegetation_farming_01_{i}" for i in range(104, 112)],         # Greenpeas
    "ginger": [f"vegetation_farming_01_{i}" for i in range(96, 104)],         # SweetPotato
    "bush": [f"vegetation_farming_01b_{i}" for i in range(64, 72)],           # BellPepper
}
#: vanilla ornamental trees, sized to the stage: index = 4 sizes x 4 seasons
#: (0-3 winter, 4-7 snow, 8-11 spring bloom, 12-15 summer)
TREE_REFS = ["e_cockspurhawthorn_1_12", "e_cockspurhawthorn_1_12", "e_cockspurhawthorn_1_13",
             "e_cockspurhawthorn_1_13", "e_cockspurhawthorn_1_14", "e_dogwood_1_10",
             "e_cockspurhawthorn_1_15", "e_cockspurhawthorn_1_15"]
#: the mod user's painted sheet for each tree crop (no apple sheet: the cherry is closest)
PAINTED_NAME = {"apple": "Cherry", "pear": "Pear", "peach": "Peach", "cherry": "Cherry",
                "orange": "Orange", "lemon": "Lemon", "lime": "Lime", "grapefruit": "Grapefruit",
                "avocado": "Avocado", "mango": "Mango", "olive": "Olive", "coffee": "Coffee",
                "banana": "Banana", "grape": "Grape", "pineapple": "Pineapple"}


def py() -> list[str]:
    return [sys.executable]


def arches() -> dict[str, str]:
    """crop -> archetype, read off the recipe's CROPS table (the recipe imports bpy, so
    it is parsed rather than imported)."""
    text = (ROOT / "examples" / "ff_crop.py").read_text(encoding="utf-8")
    return dict(re.findall(r'^\s+"(\w+)":\s+dict\(arch="(\w+)"', text, re.M))


def after_harvest() -> set[str]:
    """The perennials whose last column is the fruiting plant after harvest (the recipe's
    AFTER_HARVEST, parsed like arches())."""
    text = (ROOT / "examples" / "ff_crop.py").read_text(encoding="utf-8")
    m = re.search(r"^AFTER_HARVEST = \(([^)]*)\)", text, re.M)
    return set(re.findall(r'"(\w+)"', m.group(1))) if m else set()


def refs_for(crop: str) -> list[str]:
    arch = arches().get(crop)
    if arch == "tree":
        return TREE_REFS
    refs = HABIT_REFS[crop] if crop in HABIT_REFS else HABIT_REFS["bush"]
    if crop in after_harvest():
        # the last column is the fruiting plant without its fruit: it takes the fruiting
        # stage's shading, not the vanilla crop's withered last stage
        refs = refs[:7] + [refs[6]]
    return refs


def build_extra(crop: str) -> list[str]:
    if arches().get(crop) == "tree":
        # crown lit as one body; the pool of shade under the trunk, sized to the sprite
        return ["--canopy-ramp", "0.16", "--ground-shadow", "1", "--ground-shadow-shape", "ellipse",
                "--ground-shadow-width", "0.42", "--ground-shadow-alpha", "64"]
    return []


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# --------------------------------------------------------------------------- #
# pipeline steps
# --------------------------------------------------------------------------- #

def render(crop: str, layer: str = "all") -> bool:
    t0 = time.time()
    cmd = [str(BLENDER), "-b", "-P", str(ROOT / "examples" / "ff_crop.py"), "--", crop]
    if layer != "all":
        cmd += ["--layer", layer]
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, errors="replace")
    text = out.stdout + out.stderr
    ok = re.search(r"rendered 8 cell", text) is not None
    for line in text.splitlines():
        if "WARNING" in line or "Traceback" in line or "Error" in line:
            log(f"  {crop}: {line.strip()[:160]}")
    log(f"render {crop:11s} {'ok' if ok else 'FAILED'} ({time.time() - t0:.0f}s)")
    if not ok:
        print(text[-3000:])
    return ok


def build(crop: str, out: Path = DIST) -> bool:
    t0 = time.time()
    tid = TILEDEF_BASE + ORDER.index(crop)
    refs = refs_for(crop)
    cmd = py() + ["-m", "pzforge.cli", "build", str(ROOT / "build" / f"ff_{crop}_cells"),
                  "--out", str(out), "--mod-id", MOD_ID, "--mod-name", "FF Crops",
                  "--tiledef-id", str(tid), "--preset", "vegetation",
                  "--health-variants", "--despeckle", "2",
                  # growing crops are translucent billboards in vanilla: no boxes, no depth
                  "--plant-geometry"]
    if arches().get(crop) != "tree":
        # the habit's own vanilla sprites lend their form shading; trees get none: the
        # hawthorn stand-in (vanilla has no fruit tree) has leaves where our trunk is,
        # and its grafted tone field painted v22a's trunks light orange and blotched
        # (one-cell A/B: graft on vs off, everything else equal)
        cmd += ["--shade-like", ",".join(refs)]
    cmd += build_extra(crop)
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, errors="replace")
    text = res.stdout + res.stderr
    ok = res.returncode == 0 and "sheet ff_" in text
    if not ok:
        print(text[-3000:])
        log(f"build  {crop:11s} FAILED")
        return False
    pack = out / MOD_ID / "42" / "media" / "texturepacks" / f"ff_{crop}_01.pack"
    xdir = ROOT / "build" / f"x_{crop}"
    shutil.rmtree(xdir, ignore_errors=True)
    subprocess.run(py() + ["-m", "pzforge.cli", "extract", str(pack), str(xdir)], cwd=ROOT,
                   capture_output=True, text=True)
    cmp = subprocess.run(py() + ["-m", "pzforge.cli", "compare", refs[6],
                                 str(xdir / f"ff_{crop}_01_6.png")],
                         cwd=ROOT, capture_output=True, text=True, errors="replace")
    iou = " ".join(ln.strip() for ln in cmp.stdout.splitlines() if "IoU" in ln or "opaque" in ln)
    log(f"build  {crop:11s} ok ({time.time() - t0:.0f}s) {re.sub(r'\s+', ' ', iou)}")
    return True


def pipeline(crop: str, layer: str = "all") -> bool:
    return render(crop, layer) and build(crop)


# --------------------------------------------------------------------------- #
# reading sprites
# --------------------------------------------------------------------------- #

def pack_cells(crop: str, root: Path = DIST) -> dict:
    from PIL import Image
    from pzforge.packfile import TexturePack
    pack = TexturePack.read(root / MOD_ID / "42" / "media" / "texturepacks" / f"ff_{crop}_01.pack")
    cells = {}
    for page in pack.pages:
        atlas = Image.open(io.BytesIO(page.png)).convert("RGBA")
        for e in page.entries:
            m = re.search(r"_(\d+)$", e.name)
            if not m:
                continue
            c = Image.new("RGBA", (128, 256), (0, 0, 0, 0))
            c.paste(atlas.crop((e.x, e.y, e.x + e.w, e.y + e.h)), (e.ox, e.oy))
            cells[int(m.group(1))] = c
    return cells


def painted_cells(crop: str) -> list:
    from PIL import Image
    name = PAINTED_NAME.get(crop)
    if not name or not (PAINTED / f"Tree_{name}.png").exists():
        return []
    sheet = Image.open(PAINTED / f"Tree_{name}.png").convert("RGBA")
    return [sheet.crop((k * 128, 0, k * 128 + 128, 256)) for k in range(sheet.width // 128)]


def vanilla(name: str):
    from pzforge.compare import vanilla_sprite
    return vanilla_sprite(name).convert("RGBA")


# --------------------------------------------------------------------------- #
# review images
# --------------------------------------------------------------------------- #

BG = (18, 20, 24, 255)
INK = (240, 235, 190, 255)


def _panel(tiles, zoom=1, pad=6):
    """tiles: list of (label, image). Lays them out in a row, scaled by ``zoom``."""
    from PIL import Image, ImageDraw
    ims = [(lab, im.resize((im.width * zoom, im.height * zoom), Image.NEAREST)) for lab, im in tiles]
    w = sum(im.width for _, im in ims) + pad * (len(ims) + 1)
    h = max(im.height for _, im in ims) + 18
    out = Image.new("RGBA", (w, h), BG)
    d = ImageDraw.Draw(out)
    x = pad
    for lab, im in ims:
        out.alpha_composite(im, (x, 16))
        d.text((x + 2, 2), lab, fill=INK)
        x += im.width + pad
    return out


def _stack(images, pad=4):
    from PIL import Image
    w = max(im.width for im in images)
    h = sum(im.height for im in images) + pad * (len(images) - 1)
    out = Image.new("RGBA", (w, h), BG)
    y = 0
    for im in images:
        out.alpha_composite(im, (0, y))
        y += im.height + pad
    return out


def strip(crop: str, tag: str) -> list[Path]:
    """8 stages, ours over the shade reference; and the c6 pair at 3x."""
    REVIEW.mkdir(parents=True, exist_ok=True)
    cells = pack_cells(crop)
    refs = refs_for(crop)
    ours = _panel([(f"{crop} c{k}", cells[k]) for k in range(8)])
    van = _panel([(f"ref c{k} {refs[k][-8:]}", vanilla(refs[k])) for k in range(8)])
    p1 = REVIEW / f"{tag}_{crop}_stages.png"
    _stack([ours, van]).save(p1)
    win = (0, 40, 128, 256)
    p2 = REVIEW / f"{tag}_{crop}_c6.png"
    _panel([(f"{crop} c6 3x", cells[6].crop(win)), (f"{refs[6]} 3x", vanilla(refs[6]).crop(win))], zoom=3).save(p2)
    return [p1, p2]


def painted(crop: str, tag: str) -> list[Path]:
    """Ours beside the mod user's painted sheet: 8 stages, the ripe crown at 3x, the
    trunk at 6x, the fruit at 6x."""
    REVIEW.mkdir(parents=True, exist_ok=True)
    cells = pack_cells(crop)
    pc = painted_cells(crop)
    if not pc:
        raise SystemExit(f"no painted sheet for {crop}")
    p1 = REVIEW / f"{tag}_{crop}_vs_painted_stages.png"
    _stack([_panel([(f"ours c{k}", cells[k]) for k in range(8)]),
            _panel([(f"painted c{k}", pc[k]) for k in range(len(pc))])]).save(p1)
    win = (0, 20, 128, 256)
    p2 = REVIEW / f"{tag}_{crop}_vs_painted_crown.png"
    _panel([("painted ripe (c7) 3x", pc[-1].crop(win)), ("ours ripe (c6) 3x", cells[6].crop(win))], zoom=3).save(p2)
    trunk = (24, 150, 104, 252)
    fruit = (30, 50, 98, 130)
    p3 = REVIEW / f"{tag}_{crop}_vs_painted_detail.png"
    _panel([("painted trunk 6x", pc[-1].crop(trunk)), ("ours trunk 6x", cells[6].crop(trunk)),
            ("painted fruit 6x", pc[-1].crop(fruit)), ("ours fruit 6x", cells[6].crop(fruit))], zoom=5).save(p3)
    return [p1, p2, p3]


def sheet(tag: str, crops=ORDER) -> list[Path]:
    REVIEW.mkdir(parents=True, exist_ok=True)
    allc = {c: pack_cells(c) for c in crops}
    rows = []
    for i in range(0, len(crops), 6):
        rows.append(_panel([(f"{c} c6", allc[c][6]) for c in crops[i:i + 6]]))
    p1 = REVIEW / f"{tag}_c6_grid.png"
    _stack(rows).save(p1)
    p2 = REVIEW / f"{tag}_stages_all.png"
    _stack([_panel([(f"{c} c{k}", allc[c][k]) for k in range(8)]) for c in crops]).save(p2)
    return [p1, p2]


# --------------------------------------------------------------------------- #
# measurements
# --------------------------------------------------------------------------- #

def _hsv_regions(im, y_soil=150):
    px = im.load()
    out = {"foliage": [], "soil": [], "fruit": [], "trunk": []}
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            if a < 200:
                continue
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            d = h * 360
            if 50 <= d <= 170 and s > 0.15:
                out["foliage"].append((r, g, b, h, s, v))
            elif (d < 20 or d > 330) and s > 0.45:
                out["fruit"].append((r, g, b, h, s, v))
            elif 8 <= d <= 50 and s > 0.25:
                (out["soil"] if y > y_soil else out["trunk"]).append((r, g, b, h, s, v))
    return out


def _q(vals, i, qs=(0.1, 0.5, 0.9)):
    v = sorted(x[i] for x in vals)
    return [v[min(len(v) - 1, int(q * len(v)))] for q in qs] if v else [float("nan")] * len(qs)


def islands(im, max_px=6):
    px = im.load()
    w, h = im.size
    seen = bytearray(w * h)
    comps = []
    for y in range(h):
        for x in range(w):
            if seen[y * w + x] or px[x, y][3] <= 40:
                continue
            q = deque([(x, y)])
            seen[y * w + x] = 1
            n, top = 0, y
            while q:
                cx, cy = q.popleft()
                n += 1
                top = min(top, cy)
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx] and px[nx, ny][3] > 40:
                        seen[ny * w + nx] = 1
                        q.append((nx, ny))
            comps.append((n, top))
    main_top = min((t for n, t in comps if n > 100), default=0)
    tiny = [c for c in comps if c[0] <= max_px]
    return len(comps), len(tiny), sum(1 for n, t in tiny if t < main_top - 2)


def silhouette(im):
    """(crown top, crown skirt, crown width, trunk foot) in px: rows with more than three
    green pixels make the crown; the foot is the lowest fully opaque row."""
    px = im.load()
    green_rows = []
    xs = []
    foot = 0
    for y in range(im.height):
        row = []
        for x in range(im.width):
            r, g, b, a = px[x, y]
            if a >= 250:
                foot = y
            if a > 128:
                h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
                if 50 <= h * 360 <= 170 and s > 0.15:
                    row.append(x)
        if len(row) >= 6:                 # a lone tuft or twig is not the crown's edge
            green_rows.append(y)
            xs += row
    if not green_rows:
        return (0, 0, 0, foot)
    return (green_rows[0], green_rows[-1], max(xs) - min(xs) + 1, foot)


#: fruit pixels per crop (hue in degrees): what a fruit is on the painted sheet and ours
FRUIT_MASK = {
    # reds stop short of bark's hue (23-27 deg) and darkness, which they caught at first
    "apple": lambda h, s, v: (h < 18 or h > 335) and s > 0.60 and v > 0.18,
    "cherry": lambda h, s, v: (h < 18 or h > 335) and s > 0.60 and v > 0.18,
    "coffee": lambda h, s, v: (h < 18 or h > 335) and s > 0.50 and v > 0.22,
    "peach": lambda h, s, v: (h < 45 or h > 330) and s > 0.25 and v > 0.40,
    "mango": lambda h, s, v: (h < 52 or h > 340) and s > 0.50 and v > 0.40,
    "orange": lambda h, s, v: 15 <= h <= 45 and s > 0.55 and v > 0.30,
    "grapefruit": lambda h, s, v: (h < 32 or h > 340) and s > 0.55 and v > 0.35,
    "lemon": lambda h, s, v: 40 <= h <= 62 and s > 0.50 and v > 0.45,
    "pear": lambda h, s, v: 48 <= h <= 75 and s > 0.45 and v > 0.45,
    "lime": lambda h, s, v: 70 <= h <= 110 and s > 0.85 and v > 0.20,
    "olive": lambda h, s, v: 240 <= h <= 345 and s > 0.15 and v > 0.08,
}


def fruitcal(crops) -> None:
    """Fruit light calibration against the painted sheets: per crop, the p50/p95 fruit
    colour of ours (c6) and of the painted ripe cell, the linear gain on the painted
    p50's dominant channel (multiply the fruit's light levels by it), and the ink ratio
    (painted fruit px / ours: the fruit radius wants its square root)."""
    from pzforge.spec import srgb_to_linear
    for crop in crops:
        f = FRUIT_MASK.get(crop)
        pc = painted_cells(crop)
        if not f or not pc:
            print(f"  {crop:11s} (no mask or no painted sheet)")
            continue
        stats = []
        for im in (pack_cells(crop)[6], pc[-1]):
            px = im.load()
            pts = []
            for y in range(im.height):
                for x in range(im.width):
                    r, g, b, a = px[x, y]
                    if a > 200:
                        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
                        if f(h * 360, s, v):
                            pts.append((v, (r, g, b)))
            pts.sort()
            k = len(pts)
            if not k:
                stats.append(None)
                continue
            stats.append((k, pts[k // 2][1], pts[min(k - 1, int(0.95 * k))][1]))
        if None in stats:
            print(f"  {crop:11s} no fruit pixels found ({stats})")
            continue
        (ko, p50o, p95o), (kp, p50p, p95p) = stats
        ch = max(range(3), key=lambda i: p50p[i])
        gain = srgb_to_linear(p50p[ch] / 255) / max(1e-4, srgb_to_linear(p50o[ch] / 255))
        print(f"  {crop:11s} ours p50 {p50o} p95 {p95o} | painted p50 {p50p} p95 {p95p} | "
              f"light gain x{gain:.2f} | ink {ko} vs {kp} (radius x{(kp / ko) ** 0.5:.2f})")


def measure(crop: str) -> None:
    cells = pack_cells(crop)
    refs = refs_for(crop)
    ours6, ref6 = _hsv_regions(cells[6]), _hsv_regions(vanilla(refs[6]))
    print(f"== {crop} c6 against {refs[6]}")
    for k in ("foliage", "soil", "fruit", "trunk"):
        a, b = ours6[k], ref6[k]
        if not a or not b:
            print(f"  {k:8s} ours {len(a):5d} px, ref {len(b):5d} px")
            continue
        ra = [_q(b, i)[1] / max(1, _q(a, i)[1]) for i in range(3)]
        print(f"  {k:8s} ours {len(a):5d} ref {len(b):5d} | value p10/50/90 ours "
              f"{'/'.join(f'{v:.2f}' for v in _q(a, 5))} ref {'/'.join(f'{v:.2f}' for v in _q(b, 5))}"
              f" | transfer R x{ra[0]:.3f} G x{ra[1]:.3f} B x{ra[2]:.3f}")
    pc = painted_cells(crop)
    if pc and arches().get(crop) == "tree":
        p = _hsv_regions(pc[-1])
        print(f"== {crop} c6 against the painted ripe cell")
        for k in ("foliage", "fruit", "trunk"):
            a, b = ours6[k], p[k]
            if not a or not b:
                print(f"  {k:8s} ours {len(a):5d} px, painted {len(b):5d} px")
                continue
            print(f"  {k:8s} ours {len(a):5d} painted {len(b):5d} | value p10/50/90 ours "
                  f"{'/'.join(f'{v:.2f}' for v in _q(a, 5))} painted {'/'.join(f'{v:.2f}' for v in _q(b, 5))}"
                  f" | sat med ours {_q(a, 4)[1]:.2f} painted {_q(b, 4)[1]:.2f}"
                  f" | hue med ours {_q(a, 3)[1] * 360:.0f} painted {_q(b, 3)[1] * 360:.0f}")
    if arches().get(crop) == "tree":
        rows = [silhouette(cells[k]) for k in range(2, 8)]
        pr = [silhouette(pc[k]) for k in range(2, 8)] if pc else []
        print("  silhouette per stage (crown top / skirt / width px, trunk foot, visible trunk):")
        for i, k in enumerate(range(2, 8)):
            o = rows[i]
            line = f"    c{k}: ours top {o[0]:3d} skirt {o[1]:3d} w {o[2]:3d} foot {o[3]:3d} trunk {o[3] - o[1]:3d}"
            if pr:
                q = pr[i] if k < 6 else pr[min(len(pr) - 1, i + 1)]
                line += f"  | painted top {q[0]:3d} skirt {q[1]:3d} w {q[2]:3d} foot {q[3]:3d} trunk {q[3] - q[1]:3d}"
            print(line)
    for k in range(1, 8):
        n, tiny, above = islands(cells[k])
        print(f"  c{k}: islands {n:3d}, tiny {tiny:3d}, floating above the plant {above}")


# --------------------------------------------------------------------------- #

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("render", "build", "all", "strip", "painted", "sheet", "measure", "fruitcal"))
    ap.add_argument("crops", nargs="*")
    ap.add_argument("--layer", default="all", choices=("stems", "leaves", "all"))
    ap.add_argument("--tag", default="review")
    ap.add_argument("-j", "--jobs", type=int, default=1)
    args = ap.parse_args()
    crops = args.crops or (list(ORDER) if args.command in ("all", "build", "sheet") else [])
    if args.command == "trees":
        crops = [c for c in ORDER if arches().get(c) == "tree"]
    bad = [c for c in crops if c not in ORDER]
    if bad:
        raise SystemExit(f"unknown crop(s): {bad}")
    if args.command in ("render", "build", "all"):
        step = {"render": lambda c: render(c, args.layer), "build": build,
                "all": lambda c: pipeline(c, args.layer)}[args.command]
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            results = list(pool.map(step, crops))
        failed = [c for c, ok in zip(crops, results) if not ok]
        log(f"done; {len(crops) - len(failed)}/{len(crops)} ok" + (f", failed: {failed}" if failed else ""))
        return
    if args.command == "fruitcal":
        fruitcal(crops or [c for c in ORDER if arches().get(c) == "tree"])
        return
    if args.command == "sheet":
        for p in sheet(args.tag, tuple(crops)):
            print(p)
        return
    for crop in crops:
        if args.command == "strip":
            for p in strip(crop, args.tag):
                print(p)
        elif args.command == "painted":
            for p in painted(crop, args.tag):
                print(p)
        elif args.command == "measure":
            measure(crop)


if __name__ == "__main__":
    main()
