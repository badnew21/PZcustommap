-- Soldiers Left Behind: sets up the base once per world. Runs on a dedicated
-- server and in single player (which runs server Lua too).
--
-- Once the police station's squares are loaded it clears out the zombies in
-- and around the building, boards up the ground-floor windows from the inside,
-- locks the outside doors, and puts down the supply crates, a generator and a
-- note. A flag in the world's mod data stops it running again.

require "SoldiersLeftBehind_Shared"

local SLB = SoldiersLeftBehind
if isClient() then return end

local function state()
    return ModData.getOrCreate("SoldiersLeftBehind")
end

local function inBuilding(sq, building)
    return sq ~= nil and sq:getBuilding() == building
end

-- The square on the other side of a window or door's wall.
local function otherSide(obj)
    local ok, sq = pcall(function() return obj:getOppositeSquare() end)
    if ok and sq then return sq end
    local s = obj:getSquare()
    if obj:getNorth() then
        return getCell():getGridSquare(s:getX(), s:getY() - 1, s:getZ())
    end
    return getCell():getGridSquare(s:getX() - 1, s:getY(), s:getZ())
end

local function addBarricade(obj, building)
    -- Put the boards on the inside face: the opposite square when the
    -- window's own square is the outside one.
    local opposite = not inBuilding(obj:getSquare(), building)
    local b
    pcall(function() b = IsoBarricade.AddBarricadeToObject(obj, opposite) end)
    if not b then return false end
    for _ = 1, SLB.planksPerWindow do
        pcall(function()
            if b:canAddPlank() then b:addPlank(nil, nil) end
        end)
    end
    if isServer() then pcall(function() b:transmitCompleteItemToClients() end) end
    return true
end

-- Windows and doors in the building's outer walls, on the ground floor.
local function outerOpenings(building)
    local cell = getCell()
    local x1, y1, x2, y2 = SLB.bounds(building)
    local windows, doors, seen = {}, {}, {}
    for x = x1 - 1, x2 + 1 do
        for y = y1 - 1, y2 + 1 do
            local sq = cell:getGridSquare(x, y, 0)
            if sq then
                local objs = sq:getObjects()
                for i = 0, objs:size() - 1 do
                    local o = objs:get(i)
                    local isWindow = instanceof(o, "IsoWindow")
                    local isDoor = instanceof(o, "IsoDoor")
                    if (isWindow or isDoor) and not seen[o] then
                        seen[o] = true
                        local a, b = inBuilding(sq, building), inBuilding(otherSide(o), building)
                        if a ~= b then
                            table.insert(isWindow and windows or doors, o)
                        end
                    end
                end
            end
        end
    end
    return windows, doors
end

local function clearZombies(building)
    local x1, y1, x2, y2 = SLB.bounds(building)
    local margin = 8   -- the fenced lot round the station
    local list = getCell():getZombieList()
    local gone = 0
    for i = list:size() - 1, 0, -1 do
        local z = list:get(i)
        local zx, zy = z:getX(), z:getY()
        if zx >= x1 - margin and zx <= x2 + margin and zy >= y1 - margin and zy <= y2 + margin then
            pcall(function()
                z:removeFromWorld()
                z:removeFromSquare()
            end)
            gone = gone + 1
        end
    end
    return gone
end

local function crateSprite()
    for _, name in ipairs(SLB.crateSprites) do
        if getSprite(name) then return name end
    end
    return nil
end

-- Add one crate's contents to a container, or to the floor if there is none.
local function stock(container, sq, items)
    for _, entry in ipairs(items) do
        local itemType = SLB.itemType(entry[1])
        if itemType then
            for _ = 1, entry[2] do
                local item
                if container then
                    item = container:AddItem(itemType)
                else
                    item = sq:AddWorldInventoryItem(itemType, ZombRandFloat(0.2, 0.8), ZombRandFloat(0.2, 0.8), 0)
                end
                if item then SLB.fill(item, entry[3]) end
            end
        else
            local names = type(entry[1]) == "table" and table.concat(entry[1], " / ") or entry[1]
            SLB.log("no such item in this build, skipped: " .. names)
        end
    end
end

