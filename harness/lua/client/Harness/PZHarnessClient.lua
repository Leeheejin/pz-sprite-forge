--- PZ Harness, client side. Drives a real single-player game from Lua so a mod can be
--- verified in the engine instead of by reading its code: boot straight into a fresh
--- sandbox world, run a step machine on OnTick, act through the real build / transfer /
--- UI paths, print tagged PASS/FAIL lines to console.txt, and take screenshots.
---
--- Lives only in an ISOLATED client copy of the mod (see harness/README.md); it is never
--- shipped. Everything here was measured on B42.20; the comments say what broke without it.
---
--- Usage from a test file in lua/client/:
---   require "Harness/PZHarnessClient"
---   PZH.configure({ tag = "[MYMOD]", world = "mymodtest" })
---   PZH.bootSandbox()
---   PZH.run({ idle = function(S) ... PZH.phase("next") end, next = ..., finished = PZH.finish })

PZH = PZH or {}
PZH.TAG = PZH.TAG or "[PZH]"
PZH.WORLD = PZH.WORLD or "pzhtest"
PZH.results = {}
PZH.S = { phase = "idle", ticks = 0 }
PZH.housekeeping = { zombies = false, devicesRadius = 0 }   -- enabled per test, see store shots

function PZH.configure(opts)
    opts = opts or {}
    if opts.tag then PZH.TAG = opts.tag end
    if opts.world then PZH.WORLD = opts.world end
    if opts.housekeeping then PZH.housekeeping = opts.housekeeping end
end

