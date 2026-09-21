-- PZ Harness client self-test: the library proving itself on vanilla objects, no mod
-- involved. Builds a vanilla wooden crate through the real build action, moves steaks in
-- and out with real transfer actions (one cancelled mid-way), opens the loot window on
-- it, forces a tooltip, and takes a presentable picture. [PZHSELF] PASS/FAIL.
require "Harness/PZHarnessClient"

PZH.configure({ tag = "[PZHSELF]", world = "pzhself", housekeeping = { zombies = true, devicesRadius = 40 } })
PZH.bootSandbox()

local CRATE = "carpentry_01_19"            -- Wood_Crate_Lvl1: hammer + 4 planks + 4 nails, Carpentry 3
local isSteak = PZH.byType("Base.Steak")
local T = {}
local steps = {}

local function steaks() return PZH.count(isSteak, { T.inv, T.c }, T.player) end

steps.idle = function(S)
    local p = PZH.player()
    if not p or not p:getSquare() then return end
    T.player, T.inv = p, p:getInventory()
    PZH.unpause()
    PZH.godMode(p)
    PZH.daylight()
    PZH.zoomIn(1)
    PZH.clearLoose(T.inv)
    PZH.wear(p, { "Base.Shirt_Lumberjack", "Base.Trousers_Denim", "Base.Shoes_Black" })
    PZH.give(T.inv, { "Base.Steak", "Base.Steak", "Base.Steak", "Base.Hammer" })
    for _ = 1, 4 do T.inv:AddItem("Base.Plank") end
    for _ = 1, 4 do T.inv:AddItem("Base.Nails") end
    PZH.setPerk(p, Perks.Woodwork, 3)
    PZH.try("player starts with 3 steaks", function() return steaks() == 3 end)
    PZH.note("devices silenced: " .. PZH.silenceDevices(40))
    local bx, by, bz = PZH.build(p, CRATE)
    PZH.try("build menu knows the vanilla crate sprite", function() return bx ~= nil end)
    if not bx then PZH.phase("finished") return end
    T.square = getCell():getGridSquare(bx, by, bz)
    PZH.standAt(p, bx - 0.5, by + 0.5, bz)
    PZH.note("real build action queued for " .. bx .. "," .. by)
    PZH.phase("building")
end

steps.building = function(S)
    local obj = PZH.findBySprite(T.square, CRATE)
    if obj then
        T.obj, T.c = obj, obj:getContainer()
        PZH.report("real build action produced the crate", true, "after " .. S.ticks .. " ticks")
        PZH.try("the crate has a container", function() return T.c ~= nil end)
        if not T.c then PZH.phase("finished") return end
        PZH.queueTransfers(T.player, PZH.collect(T.inv, isSteak), T.inv, T.c)
        PZH.phase("loading")
    elseif S.ticks > 1200 then
        PZH.report("real build action produced the crate", false, "nothing on the square after " .. S.ticks .. " ticks")
        PZH.phase("finished")
    end
end

steps.loading = function(S)
    if #PZH.collect(T.c, isSteak) == 3 then
        PZH.report("3 steaks moved in by real transfer actions, none lost or duplicated", true, "total=" .. steaks())
        T.one = PZH.first(T.c, isSteak)
        PZH.queueTransfers(T.player, { T.one }, T.c, T.inv)
        PZH.phase("cancelling")
    elseif S.ticks > 900 then
        PZH.report("3 steaks moved in by real transfer actions", false, "in crate=" .. #PZH.collect(T.c, isSteak) .. " total=" .. steaks())
        PZH.phase("finished")
    end
end

steps.cancelling = function(S)
    if S.ticks == 6 then
        PZH.cancelActions(T.player)
    elseif S.ticks == 40 then
        PZH.try("cancelled transfer: steak still in the crate, total intact", function()
            return T.c:contains(T.one) and steaks() == 3, "inCrate=" .. tostring(T.c:contains(T.one)) .. " total=" .. steaks()
        end)
        PZH.queueTransfers(T.player, { T.one }, T.c, T.inv)
        PZH.phase("out")
    end
end

steps.out = function(S)
    if T.inv:contains(T.one) then
        PZH.try("completed transfer: steak in inventory, total intact", function() return steaks() == 3 and #PZH.collect(T.c, isSteak) == 2 end)
        PZH.try("loot window reaches the crate and shows it", function() return PZH.showLoot(T.c, { x = 16, y = 24, w = 600, h = 300 }, true) end)
        PZH.showTooltip(T.player, T.one, 40, 340)
        PZH.phase("picture")
    elseif S.ticks > 900 then
        PZH.report("completed transfer", false, "timed out"); PZH.phase("finished")
    end
end

steps.picture = function(S)
    if S.ticks == 20 then PZH.shot("pzh_selftest_window.png") end
    if S.ticks == 25 then PZH.hideTooltip(); PZH.hideUI(T.obj) end
    if S.ticks == 90 then PZH.deselectLoot(T.obj); PZH.shot("pzh_selftest_world.png") end
    if S.ticks >= 100 then
        PZH.report("screenshots taken (see Screenshots/pzh_selftest_*.png)", true)
        PZH.phase("finished")
    end
end

steps.finished = PZH.finish

PZH.run(steps)
