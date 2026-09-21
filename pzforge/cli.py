"""Command line front end: turn rendered cells into an installable tile mod.

    pzforge build <cells-dir> --mod-id MyTiles --preset furniture
    pzforge inspect <file.pack|file.tiles>
    pzforge extract <file.pack> <out-dir>
    pzforge ids
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from PIL import Image, ImageChops

from . import modgen, sheet as sheetmod, style as stylemod
from .packfile import TexturePack
from .sheet import build_sheet, load_cells, pack_sheet
from .tiledef import TileDefinitions, Tile, Tileset

PRESETS_PATH = Path(__file__).resolve().parents[1] / "reference" / "tile_presets.json"


def load_presets() -> dict:
    if PRESETS_PATH.exists():
        return json.loads(PRESETS_PATH.read_text())
    return {}


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #

def _mask_alpha(img: Image.Image, mask: Image.Image) -> Image.Image:
    """Clip an image's alpha to a keep-mask (255 = keep), colours untouched."""
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    return Image.merge("RGBA", (r, g, b, ImageChops.darker(a, mask)))


def cmd_build(args: argparse.Namespace) -> int:
    cells_dir = Path(args.cells)
    manifest_path = cells_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"error: no manifest.json in {cells_dir} -- render from Blender first",
              file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text())

    sheet_name = args.sheet or manifest["sheet"]
    cell_size = tuple(manifest["cell"])
    cells = load_cells(cells_dir, manifest)
    print(f"loaded {len(cells)} cell(s) at {cell_size[0]}x{cell_size[1]} "
          f"({manifest.get('scale', '?')})")

    # --- tile cut ---------------------------------------------------------
    # Multi-tile renders carry a tile pass; clip every cell to the pixels
    # standing on its own footprint tile before anything else sees them.
    tile_files = {c["file"]: c.get("tile") for c in manifest["cells"]}
    tile_masks: dict[str, Image.Image] = {}
    if any(tile_files.values()):
        from . import finish as finishmod
        cut_groups: dict[str, list] = {}
        for cell in cells:
            cut_groups.setdefault(cell.facing, []).append(cell)
        step_x, step_y = cell_size[0] // 2, cell_size[0] // 4
        repaired = 0
        for cut_group in cut_groups.values():
            cut_palette = {
                f"{c.x},{c.y}": list(finishmod.tile_pass_color(c.x, c.y))
                for c in cut_group}
            before_cut = {}
            for cell in cut_group:
                tile_name = tile_files.get(cell.source)
                if not tile_name or not (cells_dir / tile_name).exists():
                    continue
                mask = finishmod.tile_keep_mask(
                    Image.open(cells_dir / tile_name),
                    f"{cell.x},{cell.y}", cut_palette)
                tile_masks[cell.source] = mask
                before_cut[cell.source] = cell.image.convert("RGBA")
                cell.image = _mask_alpha(cell.image, mask)
            # Seam repair: at the cut plane, an antialiased pixel can decode to
            # a different tile in each cell's own (noisy) tile pass and get
            # dropped from BOTH -- a pinhole showing the floor through the
            # object. Any canvas position that was opaque before the cut but
            # is opaque in no cell afterwards is restored into the front-most
            # cell that had it.
            if len(before_cut) > 1:
                raw = [((c.x - c.y) * step_x, (c.x + c.y) * step_y)
                       for c in cut_group]
                minx = min(o[0] for o in raw)
                miny = min(o[1] for o in raw)
                offs = {c.source: (ox - minx, oy - miny)
                        for c, (ox, oy) in zip(cut_group, raw)}
                order = sorted((c for c in cut_group if c.source in before_cut),
                               key=lambda c: (c.x + c.y, c.x), reverse=True)
                w, h = cell_size
                canvas_w = max(o[0] for o in offs.values()) + w
                canvas_h = max(o[1] for o in offs.values()) + h
                pre = [[0] * canvas_w for _ in range(canvas_h)]
                post = [[0] * canvas_w for _ in range(canvas_h)]
                loaded = {c.source: (before_cut[c.source].load(),
                                     c.image.load()) for c in order}
                for cell in order:
                    ox, oy = offs[cell.source]
                    bpx, apx = loaded[cell.source]
                    for y in range(h):
                        for x in range(w):
                            if bpx[x, y][3] >= 128:
                                pre[y + oy][x + ox] = 1
                            if apx[x, y][3] >= 128:
                                post[y + oy][x + ox] = 1
                for cy in range(canvas_h):
                    for cx in range(canvas_w):
                        if not pre[cy][cx] or post[cy][cx]:
                            continue
                        for cell in order:
                            ox, oy = offs[cell.source]
                            if not (0 <= cx - ox < w and 0 <= cy - oy < h):
                                continue
                            bpx, apx = loaded[cell.source]
                            p = bpx[cx - ox, cy - oy]
                            if p[3] >= 128:
                                apx[cx - ox, cy - oy] = p
                                tile_masks[cell.source].putpixel(
                                    (cx - ox, cy - oy), 255)
                                repaired += 1
                                break
        if tile_masks:
            note = f", {repaired} seam pinhole(s) repaired" if repaired else ""
            print(f"tile cut: {len(tile_masks)} cell(s) clipped to their own "
                  f"tile{note}")

    # --- style pass -------------------------------------------------------
    toon = bool(manifest.get("toon"))
    if toon:
        print("toon-ramped cells: light is already stepped in the render -- "
              "skipping relight and tone blocking")
    shade_reference = None
    shade_refs_per_cell = None
    if args.shade_like:
        from . import compare as cmp
        names = [n.strip() for n in args.shade_like.split(",") if n.strip()]
        if len(names) > 1:
            # Multi-tile: one reference per cell, in manifest cell order. Grafting
            # every cell with the same half gives each tile a field belonging to
            # a different part of the object -- same-facing surfaces come out at
            # different levels and the light stops reading as one source.
            shade_refs_per_cell = [cmp.vanilla_sprite(n) for n in names]
            shade_reference = shade_refs_per_cell[0]
            print(f"form shading grafted per cell from {', '.join(names)}")
        else:
            shade_reference = cmp.vanilla_sprite(names[0])
            print(f"form shading grafted from {names[0]}")
    options = stylemod.StyleOptions(
        match_strength=args.style_strength,
        alpha_floor=args.alpha_floor,
        bleed_passes=args.bleed,
        grounding_strength=0.0 if args.floor else args.grounding,
        hard_alpha=args.floor,
        # Toon cells arrive already painted -- flat fills, stepped light -- and the
        # paintify flatten erases the soft per-element form the graft adds (a lid
        # gradient sits below the flatten threshold, so it gets planed right off).
        paint_levels=args.paint_levels if args.paint_levels is not None
        else (16 if args.floor else (0 if toon else 48)),
        paint_passes=args.paint_passes,
        paint_threshold=args.paint_threshold,
        paint_sharpen=args.paint_sharpen,
        grain_strength=args.grain_strength,
        grain_coverage=args.grain_coverage,
        stroke_amplitude=args.stroke_amplitude,
        stroke_coverage=args.stroke_coverage,
        stroke_length=args.stroke_length,
        shade_reference=shade_reference,
        shade_strength=args.shade_strength,
        relief_strength=args.relief_strength,
        finish_strength=args.finish_strength,
        edge_turn_strength=args.edge_turn,
        contour_strength=args.contour,
        contour_top=args.contour_top,
        shadow_strength=args.ground_shadow,
        shadow_desat_strength=args.shadow_desat,
        block_strength=0.0 if toon else args.block_strength,
        paint_flatten=args.paint_flatten,
        light_steps=args.light_steps,
        canopy_ramp=args.canopy_ramp,
        rim_ratio=args.rim,
        enabled=not args.no_style,
    )
    families = manifest.get("families") or {}
    if options.enabled:
        print(f"style pass: match={options.match_strength} "
              f"alpha_floor={options.alpha_floor} bleed={options.bleed_passes}")
        profile = stylemod.load_profile()
        normal_files = {c["file"]: c.get("normal") for c in manifest["cells"]}
        element_files = {c["file"]: c.get("element") for c in manifest["cells"]}
        light_files = {c["file"]: c.get("light") for c in manifest["cells"]}
        palette = manifest.get("elements") or {}
        view = tuple(manifest.get("view") or ()) or None
        retouch_entries = []
        # Style the composed OBJECT, then cut it back into cells. Styling cells
        # independently lets every statistics-driven pass (tone matching, element
        # budgets) see a different slice of the object, treats the cut line as a
        # silhouette, and restarts strokes/grain at the seam -- same-facing
        # surfaces come out at different levels and the seam shows. Vanilla
        # artists paint the whole object, then cut it into tiles.
        step_x, step_y = cell_size[0] // 2, cell_size[0] // 4
        # Isolated-tile sets (wall pieces) are independent sprites the game
        # overlays in painter order -- composing them onto one canvas would
        # paste a southern piece OVER the one behind it, so each cell styles
        # alone. True multi-tile objects compose per facing as usual.
        isolated = bool(manifest.get("isolate_tiles"))
        by_facing: dict[tuple, list[int]] = {}
        for i, cell in enumerate(cells):
            by_facing.setdefault((cell.facing, i if isolated else None),
                                 []).append(i)
        for (facing, _), group in by_facing.items():
            raw = [((cells[i].x - cells[i].y) * step_x,
                    (cells[i].x + cells[i].y) * step_y) for i in group]
            minx = min(o[0] for o in raw)
            miny = min(o[1] for o in raw)
            offs = {i: (ox - minx, oy - miny) for i, (ox, oy) in zip(group, raw)}
            canvas_w = max(o[0] for o in offs.values()) + cell_size[0]
            canvas_h = max(o[1] for o in offs.values()) + cell_size[1]
            order = sorted(group, key=lambda i: (cells[i].x + cells[i].y,
                                                 cells[i].x))

            def composed(images: dict) -> Image.Image | None:
                if any(images.get(i) is None for i in group):
                    return None
                canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
                for i in order:
                    canvas.alpha_composite(images[i].convert("RGBA"), offs[i])
                return canvas

            normals, elements, lights = {}, {}, {}
            for i in group:
                mask = tile_masks.get(cells[i].source)

                def aux(name):
                    if not name or not (cells_dir / name).exists():
                        return None
                    img = Image.open(cells_dir / name)
                    return _mask_alpha(img, mask) if mask is not None else img

                normals[i] = aux(normal_files.get(cells[i].source))
                elements[i] = (aux(element_files.get(cells[i].source))
                               if palette else None)
                lights[i] = aux(light_files.get(cells[i].source))
            canvas = composed({i: cells[i].image for i in group})
            normal_canvas = composed(normals)
            element_canvas = composed(elements)
            light_canvas = None if toon else composed(lights)
            top_mask = (stylemod.upward_mask(normal_canvas)
                        if normal_canvas is not None else None)
            labels = None
            if element_canvas is not None:
                from . import finish as finishmod
                labels = finishmod.decode_labels(element_canvas, palette)
            if shade_refs_per_cell is not None \
                    and len(shade_refs_per_cell) == len(cells):
                options.shade_reference = composed(
                    {i: shade_refs_per_cell[i] for i in group})
            elif shade_reference is not None and len(by_facing) > 1:
                # A reference sprite is one facing's art (the measured ones are
                # all S). Grafting its tone field onto a turned facing fights
                # the facing's own light -- so a single reference dresses the
                # S group only and the other facings keep the pure ramp.
                options.shade_reference = (shade_reference if facing == "S"
                                           else None)
            # The floor-shadow diamond is per-tile geometry -- drawn per cell
            # after the split, exactly as vanilla carries one per sprite.
            shadow_strength = options.shadow_strength
            options.shadow_strength = 0.0
            styled = stylemod.apply(canvas, options, top_mask=top_mask,
                                    element_labels=labels, light=light_canvas,
                                    normal=normal_canvas, view=view,
                                    families=families)
            options.shadow_strength = shadow_strength
            styled_px = styled.load()
            for i in group:
                cell = cells[i]
                ox, oy = offs[i]
                before = stylemod.measure(cell.image)
                raw_image = cell.image
                src = cell.image.convert("RGBA")
                src_px = src.load()
                out = Image.new("RGBA", src.size, (0, 0, 0, 0))
                out_px = out.load()
                for y in range(src.size[1]):
                    for x in range(src.size[0]):
                        a = src_px[x, y][3]
                        if a < options.alpha_floor:
                            continue
                        if options.hard_alpha:
                            if a < 128:
                                continue
                            a = 255
                        r, g, b, _ = styled_px[x + ox, y + oy]
                        out_px[x, y] = (r, g, b, a)
                if shadow_strength > 0:
                    out = stylemod.ground_shadow(out, shadow_strength)
                cell.image = out
                if args.retouch_out:
                    retouch_entries.append({
                        "name": cell.source,
                        "styled": cell.image,
                        "beauty": raw_image,
                        "light": lights[i],
                        "normal": normals[i],
                        "elements": elements[i],
                        "vanilla": shade_reference,
                    })
                after = stylemod.measure(cell.image)
                if before and after:
                    print(f"   {cell.source or cell.facing}: "
                          f"value {before['median_value']:.2f}->"
                          f"{after['median_value']:.2f} "
                          f"spread {before['value_spread']:.2f}->"
                          f"{after['value_spread']:.2f} "
                          f"sat {before['median_saturation']:.2f}->"
                          f"{after['median_saturation']:.2f}")
        for key in ("median_value", "value_spread", "median_saturation"):
            print(f"   vanilla {key} band: "
                  f"{profile[key][stylemod.BAND[0]]:.2f}-{profile[key][stylemod.BAND[1]]:.2f}")
        if args.retouch_out:
            from . import retouch
            retouch.write_export(Path(args.retouch_out), cells_dir, manifest,
                                 retouch_entries)
            print(f"retouch folder: {args.retouch_out}  (edit the top-level "
                  f"PNGs, keep alpha, then rebuild with --no-style)")
    else:
        print("style pass: skipped")

    if args.health_variants:
        # Vanilla ships unhealthy/dying/dead as hue-keyed transforms of the healthy
        # art (see pzforge.variants). Rows are stacked below the healthy grid, so a
        # crop's sprites index as healthy 0..n-1, unhealthy n..2n-1, and so on.
        from . import variants as varmod
        from .sheet import Cell
        lut = varmod.load()
        rows = max(cl.y for cl in cells) + 1
        extra = []
        for k, variant in enumerate(varmod.VARIANTS, start=1):
            for cell in cells:
                dup = Cell(varmod.derive(cell.image, variant, lut), cell.facing,
                           cell.x, cell.y + rows * k,
                           source=f"{variant}:{cell.source}")
                dup.facing_order = cell.facing_order
                extra.append(dup)
        cells = cells + extra
        print(f"health variants: {len(extra)} sprite(s) derived "
              f"({', '.join(varmod.VARIANTS)})")
    sheet = build_sheet(sheet_name, cells, cell_size, cols=args.columns)
    print(f"sheet {sheet_name}: {sheet.cols}x{sheet.rows} grid, "
          f"{len(sheet.cells)} sprite(s)")

    # --- properties -------------------------------------------------------
    presets = load_presets()
    props: dict[str, str] = {}
    if args.preset:
        entry = presets.get(args.preset)
        if entry is None:
            print(f"error: unknown preset {args.preset!r}; have "
                  f"{', '.join(sorted(presets)) or '(none extracted)'}", file=sys.stderr)
            return 2
        props.update(entry["core"])
        if not entry["core"]:
            print(f"warning: preset {args.preset!r} has no core properties "
                  f"(vanilla is too varied there) -- set them with --prop",
                  file=sys.stderr)
    for pair in args.prop or []:
        key, _, value = pair.partition("=")
        props[key.strip()] = value.strip()

    multi_tile = manifest.get("footprint", [1, 1]) != [1, 1]
    # A preset "sequence" assigns per-sprite properties cyclically by sprite
    # index -- how wall sets work (WallW, WallN, WallNW, WallSE are four
    # independent single-tile sprites, not one multi-tile object), so grid
    # positions are not written for sequence presets. "{sprite:N}" inside a
    # value resolves to the sheet's Nth sprite name (vanilla corner pieces
    # reference their straight walls that way).
    sequence = (presets.get(args.preset, {}).get("sequence")
                if args.preset else None)
    tiles = []
    for index, cell in enumerate(sheet.cells):
        tile_props = dict(props)
        if sequence:
            step = dict(sequence[index % len(sequence)])
            for key, value in step.items():
                if "{sprite:" in str(value):
                    for n in range(len(sheet.cells)):
                        value = value.replace("{sprite:%d}" % n,
                                              sheet.sprite_name(n))
                tile_props[key] = value
        if len(manifest.get("facings", [])) > 1:
            tile_props["Facing"] = cell.facing
        if multi_tile and not sequence:
            tile_props["SpriteGridPos"] = f"{cell.x},{cell.y}"
        tiles.append(Tile(tile_props))
    tiles += [Tile() for _ in range(sheet.cols * sheet.rows - len(tiles))]

    tileset = Tileset(sheet_name, f"{sheet_name}.png", sheet.cols, sheet.rows,
                      args.tileset_id, tiles)
    tdefs = TileDefinitions([tileset])

    # --- pack -------------------------------------------------------------
    pack = pack_sheet(sheet, page_size=args.page_size)
    print(f"pack: {len(pack.pages)} page(s) of {args.page_size}px, "
          f"{sum(len(p.entries) for p in pack.pages)} trimmed sprite(s)")

    # --- write the mod ----------------------------------------------------
    tiledef_id = args.tiledef_id
    if tiledef_id is None:
        tiledef_id = modgen.free_tiledef_id()
        print(f"tiledef id: {tiledef_id} (first free id across installed mods)")
    else:
        clashes = modgen.used_tiledef_ids().get(tiledef_id)
        if clashes:
            print(f"warning: tiledef id {tiledef_id} is already used by "
                  f"{', '.join(clashes[:3])}", file=sys.stderr)

    layout = modgen.ModLayout.create(Path(args.out), args.mod_id,
                                     None if args.b41 else args.build)
    pack.write(layout.texturepacks / f"{sheet_name}.pack")
    tdefs.write(layout.media / f"{sheet_name}.tiles")
    (layout.media / f"{sheet_name}.tiles.txt").write_text(tdefs.to_text(), encoding="utf-8")
    sheet.image().save(layout.media / f"{sheet_name}.png")

    info = modgen.ModInfo(
        id=args.mod_id,
        name=args.mod_name or args.mod_id,
        description=args.description,
        author=args.author,
        tiledef=sheet_name,
        tiledef_id=tiledef_id,
        pack=sheet_name,
    )
    (layout.root / "mod.info").write_text(info.render(), encoding="utf-8")

    print(f"\nwrote mod to {layout.root}")
    for path in sorted(layout.root.rglob("*")):
        if path.is_file():
            print(f"   {path.relative_to(layout.root)}  "
                  f"({path.stat().st_size / 1024:.1f} KB)")
    print(f"\ncopy that folder into {Path.home() / 'Zomboid' / 'mods'} and enable "
          f"'{info.name}' in the mod menu")
    return 0


