-- Only needed if you leave SpawnPoint=0,0,0 in the ini and want players to
-- pick the base from the spawn list instead. Put this in Zomboid/Server/
-- and rename it to <your server name>_spawnregions.lua.
function SpawnRegions()
    return {
        { name = "Muldraugh - Police Station (Soldiers Left Behind)",
          file = "media/maps/Muldraugh - Police Station (Soldiers Left Behind)/spawnpoints.lua" },
        { name = "Muldraugh, KY", file = "media/maps/Muldraugh, KY/spawnpoints.lua" },
    }
end
