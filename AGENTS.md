# Agent guide

Operating manual for AI agents (and humans who work like them) driving
PZ Sprite Forge. The README explains *why* everything is the way it is; this
file is the *how*: commands, task recipes, verification standards, and the
pitfalls that cost the most time to rediscover.

## Ground rules

1. **Measure, don't invent.** Every number in a recipe or a material class
   traces back to a measurement of the shipped game files or of a render.
   When output and reference disagree, measure the disagreement (medians,
   per-channel ratios, pixel positions) and correct by the measured amount.
   Calibrating "by eye" is how sprites drift out of the vanilla band.
2. **Fold fixes into the tool, not the artefact.** A correction that lives in
   one output file is lost on the next render. Corrections belong in the
   material classes, the style pass, the rig, or the recipe -- in that order
   of preference.
3. **Verify by diffing.** Every claim ("matches vanilla", "unchanged by the
   refactor") is a comparison you can run: `pzforge compare`, a pixel diff,
   or a face-median table. Run it; do not assert it.

## Environment

- Blender headless: `"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" -b -P examples/<recipe>.py`
  (any 4.2+ works; adjust the path).
- Packaging/analysis: `uv run --python 3.12 --with pillow python -m pzforge.cli ...`
  from the repo root. The format layers are stdlib-only; only the imaging
  side needs Pillow.
- Project Zomboid must be installed; measurements read
  `.../ProjectZomboid/media` (override with `--game-media` where offered).
- Tests: `tests/test_pipeline.py`, `tests/test_geometry.py`,
  `tests/test_assets.py`, `tests/test_tilegeometry.py`,
  `tests/test_depthmap.py`, `tests/test_workshop.py` (plain `python`
  scripts, expect `ALL PASS`), `tools/validate_formats.py` (round-trip,
  expects `0 failed`). Run them all after touching `pzforge/` or
  `blender/`.

## The two-stage workflow

**Stage 1 -- material.** `MATERIAL_CLASSES` in `blender/pz_sprite_forge.py`
holds each material's measured grammar (hue correction, dark/light swing,
ramp mode + level/tint overrides, texture ramp range, projection, scale,
which `pzforge.texture.material_spec` grammar draws its map).
`F.forge_material(name, class, paint, ...)` applies it; every convention can
be overridden per part, but overrides should carry a measured justification.