local function placeCrate(sq, items)
    local sprite = crateSprite()
    local crate
    if sprite then
        pcall(function()
            crate = IsoThumpable.new(getCell(), sq, sprite, false, nil)
            crate:setIsContainer(true)
            crate:getContainer():setType("crate")
            sq:AddSpecialObject(crate)
        end)
    end
    if crate and crate:getContainer() then
        stock(crate:getContainer(), sq, items)
        pcall(function() crate:getContainer():setExplored(true) end)
        if isServer() then crate:transmitCompleteItemToClients() end
        return crate
    end
    SLB.log("could not place a crate at " .. sq:getX() .. "," .. sq:getY() .. "; leaving the items on the floor")
    stock(nil, sq, items)
end

local ORDERS = "ORDERS - 2nd Bn, Knox Event response\n\n"
    .. "Evac is cancelled. The convoy could not wait for the three of you.\n"
    .. "This station is your post now. We boarded the ground floor windows and\n"
    .. "locked the doors. Unlock one from the inside when you need to go out.\n\n"
    .. "Left for you: rifle, shotgun, three sidearms and ammunition, three kits,\n"
    .. "rations, water, medical, tools and a generator with fuel.\n\n"
    .. "Hold out. Nobody is coming. Good luck."

local function leaveOrders(sq)
    local itemType = SLB.itemType({ "Base.Notebook", "Base.SheetPaper2" })
    if not itemType then return end
    local note = sq:AddWorldInventoryItem(itemType, 0.5, 0.5, 0)
    if not note then return end
    pcall(function() note:setName("Orders") end)
    pcall(function() note:setCustomName(true) end)
    pcall(function() note:addPage(1, ORDERS) end)
end

-- Take the best square for a crate: one in a room whose name has one of the
-- wanted words, or else the next free one.
local function takeSquare(squares, prefer)
    for _, word in ipairs(prefer or {}) do
        for i, sq in ipairs(squares) do
            if string.find(SLB.roomName(sq), word, 1, true) then
                return table.remove(squares, i)
            end
        end
    end
    return table.remove(squares, 1)
end

local function setUp(building)
    local x1, y1, x2, y2 = SLB.bounds(building)
    SLB.log(string.format("setting up the base in the building at %d,%d-%d,%d", x1, y1, x2, y2))

    local gone = clearZombies(building)
    local windows, doors = outerOpenings(building)
    local boarded = 0
    for _, w in ipairs(windows) do
        pcall(function() w:setIsLocked(true) end)
        if addBarricade(w, building) then boarded = boarded + 1 end
    end
    for _, d in ipairs(doors) do
        pcall(function()
            if d:IsOpen() then d:ToggleDoorSilent() end
            d:setLockedByKey(true)
            d:setIsLocked(true)
        end)
        if isServer() then pcall(function() d:transmitCompleteItemToClients() end) end
    end

    local squares = SLB.floorSquares(building, 0)
    -- Keep the squares nearest the spawn point free for the players.
    for _ = 1, math.min(3, #squares - #SLB.crates - 1) do table.remove(squares, 1) end
    local crates = 0
    for _, def in ipairs(SLB.crates) do
        local sq = takeSquare(squares, def.prefer)
        if sq then
            placeCrate(sq, def.items)
            crates = crates + 1
            SLB.log(string.format("%s crate at %d,%d (%s)", def.label, sq:getX(), sq:getY(), SLB.roomName(sq)))
        end
    end
    local floor = takeSquare(squares, { "storage", "garage" })
    if floor then
        stock(nil, floor, SLB.floorItems)
        leaveOrders(floor)
    end

    SLB.log(string.format("done: %d zombies removed, %d of %d windows boarded, %d doors locked, %d crates",
        gone, boarded, #windows, #doors, crates))
end

local function tick()
    local data = state()
    if data.setUp then
        Events.EveryOneMinute.Remove(tick)
        return
    end
    local building = SLB.findBuilding()
    if not building or not SLB.loaded(building) then return end
    data.setUp = true
    local ok, err = pcall(setUp, building)
    if not ok then SLB.log("setup stopped with an error: " .. tostring(err)) end
end

Events.EveryOneMinute.Add(tick)
