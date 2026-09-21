-- Client test template. Copy into your mod's tests/ folder, rename the tag and world, and
-- replace the steps. Run: pzh client --mod <mod> --id <ModId> --test tests/client/this.lua
require "Harness/PZHarnessClient"

PZH.configure({ tag = "[MYMOD]", world = "mymodtest" })
PZH.bootSandbox()

local T = {}
local steps = {}

steps.idle = function(S)                       -- every tick until the player exists
    local p = PZH.player()
    if not p or not p:getSquare() then return end
    T.player, T.inv = p, p:getInventory()
    PZH.unpause()
    -- give materials, set skills, then act through the real paths:
    -- PZH.give(T.inv, { "Base.Hammer", "Base.Plank", "Base.Plank", "Base.Nails", "Base.Nails" })
    -- PZH.setPerk(p, Perks.Woodwork, 3)
    -- local bx, by, bz = PZH.build(p, "my_sprite_01_0")
    -- T.square = getCell():getGridSquare(bx, by, bz)
    -- PZH.standAt(p, bx - 0.5, by + 0.5, bz)
    PZH.try("something true about the mod", function() return true, "extra detail" end)
    PZH.phase("finished")
end

-- steps.built = function(S)
--     local obj = PZH.findBySprite(T.square, "my_sprite_01_0")
--     if obj then PZH.report("real build action produced the object", true, "after " .. S.ticks .. " ticks"); PZH.phase("finished")
--     elseif S.ticks > 1200 then PZH.report("real build action produced the object", false); PZH.phase("finished") end
-- end

steps.finished = PZH.finish                    -- RESULT line, DONE, quit

PZH.run(steps)
