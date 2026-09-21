"""Asset harness: produce a whole tile set from one spec file, repeatably.

A tile set is never one sprite. The Home Brewing barrel rack alone is six
sheets (a four-part rack, the racked barrel twice with different tile props),
each of them a Blender render, a ``build`` with its own property list, an
``extract`` for reading the result, a composite that stacks the parts the way
the game draws them, and a measurement against the reference that the art was
copied from. Done by hand that is thirty commands whose flags drift apart
between runs; done here it is one spec and one command::

    python -m pzforge.cli assets examples/homebrewing_assets.json --install

The spec is JSON (see ``examples/homebrewing_assets.json``) with four lists:

``assets``
    one entry per sheet: the recipe Blender runs, the cells directory it
    renders into, the ``build`` arguments (mod id, tiledef id, tile props,
    style flags -- exactly what the ``build`` command takes) and the folder
    the finished sprites are extracted into for previews. A sheet's Build 42
    tile geometry (its depth; see :mod:`pzforge.geometry`) is built with it
    and merged into the mod's one ``tileGeometry.txt`` on install.
``previews``
    stacked composites: layers drawn in the game's draw order, each shifted
    up by its render y offset, for every facing side by side -- the view a
    layered object (rack, barrels, decks) can only be judged in.
``measures``
    patches on a preview (or any image) and the luminance ratios the
    reference imposes on them, reported PASS/FAIL like a test run. This is
    the "compare" step for objects that have no vanilla twin: the numbers
    come from the reference screenshot, measured the same way.
``install``
    the mod media folders the ``.pack``/``.tiles`` pairs are copied into
    (``--install``); the harness never touches them otherwise.

Relative paths resolve against the current directory, like every other
command here, so run it from the repository root.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

#: Facing index -> name, the order sprites take inside a sheet.
FACINGS = ("S", "E", "N", "W")
#: Sheets are rendered at 2x; a render y offset is quoted in 1x pixels.
PX_SCALE = 2
BACKGROUND = (40, 40, 44, 255)


# --------------------------------------------------------------------------- #
# stacked composites
# --------------------------------------------------------------------------- #

@dataclass
class Layer:
    """One sheet in a stack: ``<png>/<sheet>_<k>.png`` per facing ``k``,
    drawn shifted up by ``offset`` 1x pixels (the object's render y offset)."""

    png: Path
    sheet: str
    offset: int = 0

    def sprite(self, k: int) -> Path:
        return Path(self.png) / f"{self.sheet}_{k}.png"

    @classmethod
    def parse(cls, text: str) -> "Layer":
        """``PNGDIR/SHEET[:OFFSET]`` -- the command-line spelling."""
        offset = 0
        if ":" in text.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]:
            text, off = text.rsplit(":", 1)
            offset = int(off)
        path = Path(text)
        return cls(path.parent, path.name, offset)


