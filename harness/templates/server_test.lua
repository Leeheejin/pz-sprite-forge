-- Server test template. Copy into your mod's tests/ folder, rename the tag, and replace the
-- setup and poll functions. Run: pzh server --mod <mod> --id <ModId> --test tests/server/this.lua
require "Harness/PZHarnessServer"

PZH.configure({ tag = "[MYTEST]" })

PZH.runServer(function(st)                     -- once, on OnServerStarted
    -- local sq = PZH.detachedSquare(6800, 5500, 0)
    -- st.obj = PZH.placeSprite(sq, "my_sprite_01_0")
    PZH.try("an item of the mod instantiates", function() return PZH.make("Base.Steak") ~= nil end)
end, function(st, minute)                      -- every game minute
    if minute >= 3 then
        PZH.try("something true after 3 game minutes", function() return true end)
        PZH.finishServer(st)                   -- RESULT line, DONE
    end
end)
