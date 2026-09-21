# PZ Harness: in-engine tests for Project Zomboid mods

A test tool. It runs a mod in a real Project Zomboid client and a real dedicated server,
driven by a Lua test file, on an isolated `-cachedir` that never touches the user's saves,
options or live server, and reads tagged PASS/FAIL lines back out of the console. Static
checks catch names that do not resolve; only the engine shows an item lost on a cancelled
transfer, a container the loot window cannot reach, or a fire that never lights.

```
pzh check    --mod <dir> [--icon-prefix X] [--key-marker X] [--langs EN,KO]
pzh server   --mod <dir> --id <ModId> --test <file.lua> [--port N] [--timeout s]
pzh client   --mod <dir> --id <ModId> --test <file.lua> [--out <dir>] [--park] [--accept-tos] [--timeout s]
pzh selftest [--accept-tos] [--only client|server]
pzh promo    <make_promo.ps1 arguments>
```

| stage | what runs | what it proves |
|---|---|---|
| `check` | node, seconds | every item, model, icon, timed action, category, sprite and translation key the mod names exists (B42 boots into an error on any that does not) |
| `server` | dedicated server, no players, ~3 min | server-side logic on real objects: containers, cooking, fuel, modData, save/load-time flags |
| `client` | single-player client, ~5-8 min | the player's paths: real build action, real transfer actions and cancels, loot window reach, UI rows and tooltips, item counts across inventory + container + floor |
| `client` with a shot list | same | store screenshots: clothed survivor, noon, clear sky, zoomed, no chatter, no zombies |
| `selftest` | both, on vanilla objects | the tool itself: a built wooden crate, a lit barrel oven, transfers, cancels, the loot window, a picture |

Tests live with the mod, not here: `<mod>/tests/client/*.lua`, `<mod>/tests/server/*.lua`.
`templates/` has a skeleton of each; `selftest/` is a complete, mod-independent pair.

## Requirements

- Windows, Git Bash (the scripts use `cygpath`, `powershell`, `taskkill`).
- Project Zomboid installed (`$PZ_GAME`, default the Steam path) and, for `server`, a
  dedicated server install (`$PZ_SERVER`, default `C:/PZServer`). Their own JREs are used.
- node for `check`.
- The mod in the B42 layout: `<dir>/42/mod.info` + `<dir>/common/media/...`. A `mod.info`
  at `<dir>` itself makes the B42 client refuse the folder.

## Quick start

```bash
PZH=/path/to/pz-sprite-forge/harness
bash $PZH/pzh selftest --accept-tos                       # once: the tool works on this machine

cd /path/to/MyMod-dev                                    # mods/42/mod.info + mods/common/, tests/
bash $PZH/pzh check  --mod mods --icon-prefix MY --key-marker MY
bash $PZH/pzh server --mod mods --id MyMod --test tests/server/selftest.lua
bash $PZH/pzh client --mod mods --id MyMod --test tests/client/client_test.lua --accept-tos
bash $PZH/pzh client --mod mods --id MyMod --test tests/client/store_shots.lua --park --out shots
```

Each run installs the mod, `Harness/` and the test into a cachedir under `$PZH_CACHE`
(default `%LOCALAPPDATA%\pzh`, one per mod and side), launches the game on it, gets past
everything that waits for a human, waits for `<TAG>] DONE`, prints the test's lines and the
`RESULT n passed, m failed` summary, collects screenshots, and stops the process. The exit
status is 0 only on `0 failed` (a shot list, which has no RESULT line, passes on DONE).
The tag is read from the test's `PZH.configure({ tag = "[...]" })`.

