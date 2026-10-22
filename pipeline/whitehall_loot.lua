-- White Lake to Muskegon: less loot.
--
-- The map is real towns at their real density, and KnoxMap furnishes every
-- house in them: a street of fifty houses is fifty kitchens of cupboards and
-- fridges, fifty bedrooms of dressers and wardrobes. With the game's loot
-- tables filling every one, there is far more in the towns than a player can
-- carry, and none of it is scarce.
--
-- This leaves a share of what the game puts in each container on this map,
-- and takes the rest out as the container is first filled - the same moment
-- the game's own loot logging and KnoxMap's rifle cache use (OnFillContainer).
-- Containers anywhere else in the world, and anything a player puts in a
-- container, are not touched. The guaranteed rifle and its ammunition are
-- always left where they are.
--
-- KEEP is the share of each container's loot that stays: 0.35 keeps about a
-- third. Set it to 1 to have the game's own amounts back.

WhiteLakeMuskegonLoot = WhiteLakeMuskegonLoot or {}
local M = WhiteLakeMuskegonLoot

M.KEEP = 0.35
-- The map's world tiles: x from 120000 (cell 400 of 300 tiles), and the
-- frame's width and height. Filled in by the build.
M.X0, M.Y0, M.X1, M.Y1 = @X0@, @Y0@, @X1@, @Y1@

local SKIP = { inventorymale = true, inventoryfemale = true, floor = true }
local ALWAYS_KEEP = { ["Base.AssaultRifle"] = true, ["Base.556Clip"] = true,
                      ["Base.556Box"] = true }

-- The square a container sits on, the way KnoxMap's rifle cache finds it:
-- off the container's parent, inside a pcall.
local function squareOf(container)
    if container == nil or type(container) ~= "userdata" then return nil end
    local ok, square = pcall(function()
        local parent = container:getParent()
        return parent and parent:getSquare() or nil
    end)
    if ok then return square end
    return nil
end

local function onFillContainer(_roomName, _containerType, container)
    if M.KEEP >= 1 then return end
    local square = squareOf(container)
    if not square then return end
    local x, y = square:getX(), square:getY()
    if x < M.X0 or x >= M.X1 or y < M.Y0 or y >= M.Y1 then return end
    local ok, kind = pcall(function() return container:getType() end)
    if ok and kind and SKIP[kind] then return end

    local items = container:getItems()
    local doomed = {}
    for i = 0, items:size() - 1 do
        local item = items:get(i)
        if not ALWAYS_KEEP[item:getFullType()] and ZombRand(1000) >= M.KEEP * 1000 then
            table.insert(doomed, item)
        end
    end
    for _, item in ipairs(doomed) do
        local removed = pcall(function() container:DoRemoveItem(item) end)
        if not removed then
            pcall(function() container:Remove(item) end)
        end
    end
end

Events.OnFillContainer.Add(onFillContainer)
