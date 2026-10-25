-- Soldiers Left Behind: puts each new character on a clear floor tile inside
-- the station, in case the spawn point landed them in the lot outside or on
-- a tile something now stands on.

require "SoldiersLeftBehind_Shared"

local SLB = SoldiersLeftBehind

local function moveTo(player, sq)
    local x, y, z = sq:getX() + 0.5, sq:getY() + 0.5, sq:getZ()
    if pcall(function() player:teleportTo(x, y, z) end) then return end
    player:setX(x)
    player:setY(y)
    player:setZ(z)
    player:setLx(x)
    player:setLy(y)
    player:setLz(z)
end

local function place(player)
    local md = player:getModData()
    if md.SoldiersLeftBehindPlaced then return end
    -- Only a character that has just started, near the base.
    local t = SLB.target
    local far = math.abs(player:getX() - t.x) > 80 or math.abs(player:getY() - t.y) > 80
    if far or player:getHoursSurvived() > 1 then
        md.SoldiersLeftBehindPlaced = true
        return
    end
    local building = SLB.findBuilding()
    if not building then return end
    md.SoldiersLeftBehindPlaced = true
    local here = player:getCurrentSquare()
    if here and here:getBuilding() == building and here:isFree(false) then return end
    local squares = SLB.floorSquares(building, 0)
    if #squares == 0 then return end
    local pick = squares[ZombRand(math.min(6, #squares)) + 1]
    moveTo(player, pick)
    SLB.log(string.format("moved %s inside the station to %d,%d", player:getUsername() or "player", pick:getX(), pick:getY()))
end

Events.OnPlayerUpdate.Add(function(player)
    if player:isLocalPlayer() then pcall(place, player) end
end)
