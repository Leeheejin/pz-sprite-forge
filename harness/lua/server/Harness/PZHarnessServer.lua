--- PZ Harness, server side. A self-test that runs inside a real dedicated server with no
--- players: setup on OnServerStarted, polling on EveryOneMinute, tagged PASS/FAIL lines in
--- the server console so the boot log can be read back mechanically.
---
--- Lives only in an ISOLATED server copy of the mod (see harness/README.md); never shipped.
---
--- Usage from a test file in lua/server/:
---   require "Harness/PZHarnessServer"
---   PZH.configure({ tag = "[MYMOD]" })
---   PZH.runServer(setup, poll)   -- setup(state) once; poll(state, minute) every game minute
---
--- A player-less server has no loaded chunks, so getCell():getGridSquare() is always nil.
--- PZH.detachedSquare makes a square the engine will still process (ProcessItems is per
--- cell, not per square), which is enough for object, container and cooking checks.
--- AddTileObject on such a square logs a PathfindNative "square.chunk is null" NPE once per
--- call; the object is already placed, so that line is harness noise, not a mod error.

PZH = PZH or {}
PZH.TAG = PZH.TAG or "[PZH]"
PZH.results = {}

function PZH.configure(opts)
    if opts and opts.tag then PZH.TAG = opts.tag end
end

function PZH.note(msg) print(PZH.TAG .. " ---- " .. tostring(msg)) end
function PZH.report(name, pass, extra)
    PZH.results[#PZH.results + 1] = pass and true or false
    print(PZH.TAG .. " " .. (pass and "PASS" or "FAIL") .. " | " .. name ..
          (extra ~= nil and ("  << " .. tostring(extra)) or ""))
end
function PZH.try(name, fn)
    local okCall, res, extra = pcall(fn)
    if not okCall then PZH.report(name, false, "lua error: " .. tostring(res)) return nil end
    PZH.report(name, res and true or false, extra)
    return res
end
function PZH.summarise()
    local pass, fail = 0, 0
    for _, r in ipairs(PZH.results) do if r then pass = pass + 1 else fail = fail + 1 end end
    print(PZH.TAG .. " ===== RESULT " .. pass .. " passed, " .. fail .. " failed =====")
    print(PZH.TAG .. " DONE")
end

--- instanceItem is the one factory that works from Lua here (InventoryItemFactory.CreateItem
--- returns nil). Script definitions (zombie.scripting.objects.Item) lack the runtime getters
--- such as getReplaceOnCooked(); always inspect a real instance.
function PZH.make(fullType)
    local okCall, it = pcall(function() return instanceItem(fullType) end)
    if not okCall then return nil end
    return it
end
function PZH.holds(container, fullType)
    if not container then return false end
    local items = container:getItems()
    for i = 0, items:size() - 1 do if items:get(i):getFullType() == fullType then return true end end
    return false
end
function PZH.dump(container)
    local items = container:getItems()
    local out = {}
    for i = 0, items:size() - 1 do out[#out + 1] = items:get(i):getFullType() end
    return table.concat(out, ",")
end
function PZH.detachedSquare(x, y, z)
    return IsoGridSquare.new(getCell(), nil, x, y, z or 0)
end
--- a plain IsoObject with the sprite on a square, the state a built tile is in before the
--- entity's OnCreate turns it into its real class
function PZH.placeSprite(square, spriteName)
    local obj = IsoObject.new(getCell(), square, getSprite(spriteName))
    square:AddTileObject(obj)
    return obj
end

function PZH.runServer(setup, poll)
    local state = { minute = 0 }
    Events.OnServerStarted.Add(function()
        print(PZH.TAG .. " ===== self-test start =====")
        local okCall, err = pcall(setup, state)
        if not okCall then
            print(PZH.TAG .. " FAIL | setup crashed :: " .. tostring(err))
            PZH.results[#PZH.results + 1] = false
            PZH.summarise()
            return
        end
        Events.EveryOneMinute.Add(function()
            if state.finished then return end
            state.minute = state.minute + 1
            local okPoll, perr = pcall(poll, state, state.minute)
            if not okPoll then
                PZH.report("poll crashed at minute " .. state.minute, false, tostring(perr))
                state.finished = true
                PZH.summarise()
            end
        end)
    end)
    return state
end
--- call from poll when the test is over
function PZH.finishServer(state)
    state.finished = true
    PZH.summarise()
end
