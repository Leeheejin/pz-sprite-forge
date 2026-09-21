"""Compose a mock in-game scene so custom tiles can be judged against vanilla ones.

Style problems are invisible in isolation. A crate that looks fine on its own can be
obviously too saturated, too contrasty or a pixel out of alignment the moment it sits
on a vanilla floor next to vanilla furniture -- so this lays them out on the same
isometric grid the game uses and renders a single PNG.

Screen position of tile ``(i, j)`` at 2x is ``x = (i - j) * 64``, ``y = (i + j) * 32``:
one tile step moves half a diamond across and a quarter down.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass
import os
from pathlib import Path

from PIL import Image

from .packfile import TexturePack

DEFAULT_GAME_MEDIA = Path(
    os.environ.get("PZ_MEDIA")
    or r"C:\Program Files (x86)\Steam\steamapps\common\ProjectZomboid\media")

CELL_W, CELL_H = 128, 256
STEP_X, STEP_Y = CELL_W // 2, CELL_W // 4


@dataclass
class SpriteSource:
    """Random access to the sprites inside a set of packs, by sprite name."""

    index: dict[str, tuple]

    @classmethod
    def from_packs(cls, paths: list[Path]) -> "SpriteSource":
        index: dict[str, tuple] = {}
        for path in paths:
            if not path.exists():
                continue
            pack = TexturePack.read(path)
            for page in pack.pages:
                for entry in page.entries:
                    index[entry.name] = (page, entry)
        return cls(index)

    def names(self, prefix: str = "") -> list[str]:
        return sorted(n for n in self.index if n.startswith(prefix))

    def get(self, name: str) -> Image.Image | None:
        hit = self.index.get(name)
        if hit is None:
            return None
        page, e = hit
        if not hasattr(page, "_decoded"):
            page._decoded = Image.open(io.BytesIO(page.png)).convert("RGBA")
        cell = Image.new("RGBA", (e.ow, e.oh), (0, 0, 0, 0))
        cell.paste(page._decoded.crop((e.x, e.y, e.x + e.w, e.y + e.h)), (e.ox, e.oy))
        return cell


def compose(placements: list[tuple[int, int, Image.Image]], cols: int, rows: int,
            background: tuple[int, int, int, int] = (26, 28, 32, 255)) -> Image.Image:
    """Paint tiles onto an isometric grid, back to front."""
    width = (cols + rows) * STEP_X + CELL_W
    height = (cols + rows) * STEP_Y + CELL_H
    origin_x = rows * STEP_X
    canvas = Image.new("RGBA", (width, height), background)

    # Painter's order: tiles further from the camera are drawn first.
    for i, j, sprite in sorted(placements, key=lambda p: (p[0] + p[1], p[0])):
        x = origin_x + (i - j) * STEP_X
        y = (i + j) * STEP_Y
        canvas.alpha_composite(sprite, (x, y))
    return canvas


def build_scene(custom_pack: Path, cols: int = 5, rows: int = 5,
                game_media: Path = DEFAULT_GAME_MEDIA,
                floor_sprite: str = "blends_natural_01_0",
                vanilla_objects: list[str] | None = None,
                seed: int = 5) -> Image.Image:
    """Alternate custom and vanilla objects on a vanilla floor."""
    vanilla = SpriteSource.from_packs([
        game_media / "texturepacks" / "Tiles2x.floor.pack",
        game_media / "texturepacks" / "Tiles2x.pack",
    ])
    mine = SpriteSource.from_packs([custom_pack])
    if not mine.index:
        raise ValueError(f"{custom_pack} contains no sprites")

    floor = vanilla.get(floor_sprite)
    if floor is None:
        raise ValueError(f"vanilla floor sprite {floor_sprite!r} not found -- "
                         f"is the game installed at {game_media}?")

    if vanilla_objects is None:
        vanilla_objects = [n for n in vanilla.names("furniture_seating_indoor_01_")][:6]
    reference = [img for img in (vanilla.get(n) for n in vanilla_objects) if img]
    custom = [mine.get(n) for n in mine.names()]
    custom = [c for c in custom if c]

    rng = random.Random(seed)
    placements: list[tuple[int, int, Image.Image]] = []
    for i in range(cols):
        for j in range(rows):
            placements.append((i, j, floor))

    # Chequerboard the two sources so every custom tile has a vanilla neighbour.
    slot = 0
    for i in range(cols):
        for j in range(rows):
            if (i + j) % 2:
                continue
            pool = custom if (slot % 2 == 0 or not reference) else reference
            placements.append((i, j, pool[(slot // 2) % len(pool)]))
            slot += 1
    rng.shuffle(placements[:0])  # keep ordering deterministic; shuffle nothing
    return compose(placements, cols, rows)
