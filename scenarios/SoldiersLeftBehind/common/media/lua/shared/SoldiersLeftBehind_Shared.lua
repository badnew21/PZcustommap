-- Soldiers Left Behind: settings and helpers used by both the server side
-- (which stocks and boards up the base) and the client side (which puts each
-- new character inside it).

SoldiersLeftBehind = SoldiersLeftBehind or {}
local SLB = SoldiersLeftBehind

-- The Muldraugh police station, inside its fenced lot. The building is found
-- by searching round this point, so it does not have to be exact.
SLB.target = { x = 10638, y = 10420, z = 0 }
SLB.searchRadius = 45
SLB.planksPerWindow = 3
SLB.crateSprites = { "carpentry_01_16", "carpentry_01_17", "carpentry_01_18", "carpentry_01_19" }

-- Each crate is a list of { item, count }. An item may give several names to
-- try in order, for items whose name differs between builds. Anything the game
-- does not know is skipped and written to console.txt.
SLB.crates = {
    {   label = "Armory",
        prefer = { "gun", "armory", "storage", "locker" },
        items = {
            { "Base.AssaultRifle", 1 },
            { "Base.Shotgun", 1 },
            { "Base.Pistol", 3 },
            { "Base.556Clip", 3 },
            { "Base.9mmClip", 6 },
            { "Base.556Box", 3 },
            { "Base.Bullets9mmBox", 4 },
            { "Base.ShotgunShellsBox", 3 },
            { "Base.HuntingKnife", 3 },
            { "Base.Machete", 1 },
            { "Base.Crowbar", 1 },
        } },
    {   label = "Soldier kits",
        prefer = { "locker", "storage" },
        items = {
            { { "Base.Bag_ALICEpack_Army", "Base.Bag_ALICEpack" }, 3 },
            { "Base.Vest_BulletArmy", 3 },
            { { "Base.Hat_Army", "Base.Hat_BalaclavaFull" }, 3 },
            { { "Base.Jacket_ArmyCamoGreen", "Base.Jacket_CamoGreen" }, 3 },
            { { "Base.Trousers_CamoGreen", "Base.Trousers_ArmyService" }, 3 },
            { { "Base.Shirt_CamoGreen", "Base.Tshirt_ArmyGreen" }, 3 },
            { { "Base.Shoes_ArmyBoots", "Base.Shoes_BlackBoots" }, 3 },
            { { "Base.Gloves_LeatherGloves", "Base.Gloves_LeatherGlovesBlack" }, 3 },
            { { "Base.HolsterSimple", "Base.HolsterDouble" }, 3 },
            { { "Base.WalkieTalkie5", "Base.WalkieTalkie4" }, 3 },
            { { "Base.Torch", "Base.HandTorch" }, 3 },
            { "Base.Battery", 9 },
            { "Base.Lighter", 3 },
            { { "Base.Bag_FannyPackFront", "Base.Bag_FannyPackBack" }, 3 },
        } },
    {   label = "Medical",
        prefer = { "office", "kitchen" },
        items = {
            { "Base.FirstAidKit", 1 },
            { "Base.Bandage", 15 },
            { "Base.AlcoholWipes", 6 },
            { "Base.Disinfectant", 2 },
            { "Base.Pills", 2 },
            { "Base.PillsAntiDep", 1 },
            { "Base.PillsBeta", 1 },
            { "Base.PillsVitamins", 2 },
            { "Base.SutureNeedle", 3 },
            { "Base.Tweezers", 1 },
            { "Base.Splint", 2 },
        } },
    {   label = "Rations",
        prefer = { "kitchen", "break", "storage" },
        items = {
            { "Base.TinnedBeans", 8 },
            { "Base.CannedChili", 6 },
            { "Base.CannedCorn", 6 },
            { "Base.CannedPeas", 6 },
            { "Base.CannedBolognese", 4 },
            { "Base.TunaTin", 6 },
            { "Base.TinnedSoup", 6 },
            { "Base.Crackers", 4 },
            { "Base.GranolaBar", 8 },
            { "Base.Peanuts", 4 },
            { "Base.Rice", 2 },
            { "Base.Pasta", 2 },
            { "Base.TinOpener", 2 },
            { { "Base.Pot", "Base.Saucepan" }, 1 },
        } },
    {   label = "Water",
        prefer = { "kitchen", "break", "storage" },
        items = {
            { "Base.WaterBottle", 12, "water" },
            { { "Base.WaterPot", "Base.Pot" }, 1, "water" },
        } },
    {   label = "Tools",
        prefer = { "storage", "garage" },
        items = {
            { "Base.Hammer", 2 },
            { "Base.Saw", 1 },
            { "Base.Screwdriver", 1 },
            { "Base.NailsBox", 3 },
            { "Base.Plank", 12 },
            { "Base.Sledgehammer", 1 },
            { "Base.Axe", 1 },
            { "Base.DuctTape", 2 },
            { "Base.Rope", 2 },
            { "Base.SheetRope", 4 },
            { "Base.EmptyPetrolCan", 2 },
            { "Base.PetrolCan", 2, "petrol" },
        } },
}