--- reporting -----------------------------------------------------------------------
function PZH.note(msg) print(PZH.TAG .. " ---- " .. tostring(msg)) end
function PZH.report(name, pass, extra)
    PZH.results[#PZH.results + 1] = pass and true or false
    print(PZH.TAG .. " " .. (pass and "PASS" or "FAIL") .. " | " .. name ..
          (extra ~= nil and ("  << " .. tostring(extra)) or ""))
end
--- fn returns (pass, extra); a Lua error inside counts as a FAIL with the message
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
--- lands in <cachedir>/Screenshots/<name>
function PZH.shot(name)
    local okCall, err = pcall(function() getCore():TakeFullScreenshot(name) end)
    PZH.note("screenshot " .. name .. (okCall and "" or (" FAILED: " .. tostring(err))))
end

--- boot ----------------------------------------------------------------------------
--- Straight from the main menu into a fresh sandbox world, exactly what the menu entry
--- does in MainScreen.lua, so no clicking is needed. A fresh world every run: a killed
--- client leaves its save behind, and a reused world carries the previous run's objects
--- into the counts. getTimestampMs() is not available this early; getTimestamp()/ZombRand are.
function PZH.bootSandbox(opts)
    opts = opts or {}
    local started = false
    Events.OnMainMenuEnter.Add(function()
        if started then return end
        started = true
        MainScreen.instance:setDefaultSandboxVars()
        if opts.sandbox then
            for k, v in pairs(opts.sandbox) do pcall(function() getSandboxOptions():set(k, v) end) end
        end
        getWorld():setGameMode("Sandbox")
        getWorld():setMap(opts.map or "DEFAULT")
        local stamp = nil
        pcall(function() stamp = getTimestamp() end)
        if not stamp then stamp = ZombRand(100000000) end
        local world = PZH.WORLD .. tostring(stamp)
        PZH.note("main menu reached; starting fresh sandbox world " .. world)
        getWorld():setWorld(world)
        deleteSave("Sandbox/" .. world)
        createWorld(world)
        GameWindow.doRenderEvent(false)
        forceChangeState(LoadingQueueState.new())
    end)
end

--- step machine --------------------------------------------------------------------
--- steps[phase](S) runs every tick; a Java exception is NOT caught by pcall and kills the
--- OnTick handler from that tick on, so keep engine calls that can throw inside their own
--- pcall. "harness engaged" is the line the runner script waits for after the click gate.
function PZH.phase(name) PZH.S.phase = name; PZH.S.ticks = 0 end
function PZH.run(steps)
    Events.OnGameStart.Add(function()
        PZH.note("game started; harness engaged")
        PZH.S.phase, PZH.S.ticks = "idle", 0
        Events.OnTick.Add(function()
            local S = PZH.S
            S.ticks = S.ticks + 1
            if PZH.player() and S.ticks % 30 == 0 then PZH.housekeep() end
            local step = steps[S.phase]
            if not step then return end
            local okCall, err = pcall(step, S)
            if not okCall then
                PZH.report("step '" .. S.phase .. "' crashed", false, tostring(err))
                PZH.phase("finished")
            end
        end)
    end)
end
--- default terminal step: print the summary, then ask the game to quit (the runner kills
--- the process by cachedir anyway; getCore():quit() alone can leave the window behind)
function PZH.finish(S)
    if S.ticks == 1 then PZH.summarise()
    elseif S.ticks == 120 then pcall(function() getCore():quit() end) end
end

--- player and world ----------------------------------------------------------------
function PZH.player() return getSpecificPlayer(0) end
--- -debug opens the character panel with the game paused; nothing timed runs until unpaused
function PZH.unpause()
    pcall(function() UIManager.getSpeedControls():SetCurrentGameSpeed(1) end)
    pcall(function() local w = getPlayerInfoPanel(0); if w and w:isVisible() then w:close() end end)
end
--- a bite tears the UI pages down mid-run (getPlayerLoot(0) becomes nil)
function PZH.godMode(p) pcall(function() (p or PZH.player()):setGodMod(true) end) end
function PZH.setPerk(p, perk, level) pcall(function() p:setPerkLevelDebug(perk, level) end) end
function PZH.standAt(p, x, y, z)
    p:setX(x); p:setY(y); p:setZ(z)
end
--- noon, no weather, and the climate floats that decide fog/cloud/rain/night pinned by
--- override so nothing drifts in mid-run (for screenshots)
function PZH.daylight()
    pcall(function() getGameTime():setTimeOfDay(12.5) end)
    pcall(function()
        local cm = getClimateManager()
        cm:stopWeatherAndThunder()
        local pin = { [ClimateManager.FLOAT_FOG_INTENSITY] = 0, [ClimateManager.FLOAT_CLOUD_INTENSITY] = 0,
                      [ClimateManager.FLOAT_PRECIPITATION_INTENSITY] = 0, [ClimateManager.FLOAT_NIGHT_STRENGTH] = 0,
                      [ClimateManager.FLOAT_DAYLIGHT_STRENGTH] = 1 }
        for idx, v in pairs(pin) do
            local cf = cm:getClimateFloat(idx)
            cf:setEnableOverride(true); cf:setOverride(v, v)
        end
    end)
end
--- zoom value: smaller = closer; -1 steps in. getZoom reads the old value on the same
--- frame, the picture is closer on the next one.
function PZH.zoomIn(steps)
    pcall(function()
        local core = getCore()
        core:setAutoZoom(0, false)
        for _ = 1, (steps or 1) do core:doZoomScroll(0, -1) end
    end)
end
--- TVs and radios in range chatter into speech bubbles
function PZH.silenceDevices(radius)
    local p = PZH.player()
    if not p then return 0 end
    local px, py, pz = math.floor(p:getX()), math.floor(p:getY()), math.floor(p:getZ())
    local n = 0
    for dx = -radius, radius do for dy = -radius, radius do
        local sq = getCell():getGridSquare(px + dx, py + dy, pz)
        if sq then
            local objs = sq:getObjects()
            for i = 0, objs:size() - 1 do
                local o = objs:get(i)
                if instanceof(o, "IsoWaveSignal") then
                    pcall(function() o:getDeviceData():setIsTurnedOn(false); n = n + 1 end)
                end
            end
        end
    end end
    return n
end
--- zombies walk into frame, and into the survivor
function PZH.sweepZombies()
    local n = 0
    pcall(function()
        local zl = getCell():getZombieList()
        for i = zl:size() - 1, 0, -1 do
            local z = zl:get(i)
            pcall(function() z:removeFromWorld(); z:removeFromSquare() end)
            n = n + 1
        end
    end)
    return n
end
function PZH.housekeep()
    local h = PZH.housekeeping
    if h.zombies then
        local z = PZH.sweepZombies()
        if z > 0 then PZH.note("swept " .. z .. " zombies") end
    end
    if h.devicesRadius and h.devicesRadius > 0 then PZH.silenceDevices(h.devicesRadius) end
end

--- inventory -----------------------------------------------------------------------
function PZH.give(inv, types)
    local out = {}
    for _, t in ipairs(types) do out[#out + 1] = inv:AddItem(t) end
    return out
end
--- drop the loose starting items, keep what is worn
function PZH.clearLoose(inv)
    local items = inv:getItems()
    local loose = {}
    for i = 0, items:size() - 1 do local it = items:get(i); if not it:isEquipped() then loose[#loose + 1] = it end end
    for _, it in ipairs(loose) do inv:Remove(it) end
end
function PZH.wear(p, types)
    for _, t in ipairs(types) do
        pcall(function()
            local it = p:getInventory():AddItem(t)
            p:setWornItem(it:getBodyLocation(), it)
        end)
    end
    pcall(function() p:resetModelNextFrame() end)
end
function PZH.first(container, filter)
    local items = container:getItems()
    for i = 0, items:size() - 1 do if filter(items:get(i)) then return items:get(i) end end
    return nil
end
function PZH.collect(container, filter)
    local out = {}
    local items = container:getItems()
    for i = 0, items:size() - 1 do if filter(items:get(i)) then out[#out + 1] = items:get(i) end end
    return out
end
--- items matching filter across any number of containers, plus the player's floor square
--- when a player is given (a dropped item is neither lost nor duplicated, just moved)
function PZH.count(filter, containers, p)
    local n = 0
    for _, c in ipairs(containers) do
        if c then
            local items = c:getItems()
            for i = 0, items:size() - 1 do if filter(items:get(i)) then n = n + 1 end end
        end
    end
    if p and p:getSquare() then
        local wo = p:getSquare():getWorldObjects()
        for i = 0, wo:size() - 1 do
            local it = wo:get(i):getItem()
            if it and filter(it) then n = n + 1 end
        end
    end
    return n
end
function PZH.byType(fullType) return function(it) return it:getFullType() == fullType end end
function PZH.byPrefix(prefix)
    local n = string.len(prefix)
    return function(it) return string.sub(it:getFullType(), 1, n) == prefix end
end
function PZH.dump(container, stripModule)
    local items = container:getItems()
    local out = {}
    for i = 0, items:size() - 1 do
        local t = items:get(i):getFullType()
        if stripModule then t = t:gsub(stripModule .. "%.", "") end
        out[#out + 1] = t
    end
    return table.concat(out, ",")
end
--- one real ISInventoryTransferAction per item, the way the player's drag does it
function PZH.queueTransfers(p, items, from, to)
    for _, it in ipairs(items) do ISTimedActionQueue.add(ISInventoryTransferAction:new(p, it, from, to)) end
    return #items
end
function PZH.cancelActions(p) ISTimedActionQueue.clear(p) end

--- world objects and building ------------------------------------------------------
function PZH.findBySprite(square, spriteName, className)
    if not square then return nil end
    local objs = square:getObjects()
    for i = 0, objs:size() - 1 do
        local o = objs:get(i)
        if (not className or instanceof(o, className)) and o:getSprite() and o:getSprite():getName() == spriteName then
            return o
        end
    end
    return nil
end
--- The real build path: the object info the build menu uses, the build object it makes and
--- its tryBuild, which queues the real ISBuildAction that ends in the entity's OnCreate.
--- ISBuildMenu.cheat only skips the walk; the recipe still consumes its inputs, so the
--- player must hold them and have the skill. The player spawns indoors, so this walks a
--- spiral outward for the first square the build object itself accepts (integer coords:
--- tryBuild wants a grid square, not a position). Returns bx, by, bz or nil.
function PZH.build(p, spriteName, radius)
    ISBuildMenu.cheat = true
    local info = SpriteConfigManager.getObjectInfoFromSprite(spriteName)
    if not info then return nil end
    local be = ISBuildIsoEntity:new(p, info, 1, ISInventoryPaneContextMenu.getContainers(p))
    be.player = 0
    local px, py, pz = math.floor(p:getX()), math.floor(p:getY()), math.floor(p:getZ())
    local bx, by = nil, nil
    for r = 1, (radius or 12) do
        for dx = -r, r do for dy = -r, r do
            if (math.abs(dx) == r or math.abs(dy) == r) and bx == nil then
                local sq = getCell():getGridSquare(px + dx, py + dy, pz)
                local okSq, usable = pcall(function() return sq and sq:isOutside() and sq:isFree(false) and not sq:isSolid() end)
                if okSq and usable then
                    local okV, valid = pcall(function() return be:isValid(sq) end)
                    if okV and valid then bx, by = px + dx, py + dy end
                end
            end
        end end
        if bx then break end
    end
    if not bx then bx, by = px + 1, py end
    be:tryBuild(bx, by, pz)
    return bx, by, pz, be
end

--- UI -------------------------------------------------------------------------------
--- The loot window lists containers within the player's 3x3 and canReachTo; a player
--- teleported onto a tile edge (integer x) gets pushed a tile over and the container drops
--- out of the list, so stand at tile centres (+0.5). rect = {x,y,w,h} to lay the window out
--- for a picture; the page sizes its pane from its own height in refreshBackpacks.
--- Vanilla draws per-row detail (its Cooking bar, a mod's bar) on expanded rows only.
--- Returns true when the pane shows the container.
function PZH.showLoot(container, rect, hideInventory)
    local page = getPlayerLoot(0)
    if not page then return false end
    if hideInventory then
        pcall(function() getPlayerInventory(0):setVisible(false) end)
    else
        pcall(function() local inv = getPlayerInventory(0); inv:setVisible(true); inv.pin = true; inv.isCollapsed = false end)
    end
    pcall(function()
        page:setVisible(true); page.pin = true; page.isCollapsed = false
        if page.collapseCounter then page.collapseCounter = 0 end
        if rect then page:setX(rect.x); page:setY(rect.y); page:setWidth(rect.w); page:setHeight(rect.h) end
        page:refreshBackpacks()
        page:selectButtonForContainer(container)
        local pane = page.inventoryPane
        if pane and pane.inventory ~= container then pane.inventory = container; pane:refreshContainer() end
        if pane and pane.collapsed then
            local items = container:getItems()
            for i = 0, items:size() - 1 do
                local it = items:get(i)
                pane.collapsed[it:getName()] = false
                pane.collapsed[it:getDisplayName()] = false
            end
        end
    end)
    local pane = page and page.inventoryPane
    return pane ~= nil and pane.inventory == container
end
--- The page's update() runs while hidden and re-applies the selected container's orange
--- outline every frame (updateContainerHighlight), and un-collapses a pinned page. Unpin
--- and collapse it, and the next update clears the object itself.
function PZH.deselectLoot(obj)
    pcall(function() local page = getPlayerLoot(0); page.pin = false; page.isCollapsed = true; page.collapseCounter = 999 end)
    if obj then pcall(function() obj:setHighlighted(0, false); obj:setOutlineHighlight(0, false); obj:setOutlineHlAttached(0, false) end) end
end
function PZH.hideUI(obj)
    pcall(function() getPlayerInventory(0):setVisible(false) end)
    pcall(function() getPlayerLoot(0):setVisible(false) end)
    PZH.deselectLoot(obj)
end
--- a forced item tooltip at a fixed spot (setCharacter is required or DoTooltip errors)
function PZH.showTooltip(p, item, x, y)
    PZH.hideTooltip()
    pcall(function()
        local tip = ISToolTipInv:new(item)
        tip:initialise(); tip:setVisible(true); tip:addToUIManager()
        tip:setCharacter(p)
        tip.followMouse = false
        tip:setX(x); tip:setY(y)
        PZH.tip = tip
    end)
end
function PZH.hideTooltip()
    if PZH.tip then pcall(function() PZH.tip:removeFromUIManager() end); PZH.tip = nil end
end
