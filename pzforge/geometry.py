"""Build 42 tile geometry: the depth the game draws a tile with.

Build 42 no longer paints a square's objects in list order. Every tile is
drawn with per-pixel depth, and that depth comes from ``media/tileGeometry.txt``
-- boxes, cylinders, planes and polygons per tile, in 1/10000 m with x east,
y up and z south -- which a mod ships next to its texture packs. A tile with
no entry falls back to a default that, measured on the barrel rack, lets a
sprite drawn with a render y offset (a cask on an upper tier) win over the
deck above it: the cask looked as if it broke through the shelf while the
same objects, stacked in list order, looked right.

The rig exports every part's tile-local bounding box per facing into the
cells manifest (``render_cells``, grouped by ``part["pz_geometry"]``, see
``tag_geometry``); this module turns that into the game's format, and the
asset harness merges one file per sheet into the mod's single
``tileGeometry.txt``.

Depth is only sampled where the sprite has pixels, so a box round a cask is
as good as a cylinder for occlusion; what matters is that the deck above it
is a thin box at its own height and the posts are thin boxes on the tile's
corners.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Game units per metre in tileGeometry.txt.
UNITS = 10000
VERSION = 2
#: Tile properties vanilla repeats inside the geometry file.
ECHOED_PROPERTIES = ("Surface", "ItemHeight")


def _u(value: float) -> int:
    return int(round(value * UNITS))


def _box(minimum, maximum, indent: str) -> str:
    lo = "x".join(str(_u(v)) for v in minimum)
    hi = "x".join(str(_u(v)) for v in maximum)
    return (f"{indent}box\n{indent}{{\n"
            f"{indent}    translate = 0x0x0,\n{indent}    rotate = 0x0x0,\n"
            f"{indent}    min = {lo},\n{indent}    max = {hi},\n{indent}}}\n")


def tileset_text(sheet_name: str, tiles: list[dict], cols: int = 8) -> str:
    """One ``tileset { ... }`` block.

    ``tiles`` is a list of ``{"index": n, "boxes": [{"min": [x, y, z],
    "max": [x, y, z]}, ...], "properties": {...}}`` with coordinates in metres
    in the game's frame (x east, y up, z south). Sprite ``n`` sits at grid
    ``xy = (n % cols) x (n // cols)`` in the sheet.
    """
    out = [f"    tileset\n    {{\n        name = {sheet_name},\n"]
    for tile in sorted(tiles, key=lambda t: t["index"]):
        n = tile["index"]
        out.append(f"\n        /* {sheet_name}_{n} */\n        tile\n        {{\n"
                   f"            xy = {n % cols}x{n // cols},\n")
        for box in tile.get("boxes", []):
            out.append("\n" + _box(box["min"], box["max"], "            "))
        props = {k: v for k, v in (tile.get("properties") or {}).items()
                 if k in ECHOED_PROPERTIES and str(v) != ""}
        if props:
            out.append("\n            properties\n            {\n")
            for key, value in props.items():
                out.append(f"                {key} = {value},\n")
            out.append("            }\n")
        out.append("        }\n")
    out.append("    }\n")
    return "".join(out)


def file_text(tileset_blocks: list[str]) -> str:
    body = "\n".join(tileset_blocks)
    return f"tileGeometry\n{{\n    VERSION = {VERSION},\n\n{body}}}\n"


# --------------------------------------------------------------------------- #
# merging: one file per mod, one block per sheet
# --------------------------------------------------------------------------- #

_NAME = re.compile(r"name\s*=\s*([A-Za-z0-9_]+)\s*,")


def split_tilesets(text: str) -> dict[str, str]:
    """Return ``{tileset name: block text}`` for every tileset in a file."""
    blocks: dict[str, str] = {}
    pos = 0
    while True:
        start = text.find("tileset", pos)
        if start < 0:
            break
        # must be the keyword, not part of "tileGeometry"/"tilesets"
        if text[start - 1: start].isalnum() or text[start + 7: start + 8].isalnum():
            pos = start + 7
            continue
        brace = text.find("{", start)
        if brace < 0:
            break
        depth, i = 0, brace
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        block = text[start:i + 1]
        m = _NAME.search(block)
        if m:
            # normalise to the writer's indentation so files stay uniform
            blocks[m.group(1)] = "    " + block.strip() + "\n"
        pos = i + 1
    return blocks


def merge(existing: str | None, incoming: str) -> str:
    """Replace or add ``incoming``'s tilesets in ``existing`` (a whole file)."""
    blocks = split_tilesets(existing) if existing else {}
    blocks.update(split_tilesets(incoming))
    return file_text([_reindent(b) for b in blocks.values()])


def _reindent(block: str) -> str:
    """Re-indent a block to 4 spaces per level (blocks from split are raw)."""
    lines = block.strip().splitlines()
    out, depth = [], 1
    for raw in lines:
        line = raw.strip()
        if line.startswith("}"):
            depth -= 1
        out.append("    " * depth + line)
        if line.endswith("{"):
            depth += 1
        elif line == "{":
            depth += 1
    return "\n".join(out) + "\n"


def merge_into(path: Path, incoming_text: str) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    path.write_text(merge(existing, incoming_text), encoding="utf-8")


# --------------------------------------------------------------------------- #
# from the rig's manifest
# --------------------------------------------------------------------------- #

def tiles_from_manifest(manifest: dict, sheet_cells, tile_props: dict | None = None) -> list[dict]:
    """Pair the manifest's per-cell geometry with the sheet's sprite indices.

    ``sheet_cells`` are the packed :class:`pzforge.sheet.Cell` objects (their
    ``source`` is the manifest record's file name and ``index`` the sprite
    number). Cells without geometry are skipped.
    """
    by_file = {rec["file"]: rec for rec in manifest.get("cells", [])}
    tiles = []
    for cell in sheet_cells:
        rec = by_file.get(cell.source)
        if not rec or not rec.get("geometry"):
            continue
        tiles.append({"index": cell.index,
                      "boxes": [{"min": g["min"], "max": g["max"]} for g in rec["geometry"]],
                      "properties": dict(tile_props or {})})
    return tiles