-- Left on the floor beside the crates: too heavy for a crate.
SLB.floorItems = {
    { "Base.Generator", 1 },
}

function SLB.log(msg)
    print("[SoldiersLeftBehind] " .. tostring(msg))
end

-- The first name in a list the game knows, or nil.
function SLB.itemType(names)
    if type(names) ~= "table" then names = { names } end
    for _, name in ipairs(names) do
        if getScriptManager():FindItem(name) then return name end
    end
    return nil
end

-- Fill an item that holds liquid (Build 42 fluid containers, or the old
-- WaterBottleFull-style items, which come full anyway).
function SLB.fill(item, what)
    if not what or not item.getFluidContainer then return end
    pcall(function()
        local fc = item:getFluidContainer()
        if not fc then return end
        local fluid = what == "petrol" and FluidType.Petrol or FluidType.Water
        fc:addFluid(fluid, fc:getCapacity() - fc:getAmount())
    end)
end

local function roomName(sq)
    local room = sq and sq:getRoom()
    if not room then return "" end
    local ok, name = pcall(function() return room:getName() end)
    return ok and name and string.lower(name) or ""
end
SLB.roomName = roomName

-- The building to use as the base: the nearest one with a police room, or
-- failing that the nearest building at all. Returns nil until the squares
-- round the target have loaded.
function SLB.findBuilding()
    local cell = getCell()
    local t = SLB.target
    if not cell:getGridSquare(t.x, t.y, t.z) then return nil end
    local best, bestD, police, policeD
    for r = 0, SLB.searchRadius do
        for dx = -r, r do
            for dy = -r, r do
                if math.abs(dx) == r or math.abs(dy) == r then
                    local sq = cell:getGridSquare(t.x + dx, t.y + dy, t.z)
                    local b = sq and sq:getBuilding()
                    if b then
                        local d = dx * dx + dy * dy
                        if not best or d < bestD then best, bestD = b, d end
                        if string.find(roomName(sq), "police", 1, true) and (not police or d < policeD) then
                            police, policeD = b, d
                        end
                    end
                end
            end
        end
        if police and r * r > policeD then break end
    end
    return police or best
end

-- The building's bounding box on the tile grid.
function SLB.bounds(building)
    local def = building:getDef()
    return def:getX(), def:getY(), def:getX() + def:getW() - 1, def:getY() + def:getH() - 1
end

-- True when every ground-floor square of the building is loaded.
function SLB.loaded(building)
    local cell = getCell()
    local x1, y1, x2, y2 = SLB.bounds(building)
    for x = x1 - 1, x2 + 1 do
        for y = y1 - 1, y2 + 1 do
            if not cell:getGridSquare(x, y, 0) then return false end
        end
    end
    return true
end

-- Clear floor squares inside the building on a level, nearest the target first.
function SLB.floorSquares(building, z)
    local cell = getCell()
    local x1, y1, x2, y2 = SLB.bounds(building)
    local out = {}
    for x = x1, x2 do
        for y = y1, y2 do
            local sq = cell:getGridSquare(x, y, z)
            if sq and sq:getBuilding() == building and sq:isFree(false) and not sq:getZombie() then
                local blocked = false
                local objs = sq:getObjects()
                for i = 0, objs:size() - 1 do
                    local o = objs:get(i)
                    if instanceof(o, "IsoDoor") or instanceof(o, "IsoWindow") or o:getContainer() then
                        blocked = true
                        break
                    end
                end
                if not blocked then table.insert(out, sq) end
            end
        end
    end
    local t = SLB.target
    table.sort(out, function(a, b)
        local da = (a:getX() - t.x) ^ 2 + (a:getY() - t.y) ^ 2
        local db = (b:getX() - t.x) ^ 2 + (b:getY() - t.y) ^ 2
        return da < db
    end)
    return out
end