# --------------------------------------------------------------------------- #
# inspect / extract / ids
# --------------------------------------------------------------------------- #

def cmd_inspect(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if path.suffix == ".pack":
        pack = TexturePack.read(path)
        total = sum(len(p.entries) for p in pack.pages)
        print(f"{path.name}: {len(pack.pages)} page(s), {total} sprite(s), "
              f"{'PZPK' if pack.has_header else 'legacy headerless'}")
        for page in pack.pages[:args.limit]:
            img = Image.open(io.BytesIO(page.png))
            print(f"  page {page.name!r}  {img.size[0]}x{img.size[1]}  "
                  f"{len(page.entries)} sprite(s)")
            for e in page.entries[:5]:
                print(f"     {e.name:<38} {e.w:>4}x{e.h:<4} at ({e.x},{e.y}) "
                      f"offset ({e.ox},{e.oy}) in {e.ow}x{e.oh}")
    else:
        tdefs = TileDefinitions.read(path)
        print(f"{path.name}: {len(tdefs.tilesets)} tileset(s)")
        for ts in tdefs.tilesets[:args.limit]:
            filled = sum(1 for t in ts.tiles if not t.empty)
            print(f"  {ts.name:<34} {ts.cols}x{ts.rows} id={ts.id} "
                  f"image={ts.image} ({filled} defined)")
            for i, tile in enumerate(ts.tiles):
                if not tile.empty:
                    print(f"     {ts.name}_{i}: " +
                          ", ".join(f"{k}={v}" if v else k
                                    for k, v in tile.props.items()))
                    break
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    pack = TexturePack.read(args.file)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for page in pack.pages:
        atlas = Image.open(io.BytesIO(page.png)).convert("RGBA")
        if args.pages:
            atlas.save(out / f"{page.name}.png")
        for e in page.entries:
            cell = Image.new("RGBA", (e.ow, e.oh), (0, 0, 0, 0))
            cell.paste(atlas.crop((e.x, e.y, e.x + e.w, e.y + e.h)), (e.ox, e.oy))
            cell.save(out / f"{e.name}.png")
            n += 1
    print(f"extracted {n} sprite(s) to {out}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from .check import render_report, score

    targets: list[Path] = []
    for raw in args.paths:
        path = Path(raw)
        if path.is_dir():
            targets += sorted(p for p in path.glob("*.png") if not p.name.startswith("_"))
        else:
            targets.append(path)
    if not targets:
        print("error: nothing to check", file=sys.stderr)
        return 2

    profile = stylemod.load_profile()
    print(f"scored against {profile.get('sprites_sampled', '?')} vanilla sprites "
          f"(p10-p90 is the band worth staying inside)\n")
    outside = 0
    for path in targets:
        scores = score(Image.open(path).convert("RGBA"), profile)
        if not scores:
            print(f"{path.name}: too few opaque pixels to score")
            continue
        print(render_report(path.name, scores))
        outside += sum(1 for s in scores if not s.inside)
    print(f"\n{outside} statistic(s) outside the vanilla band")
    return 0


def cmd_spec(args: argparse.Namespace) -> int:
    from . import spec as specmod

    try:
        spec = specmod.load_spec()
    except FileNotFoundError as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2

    if args.sprite:
        try:
            matches = [specmod.sprite_entry(args.query)]
        except (FileNotFoundError, KeyError) as ex:
            print(f"error: {ex}", file=sys.stderr)
            return 2
    else:
        matches = specmod.lookup(spec, args.query)
    if not matches:
        print(f"nothing in the spec matches {args.query!r}", file=sys.stderr)
        return 2

    source = spec.get("source", {})
    print(f"derived from {source.get('sprites', '?')} vanilla sprites\n")
    for entry in matches[:args.limit]:
        d = entry.data
        print(f"== {entry.key}  ({entry.scope}, {d['sprites']} sprites) ==")
        size = entry.size_summary()
        if size:
            print(f"   size      {size}")
        for key in ("median_value", "value_spread", "median_saturation"):
            b = d[key]
            print(f"   {key:<17} p10 {b['p10']:.3f}   p50 {b['p50']:.3f}   "
                  f"p90 {b['p90']:.3f}")
        if "hue" in d:
            b = d["hue"]
            print(f"   {'hue (deg)':<17} p10 {b['p10']:.0f}     p50 {b['p50']:.0f}     "
                  f"p90 {b['p90']:.0f}")
        # A palette entry is a rendered colour, so the same paint appears several
        # times over -- once per face it lands on. Treating the whole spread as a
        # range of albedos double-counts the lighting and comes out far too dark, so
        # the paint colour is taken from the brightest common shade and the rest are
        # shown as what that paint looks like elsewhere.
        paint = specmod.paint_colour(d["palette"], args.face)
        if paint:
            rgb, albedo = paint
            print(f"   paint colour     from {rgb} (the brightest shade holding "
                  f">={specmod.PAINT_MIN_SHARE * 100:.0f}% of the sprite)")
            print(f"   base colour      ({albedo[0]:.3f}, {albedo[1]:.3f}, "
                  f"{albedo[2]:.3f})   for a surface facing {args.face}")
            print(f"   which renders as {specmod.rendered_from_albedo(albedo, 'S')} on S, "
                  f"{specmod.rendered_from_albedo(albedo, 'E')} on E, "
                  f"{specmod.rendered_from_albedo(albedo, 'top')} on top")
        print(f"   palette          {'colour':<10}{'share':>7}")
        for colour in d["palette"][:args.colours]:
            print(f"      {colour['hex']}  rgb{str(tuple(colour['rgb'])):<16}"
                  f"{colour['share'] * 100:5.1f}%")
        print()
    if len(matches) > args.limit:
        print(f"...and {len(matches) - args.limit} more match(es)")
    return 0


def cmd_refsheet(args: argparse.Namespace) -> int:
    from . import compare as cmp
    from . import refsheet

    if args.target.endswith(".png"):
        sprite = Image.open(args.target).convert("RGBA")
        name = Path(args.target).stem
    else:
        media = Path(args.game_media) if args.game_media else cmp.DEFAULT_GAME_MEDIA
        sprite = cmp.vanilla_sprite(args.target, media)
        name = args.target

    box = sprite.getbbox()
    if box:
        pad = 3
        sprite = sprite.crop((max(0, box[0] - pad), max(0, box[1] - pad),
                              min(sprite.width, box[2] + pad),
                              min(sprite.height, box[3] + pad)))

    regions = refsheet.segment(sprite, colours=args.colours)
    edges = refsheet.edge_mask(sprite)
    opaque = sum(1 for p in sprite.getdata() if p[3] > 200)
    print(f"== {name}  ({opaque}px opaque, {len(regions)} region(s)) ==\n")
    print(refsheet.report(regions, edges, opaque))

    out = Path(args.out or f"build/refsheet_{name}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    refsheet.build_sheet(sprite, regions, edges, scale=args.scale).save(out)
    print(f"\nwrote {out}   (sprite | drawn lines | paint regions)")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    from . import compare as cmp

    media = Path(args.game_media) if args.game_media else cmp.DEFAULT_GAME_MEDIA
    try:
        vanilla = cmp.vanilla_sprite(args.vanilla, media)
    except ValueError as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    mine = Image.open(args.mine).convert("RGBA")
    if mine.size != vanilla.size:
        print(f"error: size mismatch, vanilla {vanilla.size} vs mine {mine.size}",
              file=sys.stderr)
        return 2

    print(cmp.report(args.vanilla, vanilla, mine))
    crop = None
    if args.crop:
        box = vanilla.getbbox()
        other = mine.getbbox()
        crop = (min(box[0], other[0]) - 4, min(box[1], other[1]) - 4,
                max(box[2], other[2]) + 4, max(box[3], other[3]) + 4)
    strip = cmp.contact_strip(vanilla, mine, scale=args.scale, crop=crop)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    strip.save(out)
    print(f"\nwrote {out}   (vanilla | mine | red = vanilla only, blue = mine only)")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    from .preview import DEFAULT_GAME_MEDIA, build_scene

    scene = build_scene(
        Path(args.pack),
        cols=args.cols,
        rows=args.rows,
        game_media=Path(args.game_media) if args.game_media else DEFAULT_GAME_MEDIA,
        floor_sprite=args.floor,
        vanilla_objects=args.vanilla or None,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    scene.save(out)
    print(f"wrote {out}  ({scene.width}x{scene.height})")
    print("custom and vanilla tiles alternate on the grid -- if yours pop out as "
          "brighter, more saturated or misaligned, that is the style gap to close")
    return 0


def cmd_ids(args: argparse.Namespace) -> int:
    taken = modgen.used_tiledef_ids()
    print(f"{len(taken)} tiledef id(s) claimed by installed mods")
    for tid in sorted(taken)[:args.limit]:
        print(f"   {tid:<7} {', '.join(sorted(set(taken[tid]))[:4])}")
    print(f"\nfirst free id at or above {modgen.TILEDEF_ID_FLOOR}: "
          f"{modgen.free_tiledef_id()}")
    return 0


# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pzforge",
        description="Package Blender-rendered cells into a Project Zomboid tile mod")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="build a mod from a rendered cells directory")
    b.add_argument("cells", help="directory containing manifest.json and the cell PNGs")
    b.add_argument("--out", default="dist", help="where to write the mod folder")
    b.add_argument("--mod-id", required=True)
    b.add_argument("--mod-name", default="")
    b.add_argument("--description", default="")
    b.add_argument("--author", default="")
    b.add_argument("--sheet", default="", help="override the tilesheet name")
    b.add_argument("--preset", help="tile property preset (see reference/tile_presets.json)")
    b.add_argument("--prop", action="append", metavar="KEY=VALUE",
                   help="add or override a tile property; repeatable")
    b.add_argument("--columns", type=int, default=sheetmod.DEFAULT_COLUMNS)
    b.add_argument("--page-size", type=int, default=sheetmod.DEFAULT_PAGE_SIZE)
    b.add_argument("--tileset-id", type=int, default=1)
    b.add_argument("--tiledef-id", type=int, default=None,
                   help="global tiledef id; defaults to the first free one")
    b.add_argument("--build", default="42", help="B42 version subfolder name")
    b.add_argument("--b41", action="store_true", help="use the flat B41 mod layout")
    b.add_argument("--style-strength", type=float, default=0.6,
                   help="0 = leave tones alone, 1 = fully match vanilla percentiles")
    b.add_argument("--alpha-floor", type=int, default=8)
    b.add_argument("--bleed", type=int, default=4)
    b.add_argument("--grounding", type=float, default=1.0,
                   help="contact shading at the tile floor; 0 disables it")
    b.add_argument("--paint-levels", type=int, default=None,
                   help="tone steps for the painting pass; 0 disables it "
                        "(default 48, floors 16 -- vanilla's measured medians)")
    b.add_argument("--shade-like", default="",
                   help="vanilla sprite whose large-scale shading field to graft on;"
                        " this is what makes the 2D output read as 3D")
    b.add_argument("--shade-strength", type=float, default=1.0)
    b.add_argument("--relief-strength", type=float, default=1.0,
                   help="bright accents beside drawn lines, in the directions "
                        "measured from the --shade-like reference; 0 disables")
    b.add_argument("--finish-strength", type=float, default=1.0,
                   help="per-element finishing (lit tops, shaded undersides), "
                        "using the rig's element id pass; 0 disables")
    b.add_argument("--block-strength", type=float, default=0.65,
                   help="per-element tone blocking toward the measured vanilla "
                        "tone economy; 0 disables (skipped when relight runs)")
    b.add_argument("--retouch-out", default="",
                   help="also write a hand-retouch folder: per-cell styled PNG "
                        "(the editable file), an .ora with every underlay layer "
                        "(Krita/GIMP), loose layer PNGs, manifest and README. "
                        "After editing, package it with: build <dir> --no-style")
    b.add_argument("--shadow-desat", type=float, default=0.0,
                   help="drain chroma out of shadows toward neutral dark, the "
                        "measured fabric behaviour (sat 0.71 lit -> 0.33 dark); "
                        "for dyed-cloth materials. 0 disables")
    b.add_argument("--ground-shadow", type=float, default=0.0,
                   help="painted floor shadow behind the sprite (measured: black "
                        "at alpha 51); for objects standing on legs. 0 disables")
    b.add_argument("--contour", type=float, default=1.0,
                   help="painted contact weight: bottom silhouette rows drop to "
                        "the measured 0.71x of the interior; 0 disables")
    b.add_argument("--contour-top", type=float, default=stylemod.CONTOUR_TOP,
                   help="top-silhouette ratio; near-neutral by default, 0.77 for "
                        "objects whose top edge is a dark rim (measured: drum)")
    b.add_argument("--edge-turn", type=float, default=1.0,
                   help="darken surfaces grazing the view toward the measured "
                        "0.86 -- the tonal silhouette vanilla paints on curved "
                        "forms; 0 disables")
    b.add_argument("--paint-flatten", type=float, default=0.75,
                   help="relight path: pull each element's recovered paint toward "
                        "its flat paint tones; 0 disables")
    b.add_argument("--light-steps", type=float, default=0.8,
                   help="relight path: pull the light field into per-element "
                        "quantised steps; 0 disables")
    b.add_argument("--stroke-amplitude", type=float, default=0.0,
                   help="vertical brush-dash amplitude in value units; 0 disables")
    b.add_argument("--stroke-coverage", type=float, default=0.12)
    b.add_argument("--stroke-length", type=int, default=8)
    b.add_argument("--grain-strength", type=float, default=1.0)
    b.add_argument("--grain-coverage", type=float, default=1.0)
    b.add_argument("--paint-passes", type=int, default=2)
    b.add_argument("--paint-threshold", type=float, default=0.05)
    b.add_argument("--paint-sharpen", type=float, default=0.55)
    b.add_argument("--floor", action="store_true",
                   help="these are floor tiles: skip the grounding gradient")
    b.add_argument("--canopy-ramp", type=float, default=0.0,
                   help="crown-to-skirt value ramp over the foliage family "
                        "(painted fruit trees measure 0.09-0.23, vanilla crops 0.05)")
    b.add_argument("--rim", type=float, default=1.0,
                   help="boundary/interior value ratio on foliage and fruit elements "
                        "(painted sheets 0.68-0.91; 1.0 = off)")
    b.add_argument("--health-variants", action="store_true",
                   help="append unhealthy/dying/dead rows derived with the measured "
                        "vanilla hue transform (reference/health_lut.json)")
    b.add_argument("--no-style", action="store_true", help="skip the style pass entirely")
    b.set_defaults(func=cmd_build)

    i = sub.add_parser("inspect", help="describe a .pack or .tiles file")
    i.add_argument("file")
    i.add_argument("--limit", type=int, default=8)
    i.set_defaults(func=cmd_inspect)

    e = sub.add_parser("extract", help="write every sprite in a .pack out as a PNG")
    e.add_argument("file")
    e.add_argument("out")
    e.add_argument("--pages", action="store_true", help="also write whole atlas pages")
    e.set_defaults(func=cmd_extract)

    c = sub.add_parser("check",
                       help="score rendered cells against the vanilla style bands")
    c.add_argument("paths", nargs="+", help="cell PNGs, or directories of them")
    c.set_defaults(func=cmd_check)

    s = sub.add_parser("spec",
                       help="size, tone and palette a vanilla object of this kind has")
    s.add_argument("query", help="object name, family or category, e.g. 'Metal Drum'")
    s.add_argument("--face", default="S", choices=["S", "E", "top"],
                   help="which face the suggested base colours are for")
    s.add_argument("--colours", type=int, default=8)
    s.add_argument("--limit", type=int, default=3)
    s.add_argument("--sprite", action="store_true",
                   help="treat the query as one exact sprite name from the corpus")
    s.set_defaults(func=cmd_spec)

    r = sub.add_parser("refsheet",
                       help="decompose a reference sprite into regions, drawn lines "
                            "and per-region base colours")
    r.add_argument("target", help="vanilla sprite name, or a PNG path")
    r.add_argument("--colours", type=int, default=6,
                   help="paint clusters to segment into")
    r.add_argument("--scale", type=int, default=4)
    r.add_argument("--out", default="")
    r.add_argument("--game-media", default="")
    r.set_defaults(func=cmd_refsheet)

    m = sub.add_parser("compare",
                       help="diff a recreation against the vanilla sprite it copies")
    m.add_argument("vanilla", help="vanilla sprite name, e.g. crafted_01_32")
    m.add_argument("mine", help="your rendered cell PNG")
    m.add_argument("--out", default="build/compare.png")
    m.add_argument("--scale", type=int, default=3)
    m.add_argument("--crop", action="store_true",
                   help="crop to the sprites rather than showing the whole cell")
    m.add_argument("--game-media", default="")
    m.set_defaults(func=cmd_compare)

    p = sub.add_parser("preview",
                       help="compose a mock scene mixing your tiles with vanilla ones")
    p.add_argument("pack", help="the .pack to preview")
    p.add_argument("--out", default="build/preview.png")
    p.add_argument("--cols", type=int, default=5)
    p.add_argument("--rows", type=int, default=5)
    p.add_argument("--floor", default="blends_natural_01_0",
                   help="vanilla floor sprite to tile the ground with")
    p.add_argument("--vanilla", action="append",
                   help="vanilla sprite name to place alongside yours; repeatable")
    p.add_argument("--game-media", default="",
                   help="path to the game's media folder")
    p.set_defaults(func=cmd_preview)

    d = sub.add_parser("ids", help="list tiledef ids already claimed by installed mods")
    d.add_argument("--limit", type=int, default=30)
    d.set_defaults(func=cmd_ids)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())