**Stage 2 -- object.** An example file is a recipe: measured geometry plus a
materials dict mapping part ROLES to `forge_material` calls. Geometry
functions accept the dict so another material set can be swapped in
(`wood_drum.py` rebuilds `metal_drum.py`'s geometry in oak).

When swapping materials, classify every part role first:

| kind | example | on swap |
|---|---|---|
| shape-neutral | drum body, lid | change material only |
| material-born **surface** | rolled rim, bung | re-map to the target material |
| material-born **shape** | pressed grooves, seam strap, bolts | turn OFF via recipe flags and add the target material's own furniture (e.g. proud hoop bands) |

One role must never cover two physically different things -- that is what
put a steel rim on the first wooden barrel.

## Task recipes

### New object

1. Pick the closest vanilla reference. Measure it:
   `pzforge spec <sprite> --sprite` (sizes, paint inversion),
   `tools/show_sprite.py <sprite>` (pixel reads), bbox/column profiles for
   geometry (examples' headers show the working method).
2. Write `examples/<name>.py` following an existing recipe's shape:
   constants from measurements, `materials()` via `forge_material`,
   `build_<name>(mats)` geometry in tile units (1 m = 1 tile), `main()` with
   rig props (`footprint_x/y`, `facings`, `toon_shading=True`).
3. Render headless; build:
   `pzforge.cli build build/<name>_cells --mod-id <Mod> --preset <kind> --out dist`.
4. Compare: `pzforge.cli compare <vanilla_sprite> <extracted_png>` -- extract
   yours first with `pzforge.cli extract <pack> <dir>`. Iterate paints by
   per-channel transfer ratios (target/current per channel), not by eye.
5. Run the tests.

### Multi-tile object (one object spanning tiles)

Set `footprint_x/y`; grid (i,j) maps to world `(i, -j)` (grid y runs SOUTH =
Blender -Y; the rig handles it, but remember it when placing geometry). The
build cuts cells along tile seam planes via the tile pass and styles the
COMPOSED object once -- statistics, silhouette, strokes all see one object.
Verify cross-cell consistency: classify pixels by the normal pass into face
directions and compare per-(face, part, cell) medians; same-facing faces of
one material must match across cells (tolerance a few levels).

### Wall-style set (independent per-tile pieces)

Walls (WallW/WallN/corner/SE post) are separate single-tile sprites, not one
object. Place one piece per tile of a footprint, set
`props.isolate_tiles = True` (without it, a southern piece occludes -- and
amputates -- the piece behind it, and the canvas compose smears them
together), and build with `--preset wall --contour 0` (vanilla walls carry
no outline; contour erodes the 6 px post). The wall preset assigns
WallW/WallN/WallNW/WallSE properties cyclically by sprite index.

### Four facings

`props.facings = "4"`. The rig rotates the subject (light stays fixed, so an
east face darkens exactly as vanilla's does), transposes the footprint for
E/W, and re-parks the subject on the rotated footprint. Nothing else needed.

### Hand retouch round trip

`build ... --retouch-out retouch/<name>` exports styled PNGs + layers +
manifest. Edit RGB only -- **alpha is invariant** (trim offsets depend on
it). Rebuild with `build retouch/<name> ... --no-style`. Scripted retouches
follow the same rule; keep corrections face-coherent (uniform over a
part x facing), never position-blind pools.

### A mod's tile set: the standard path

Every sheet a mod ships is built from ONE asset spec, never from build
commands typed by hand (`examples/homebrewing_assets.json` is the worked
example: eight sheets, previews, measure bands, Workshop images).

1. One `assets` entry per sheet: the recipe (+ `--` args when one recipe
   renders several parts), its cells dir, and the exact `build` arguments --
   mod id, tiledef id, every tile property (copied from the shipped `.tiles`
   or the vanilla twin, never recalled), style flags. Variants of one object
   share their style flags (see "One object, several poses").
2. `assets <spec> --list`, then `--only a,b` / `--no-render` while
   iterating. `--install` is the only step that writes into the mod; the
   spec's `install` names the mod's media folders.
3. `build` writes each sheet's `tileGeometry.txt` block and
   `DEPTH_<sheet>.png` from the rig's per-part boxes; `--install` merges
   the block into the mod's one file and copies the map (Build 42 depth,
   below). A sheet shipped without them is drawn as a billboard.
4. The spec's `previews` and `measures` are the art checks (layered
   objects, below); its `workshop` section makes the Workshop images
   (`workshop <spec> [--publish]`) from the very packs it built.
5. Rebuild proof: after any pipeline change, rebuild with `--no-render`
   into a scratch copy of the mod's media and `diff -rq` it against the
   shipped folder. Only deliberate changes may differ, and you name them.
6. Then the engine: `pzh check`, `pzh client` (a test that places the
   objects and takes a screenshot is the art's last word, see below).

### Layered object on one square (rack with tiers, shelf with contents)

The game draws a square's objects in list order, each lifted by its render
y offset, and Build 42 then resolves overlaps per pixel by depth. Model it
the way the game draws it:

1. One sheet per draw layer (Home Brewing's rack: the base -- rear posts and
   deck 1 --, deck 2, deck 3, the front dividers), and the contents (a racked
   barrel) as their own sheet, inserted between the layers in list order.
2. Heights from the reference, through the game's scale: 43 px per metre at
   1x (vanilla table: 0.79 m <-> `Surface` 34). Deck tops measured on the
   reference (0.15 / 0.85 / 1.55 m) give each tier's render y offset; then
   lower it to where the contents really touch -- a bellied cask rests on
   its head chimes, 3 px below the deck top, or it floats on its bilge.
3. Near and far parts per facing: `F.tag_facings` (below). For N and W the
   near pair of a 1x1 rack is its own back pair.
4. Tile properties per layer: the base is a table (`IsTable`, `Surface`)
   so the moveable system can stand an object on it; helpers are
   `solidtrans`; the contents' bottom-tier sheet is `IsTableTop` +
   `IsMoveAble`, the upper tiers' sheet neither (nothing is lifted off a
   top shelf).
5. Judge the `stack` preview (every facing, layers at their offsets), never
   one sprite; pin the reference's luminance ratios with `measure` bands
   (the racked barrel: flank/head 0.56 measured, band 0.45-0.70). Then an
   in-engine screenshot: the preview shows list order, the game shows depth.

### Build 42 depth (every custom tile)

The rig exports every visible part's tile-local box per facing;
`F.tag_geometry(parts, "deck1")` merges parts into one box and `"-"` leaves
a part out. Depth is sampled only where the sprite has pixels, so a box
round a cask is enough: thin slabs per deck, thin boxes per post. The map's
encoding is measured, not assumed (`depthmap calibrate` against a vanilla
tileset: `value = 103.8 * depth + 190.3`, `reference/depth_calibration.json`);
re-run it after a game update. Two symptoms, two causes: a sprite with a
render offset drawn over the shelf above it = missing geometry; a
checkerboard of one sprite through another = missing depth map.

### One object, several poses

A barrel upright and the same barrel racked are ONE object: one geometry
function per physical part (`hb_barrel.head_furniture` serves both heads),
the pose passing its own measured paints (the racked cask's flank is
darker because the deck above shades it), and the same build flags
(stroke, grounding, ground shadow, contour) on every sheet. Two recipes
that drift apart read as two different barrels side by side.

### Workshop images

`workshop <spec>` writes the promo (the tiles on a vanilla floor, layered
columns at their render offsets) and the thumbnail (the house style, see
`pzforge.workshop.HOUSE_STYLE`: warm radial background, Georgia title,
stations at whole-number scale, item icons tinted by their fluid's
ColorReference) from the packs the mod ships; `--publish` copies them into
the Workshop folder (`preview.png` RGB, under Steam's 1 MB). Store
screenshots come from `pzh client`, never from a staged scene.

### New material class

Measure a vanilla reference of that material: per-face medians (S/E/W/top),
saturation-vs-value curve, texture signature (spread/sat/gradient), joint
colours if patterned. Add a `MATERIAL_CLASSES` entry + a
`material_spec` grammar in `pzforge/texture.py`, then prove it on one
object with a `pzforge compare` table. Every number in the entry must trace
to one of those measurements.

## Verification standards

- `pzforge compare` for silhouette IoU, medians, left/right falloff.
- Face-constancy: one material + one face direction = one tone (vanilla's
  rule; hue/sat stay constant, only value steps by facing).
- Refactors: re-render and pixel-diff against the pre-refactor cells; only
  deliberate changes may differ, and you should be able to name them.
- Bold check: view output at 1x and 0.5x -- if the material read washes out,
  features need `texture.bolden()` treatment, not more contrast at 2x.
- Art with no vanilla twin: `measure` bands taken from the reference
  screenshot, measured the same way, on the `stack` preview.
- Layered objects: the `stack` preview for every facing, then an in-engine
  screenshot (`pzh client` + `PZH.shot`). A composite that looks right
  proves the draw order, not the depth the game draws with.
- Rebuild proof: `assets <spec> --no-render --install` into a scratch copy
  of the mod's media, `diff -rq` against the shipped files.
- A mod is verified in the engine, not by reading it: `pzh check` (names
  resolve), `pzh server` (server logic on real objects), `pzh client` (real
  build / transfer / cancel actions, loot window reach, UI rows, item counts
  across inventory + container + floor). Tests live in the mod's `tests/`,
  written on the `PZH` library (`harness/templates/`). A claim such as
  "nothing is lost on cancel" is a `PZH.count` before and after, in the log,
  and the runner's exit status is the verdict. Store screenshots come from the
  same runner, never from a staged scene. Isolated `-cachedir` only; the
  user's Zomboid folder and live server are never touched, and a process is
  found by its cachedir, never killed by name (other sessions run their own).
  Run `pzh selftest` first on a new machine. `harness/README.md` lists the
  engine pitfalls the tool already absorbs.

## Pitfalls (each cost a debugging session)

- `bpy.types.Image.pixels` returns sRGB-encoded values, not linear.
- A material edit that changes nothing means the material is not applied to
  that face (check `poly.material_index` by normal, not by z).
- Palette colours are RENDERED colours: invert only the brightest common
  shade through the lighting response (`pzforge spec` does this), or you
  count the lighting twice.
- Texture sampling averages ~3-4 texels per sprite px: features narrower
  than that vanish; calibrate `texture_scale` by rendering and measuring,
  not by arithmetic.
- The style pass must never change alpha. Silhouette softness is fixed at
  render time (`filter_size`), nowhere else.
- Fabric: bolden by daub DEPTH and paint-swing width (same hue), never by
  daub size (reads as plastic) or hue rotation across the map (reads as
  marble). Check the paint swing first -- an 8% swing hides any texture.
- Wood/metal keep or gain saturation in shadow; dyed fabric desaturates
  toward neutral dark (`--shadow-desat`). Material-dependent -- measure.
- Cast-iron rule for stats: tone matching, element budgets and shading
  fields operate on the OBJECT (composed canvas), never per cell.
- If a wall piece renders amputated, a neighbouring piece occluded it:
  `isolate_tiles`.
- "Right in the composite, wrong in the game": the sheet is missing its
  tile geometry or its depth map, not a draw-order fix (Build 42 draws by
  depth; the composite only simulates list order).
- A part that is right on S and E but drawn over the contents on N and W is
  a far post in the last-drawn sheet: gate it with `tag_facings`.
- A contact shadow (`--ground-shadow`) seats an object at game size and
  reads as a dark tile scaled up; key art drops it
  (`workshop.strip_contact_shadow`).

## Layout

- `blender/pz_sprite_forge.py` -- the whole addon: rig, toon ramp,
  material classes, `forge_material`, `render_cells` (single file on
  purpose; it must import inside Blender with no package).
- `pzforge/` -- build pipeline: `cli.py` (entry), `style.py`, `finish.py`,
  `assets.py` (the spec harness), `geometry.py`/`depthmap.py` (Build 42
  depth), `workshop.py` (Workshop images),
  `texture.py`, `packfile.py`/`tiledef.py` (formats), `preview.py`,
  `retouch.py`, `spec.py`/`recipe.py` (measurement).
- `examples/` -- recipes. `tools/` -- measurement scripts that produced
  `reference/`. `tests/` -- run them.
- `harness/` -- `pzh`, the in-engine test tool: `install.sh`,
  `run_client.sh`, `run_server.sh`, `pzwin.ps1` (window driver),
  `crosscheck.js`, `make_promo.ps1`, the `PZH` Lua libraries under `lua/`,
  `selftest/` (the tool on vanilla objects) and `templates/`.