The first launch on a fresh cachedir stops at the game's terms-of-service dialog; click
Agree in the window once, or pass `--accept-tos` to record the acceptance in the isolated
`options.ini` up front (the same terms you accepted in your own install; it is a flag so
that this is your choice, not the script's).

Processes are found by their cachedir in the command line, never by process name, so
several sessions can run their own clients and servers side by side. Each run's console
is kept as `<cachedir>/console.<TAG>.txt` (`server-console.txt` for the server);
screenshots land in `<cachedir>/Screenshots/` and, with `--out`, are copied there.

## Writing a client test

```lua
require "Harness/PZHarnessClient"
PZH.configure({ tag = "[MYMOD]", world = "mymodtest" })
PZH.bootSandbox()                       -- main menu -> fresh sandbox world, no clicks

local T = {}
local steps = {}
steps.idle = function(S)                -- runs every tick until it moves on
    local p = PZH.player()
    if not p or not p:getSquare() then return end
    T.player, T.inv = p, p:getInventory()
    PZH.unpause()
    PZH.give(T.inv, { "Base.Hammer", "Base.Plank", "Base.Plank" })
    local bx, by, bz = PZH.build(p, "my_sprite_01_0")     -- the real build action
    T.square = getCell():getGridSquare(bx, by, bz)
    PZH.standAt(p, bx - 0.5, by + 0.5, bz)                -- tile centre, next to it
    PZH.phase("built")
end
steps.built = function(S)
    local obj = PZH.findBySprite(T.square, "my_sprite_01_0")
    if obj then
        PZH.report("real build action produced the object", true, "after " .. S.ticks .. " ticks")
        PZH.try("it has a container", function() return obj:getContainer() ~= nil end)
        PZH.phase("finished")
    elseif S.ticks > 1200 then
        PZH.report("real build action produced the object", false); PZH.phase("finished")
    end
end
steps.finished = PZH.finish             -- prints "RESULT n passed, m failed" and DONE
PZH.run(steps)
```

`PZH.report(name, pass, extra)` and `PZH.try(name, fn)` (fn returns `pass, extra`; a Lua
error is a FAIL) are the assertions. `PZH.count(filter, {containers}, player)` counts
across containers and the player's floor square, which is how loss and duplication are
measured: the total must not change across a transfer, a cancel or a conversion.
`PZH.queueTransfers` / `PZH.cancelActions` use the real `ISInventoryTransferAction` and
`ISTimedActionQueue`, the same code the player's drag runs. `PZH.showLoot(container,
rect, hideInventory)`, `PZH.showTooltip`, `PZH.deselectLoot(obj)`, `PZH.hideUI(obj)` and
`PZH.shot(name)` are the picture helpers; `PZH.daylight`, `PZH.zoomIn`, `PZH.wear`,
`PZH.godMode` and the `housekeeping` option (`zombies`, `devicesRadius`) make a shot
presentable. `selftest/client.lua` uses all of them on a vanilla crate.

## Writing a server test

```lua
require "Harness/PZHarnessServer"
PZH.configure({ tag = "[MYTEST]" })
PZH.runServer(function(st)              -- once, on OnServerStarted
    local sq = PZH.detachedSquare(6800, 5500, 0)
    st.obj = PZH.placeSprite(sq, "my_sprite_01_0")
    PZH.try("item instantiates", function() return PZH.make("MyMod.Thing") ~= nil end)
end, function(st, minute)               -- every game minute
    if minute >= 5 then PZH.finishServer(st) end
end)
```

A player-less server has no loaded chunks: `getCell():getGridSquare()` is always nil, so
`PZH.detachedSquare` builds a square the engine still processes (ProcessItems is per cell).
`PZH.make` wraps `instanceItem`, the one item factory that works from Lua here.
`selftest/server.lua` cooks a steak in a vanilla barrel oven this way.

## Pitfalls the tool already handles (so you know why it does what it does)

Client
- A fresh cachedir's first launch resets `mods/default.txt` to no mods unless
  `mods/reset-mods-42_00.txt` exists (the game says so in that file). `install.sh` writes
  the marker; without it the mod, and the test in it, never load and the client just sits
  on the main menu.
- The first launch also stops at the terms-of-service dialog (see Quick start).
- The client must be started detached and found again by cachedir: `ProjectZomboid64.exe`
  re-execs itself, so a launcher PID goes stale, and other sessions' harnesses that kill
  by process name will kill yours if you kill by name too.
- Three things pause a loaded game: `-debug` opens the character panel paused, a fresh
  game shows a "click to continue" gate, and the first game shows the survival guide.
  The runner clicks the window centre after `game loading took`; `install.sh` writes
  `showSurvivalGuide=false`; `PZH.unpause` closes the panel and sets speed 1.
- `focusloss=true` (the default) freezes the whole game loop when another window takes
  focus; with several sessions on one desktop a run "stalls" at random. `install.sh`
  writes `focusloss=false` before the first launch.
- The gate click leaves the mouse over the game, and a hover tooltip lands in your
  screenshot. `--park` moves it to a corner via `pzwin.ps1 move`.
- A Java exception is not caught by Lua `pcall`: the OnTick handler dies from that tick on.
  `PZH.run` wraps each step, but engine calls that can throw belong in their own `pcall`.
- The loot window lists containers in the player's 3x3 that `canReachTo`; a player set
  down on a tile edge (integer x) is pushed a tile over and the container drops out of the
  list. Stand at tile centres (`+0.5`).
- `ISBuildMenu.cheat` only skips the walk; the build recipe still consumes its inputs and
  needs the skill. `PZH.build` does the real thing, so give the player materials and
  `PZH.setPerk`.
- Vanilla draws per-row detail (its Cooking bar, a mod's bar) on expanded rows only;
  `PZH.showLoot` expands them. A forced tooltip needs `setCharacter(player)`.
- The loot page's `update()` runs while hidden and re-applies the selected container's
  orange outline every frame, and un-collapses a pinned page. `PZH.deselectLoot` unpins
  and collapses it, which lets the page clear the object itself.
- A stray zombie bite tears the UI pages down (`getPlayerLoot(0)` becomes nil). Shot
  lists run `PZH.godMode` and sweep zombies every 30 ticks.
- Zoom: `doZoomScroll(0, -1)` steps closer; `getZoom` still reads the old value on that
  frame.
- Context-menu options sit at `options[1 .. numOptions-1]`; read them with `ipairs`.
- `getTimestampMs()` is not available at OnMainMenuEnter; `PZH.bootSandbox` names the
  world with `getTimestamp()`/`ZombRand` so a killed run never contaminates the next.

Server
- `-adminpassword` must be given or the first boot waits on stdin for one.
- `PauseEmpty=true` (the default) stops game time with no players, so `EveryOneMinute`
  never fires; `run_server.sh` patches it after the generation boot.
- RakNet keeps the UDP port for a moment after a kill (`Connection Startup Failed. Code:
  5`); the runner kills its own previous JVM and waits before booting.
- `AddTileObject` on a detached square logs one PathfindNative `square.chunk is null` NPE
  per call; the object is placed, the line is harness noise. Vanilla's own boot noise
  (BrokenFences ThumpSound, ladderW/WindowShape, Mannequin zone, AnimSets NoSuchFile,
  FluidContainerScript blank name) is harmless too; filter the log by your tag.
- `zombie.scripting.objects.Item` (the script definition) lacks runtime getters such as
  `getReplaceOnCooked()`; inspect a real instance from `PZH.make`.

## Layout

```
harness/
  pzh                 the command: check / server / client / selftest / promo
  install.sh          mod + Harness/ + test into an isolated cachedir (used by pzh)
  run_client.sh       launch, pass the gates, wait for DONE, collect lines and shots
  run_server.sh       generation boot, ini patch, boot, wait for DONE
  pzwin.ps1           window driver: capture / click / move / key by cachedir tag
  crosscheck.js       static name resolution against vanilla
  make_promo.ps1      compose a 1152x760 Workshop header from harness shots
  lua/client/Harness/PZHarnessClient.lua
  lua/server/Harness/PZHarnessServer.lua
  selftest/           client.lua, server.lua and the empty mod they run in
  templates/          client_test.lua, server_test.lua
```

The harness Lua goes only into the isolated copy. Never ship `Harness/` or a test file
inside a mod.