def stack(layers: list[Layer], *, facings: int = 4, scale: int = 3,
          px_scale: int = PX_SCALE, labels: bool = True) -> Image.Image:
    """Compose the layers for every facing, in list order, bottom-aligned.

    The game draws a square's objects in list order and shifts each by its
    own render y offset, so a barrel on the second deck is the barrel sprite
    drawn after the deck below it and 45 px higher. Stacking the extracted
    sprites the same way is the only preview in which draw-order mistakes
    (a far post over the barrels, a deck under them) can be seen at all.
    """
    cols = []
    for k in range(facings):
        images = [(Image.open(l.sprite(k)).convert("RGBA"), l.offset) for l in layers]
        w = max(im.width for im, _ in images)
        h = max(im.height + off * px_scale for im, off in images)
        cell = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        for im, off in images:
            cell.alpha_composite(im, ((w - im.width) // 2, h - im.height - off * px_scale))
        cols.append(cell)
    pad = 12
    top = 24 if labels else 0
    width = sum(c.width * scale for c in cols) + pad * (len(cols) + 1)
    height = max(c.height * scale for c in cols) + pad * 2 + top
    canvas = Image.new("RGBA", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    x = pad
    for k, c in enumerate(cols):
        big = c.resize((c.width * scale, c.height * scale), Image.NEAREST)
        canvas.alpha_composite(big, (x, pad + top))
        if labels:
            draw.text((x, 4), f"{k} = face {FACINGS[k % 4]}", fill=(255, 255, 255, 255))
        x += big.width + pad
    return canvas


# --------------------------------------------------------------------------- #
# measurements
# --------------------------------------------------------------------------- #

def luminance(rgb: tuple[float, float, float]) -> float:
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def measure(image: Image.Image | Path | str, patches: dict[str, tuple]) -> dict[str, dict]:
    """Mean colour and luminance of each ``name: (x0, y0, x1, y1)`` patch."""
    im = image if isinstance(image, Image.Image) else Image.open(image)
    im = im.convert("RGB")
    out: dict[str, dict] = {}
    for name, box in patches.items():
        x0, y0, x1, y1 = (int(v) for v in box)
        px = list(im.crop((x0, y0, x1, y1)).getdata())
        n = max(1, len(px))
        rgb = tuple(sum(p[i] for p in px) / n for i in range(3))
        out[name] = {"rgb": rgb, "lum": luminance(rgb), "n": len(px)}
    return out


def check_expectations(measured: dict[str, dict],
                       expectations: list[dict]) -> list[tuple[str, bool, str]]:
    """Each expectation names a patch (``"patch": "head"``) or a ratio of two
    (``"ratio": "flank/head"``) and a ``min``/``max`` band on its luminance."""
    results = []
    for exp in expectations:
        if "ratio" in exp:
            a, b = exp["ratio"].split("/")
            value = measured[a]["lum"] / max(1e-6, measured[b]["lum"])
            label = f"{a}/{b}"
        else:
            label = exp["patch"]
            value = measured[label]["lum"]
        lo, hi = exp.get("min", float("-inf")), exp.get("max", float("inf"))
        ok = lo <= value <= hi
        results.append((label, ok, f"{value:.2f} in [{lo}, {hi}]"))
    return results


# --------------------------------------------------------------------------- #
# spec
# --------------------------------------------------------------------------- #

@dataclass
class Asset:
    name: str
    recipe: str
    cells: str
    mod_id: str
    build: list[str] = field(default_factory=list)
    args: list[str] = field(default_factory=list)
    sheet: str = ""
    png: str = ""
    install: bool = True
    note: str = ""

    def sheet_name(self) -> str:
        if self.sheet:
            return self.sheet
        manifest = Path(self.cells) / "manifest.json"
        if manifest.exists():
            return json.loads(manifest.read_text(encoding="utf-8")).get("sheet", "")
        return ""


@dataclass
class Spec:
    blender: str
    out: str = "dist"
    build_dir: str = "42"
    install: list[str] = field(default_factory=list)
    assets: list[Asset] = field(default_factory=list)
    previews: list[dict] = field(default_factory=list)
    measures: list[dict] = field(default_factory=list)


def load_spec(path: Path | str) -> Spec:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assets = [Asset(**{k: v for k, v in a.items() if not k.startswith("_")})
              for a in data.get("assets", [])]
    names = [a.name for a in assets]
    if len(set(names)) != len(names):
        raise ValueError("duplicate asset names in spec")
    return Spec(blender=data.get("blender", "blender"), out=data.get("out", "dist"),
                build_dir=str(data.get("build_dir", "42")),
                install=list(data.get("install", [])), assets=assets,
                previews=list(data.get("previews", [])),
                measures=list(data.get("measures", [])))


def pack_path(spec: Spec, asset: Asset) -> Path:
    return Path(spec.out) / asset.mod_id / spec.build_dir / "media" / "texturepacks" / f"{asset.sheet_name()}.pack"


def tiles_path(spec: Spec, asset: Asset) -> Path:
    return Path(spec.out) / asset.mod_id / spec.build_dir / "media" / f"{asset.sheet_name()}.tiles"


def geometry_path(spec: Spec, asset: Asset) -> Path:
    return Path(spec.out) / asset.mod_id / spec.build_dir / "media" / "tileGeometry.txt"


def depthmap_path(spec: Spec, asset: Asset) -> Path:
    return (Path(spec.out) / asset.mod_id / spec.build_dir / "media" / "depthmaps"
            / f"DEPTH_{asset.sheet_name()}.png")


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #

class Harness:
    """Runs a spec step by step and keeps a PASS/FAIL tally, test-runner style."""

    def __init__(self, spec: Spec, log: Callable[[str], None] = print):
        self.spec = spec
        self.log = log
        self.failures: list[str] = []

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        self.log(f"  {'PASS' if ok else 'FAIL'}  {label}{'  -- ' + detail if detail else ''}")
        if not ok:
            self.failures.append(label)
        return ok

    # -- steps ---------------------------------------------------------------
    def render(self, asset: Asset) -> bool:
        cmd = [self.spec.blender, "-b", "-P", asset.recipe, "--", *asset.args]
        self.log(f"  render {asset.name}: {' '.join(cmd[1:])}")
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        Path(asset.cells).mkdir(parents=True, exist_ok=True)
        (Path(asset.cells) / "render.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
        rendered = [ln for ln in proc.stdout.splitlines() if ln.startswith("rendered ")]
        detail = rendered[-1] if rendered else f"rc={proc.returncode}, see {asset.cells}/render.log"
        return self.check(f"{asset.name}.render", proc.returncode == 0 and bool(rendered), detail)

    def build(self, asset: Asset) -> bool:
        from .cli import main as cli_main

        argv = ["build", asset.cells, "--out", self.spec.out, "--mod-id", asset.mod_id,
                "--build", self.spec.build_dir, *asset.build]
        rc = cli_main(argv)
        pack = pack_path(self.spec, asset)
        return self.check(f"{asset.name}.build", rc == 0 and pack.exists(), str(pack))

    def extract(self, asset: Asset) -> bool:
        from .cli import main as cli_main

        if not asset.png:
            return True
        rc = cli_main(["extract", str(pack_path(self.spec, asset)), asset.png])
        n = len(list(Path(asset.png).glob(f"{asset.sheet_name()}_*.png")))
        return self.check(f"{asset.name}.extract", rc == 0 and n > 0, f"{n} sprite(s) in {asset.png}")

    def install(self, asset: Asset) -> bool:
        if not asset.install:
            return True
        ok = True
        for target in self.spec.install:
            media = Path(target)
            (media / "texturepacks").mkdir(parents=True, exist_ok=True)
            for src, dst in ((pack_path(self.spec, asset), media / "texturepacks"),
                             (tiles_path(self.spec, asset), media)):
                if not src.exists():
                    ok = self.check(f"{asset.name}.install", False, f"missing {src}") and ok
                    continue
                shutil.copy2(src, dst / src.name)
                self.log(f"  installed {src.name} -> {dst}")
            geo = geometry_path(self.spec, asset)
            if geo.exists():
                # one tileGeometry.txt per mod: this sheet's block replaces
                # its namesake, every other sheet's block stays
                from . import geometry as geom

                geom.merge_into(media / "tileGeometry.txt", geo.read_text(encoding="utf-8"))
                self.log(f"  merged tile geometry of {asset.sheet_name()} -> {media / 'tileGeometry.txt'}")
            depth = depthmap_path(self.spec, asset)
            if depth.exists():
                (media / "depthmaps").mkdir(parents=True, exist_ok=True)
                shutil.copy2(depth, media / "depthmaps" / depth.name)
                self.log(f"  installed {depth.name} -> {media / 'depthmaps'}")
        return ok

    def preview(self, entry: dict) -> bool:
        layers = [Layer(Path(l["png"]), l["sheet"], int(l.get("offset", 0)))
                  for l in entry["layers"]]
        missing = [str(l.sprite(0)) for l in layers if not l.sprite(0).exists()]
        if missing:
            return self.check(f"preview.{entry['name']}", False, "missing " + ", ".join(missing))
        image = stack(layers, facings=int(entry.get("facings", 4)),
                      scale=int(entry.get("scale", 3)))
        out = Path(entry["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        image.save(out)
        return self.check(f"preview.{entry['name']}", True, f"{out} {image.size}")

    def measure(self, entry: dict) -> bool:
        image = Path(entry["image"])
        if not image.exists():
            return self.check(f"measure.{entry['name']}", False, f"missing {image}")
        measured = measure(image, {k: tuple(v) for k, v in entry["patches"].items()})
        for name, m in measured.items():
            r, g, b = m["rgb"]
            self.log(f"    {name:12s} rgb=({r:5.1f},{g:5.1f},{b:5.1f}) lum={m['lum']:5.1f}")
        ok = True
        for label, good, detail in check_expectations(measured, entry.get("expect", [])):
            ok = self.check(f"measure.{entry['name']}.{label}", good, detail) and ok
        return ok

    # -- driver --------------------------------------------------------------
    def run(self, *, only: set[str] | None = None, render: bool = True, build: bool = True,
            extract: bool = True, previews: bool = True, measures: bool = True,
            install: bool = False) -> int:
        assets = [a for a in self.spec.assets if not only or a.name in only]
        if only:
            unknown = only - {a.name for a in self.spec.assets}
            if unknown:
                self.check("spec.only", False, "unknown asset(s): " + ", ".join(sorted(unknown)))
        for asset in assets:
            self.log(f"\n== {asset.name} ({asset.sheet_name() or asset.mod_id}) ==")
            if asset.note:
                self.log(f"  note: {asset.note}")
            if render and not self.render(asset):
                continue
            if build and not self.build(asset):
                continue
            if extract:
                self.extract(asset)
            if install:
                self.install(asset)
        if previews and self.spec.previews:
            self.log("\n== previews ==")
            for entry in self.spec.previews:
                self.preview(entry)
        if measures and self.spec.measures:
            self.log("\n== measures ==")
            for entry in self.spec.measures:
                self.measure(entry)
        self.log(f"\n{'ALL PASS' if not self.failures else 'FAILED: ' + ', '.join(self.failures)}")
        return len(self.failures)


def run_spec(path: Path | str, **kwargs) -> int:
    """Load a spec and run it; returns the failure count (0 = all pass)."""
    return Harness(load_spec(path)).run(**kwargs)


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin shim
    from .cli import main as cli_main

    return cli_main(["assets", *(argv if argv is not None else sys.argv[1:])])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
