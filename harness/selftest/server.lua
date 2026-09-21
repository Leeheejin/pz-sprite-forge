-- PZ Harness server self-test: the library proving itself on vanilla objects, no mod
-- involved. A vanilla barrel oven on a detached square, lit with fuel, cooks a steak while
-- game minutes pass on a player-less server. [PZHSELF] PASS/FAIL.
require "Harness/PZHarnessServer"

PZH.configure({ tag = "[PZHSELF]" })

PZH.runServer(function(st)
    PZH.try("instanceItem works from Lua", function() return PZH.make("Base.Steak") ~= nil end)
    local sq = PZH.detachedSquare(6800, 5500, 0)
    PZH.try("a detached square exists where no chunk is loaded", function() return sq ~= nil and getCell():getGridSquare(6800, 5500, 0) == nil end)
    local oven = IsoFireplace.new(getCell(), sq, getSprite("crafted_05_4"))   -- vanilla barrel oven
    sq:AddTileObject(oven)
    st.oven, st.c = oven, oven:getContainer()
    PZH.try("the vanilla barrel oven carries a container", function() return st.c ~= nil end)
    st.steak = PZH.make("Base.Steak")
    st.c:AddItem(st.steak)
    st.c:addItemsToProcessItems()
    oven:addFuel(600)
    oven:setLit(true)
    st.fuel0 = oven:getFuelAmount()
    PZH.try("oven lit with fuel", function() return oven:isLit() and st.fuel0 == 600, "fuel=" .. tostring(st.fuel0) end)
end, function(st, minute)
    if minute % 5 == 0 then
        PZH.note("minute " .. minute .. ": fuel=" .. st.oven:getFuelAmount() .. " cookingTime=" .. tostring(st.steak:getCookingTime()) .. " contents=" .. PZH.dump(st.c))
    end
    if minute >= 12 then
        PZH.try("game minutes pass with no player: the fire burned fuel", function()
            local burned = st.fuel0 - st.oven:getFuelAmount()
            return burned >= 8, "burned=" .. burned .. " over " .. minute .. " minutes"
        end)
        PZH.try("the engine processes items on a detached square: the steak is cooking", function()
            return st.steak:getCookingTime() > 0, "cookingTime=" .. tostring(st.steak:getCookingTime())
        end)
        PZH.try("holds/dump see the container", function() return PZH.holds(st.c, "Base.Steak"), PZH.dump(st.c) end)
        PZH.finishServer(st)
    end
end)
