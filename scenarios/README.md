# Soldiers Left Behind

A Build 42 scenario mod for vanilla Knox County: three soldiers left behind
when the evacuation pulled out, starting in a base that their unit fortified
and stocked before it left.

## The base: Muldraugh Police Station

The police station in Muldraugh, around tile 10638, 10420. Reasons for picking it:

- **It's fenced.** The lot has a fence round it, so you have a yard to work in and
  zombies have to come through one gap.
- **It's a strong building.** Brick walls, few ground-floor windows, an armory and
  holding cells.
- **It's central.** Muldraugh is the most-looted town in Knox County, the highway to
  Louisville and West Point is close by, and there are grocery stores,
  hardware, the gas station and the warehouses within a short drive.

## What the mod does

On the first game day, once a player is close enough for the station to load, the
server does all of this once for the world:

- Removes the zombies inside the station and in its lot.
- Boards up every ground-floor window in the outer walls from the inside, three
  planks each, and locks them.
- Closes and locks the outside doors. You can unlock one from the inside when you
  want to go out.
- Puts down six crates:

  | Crate | Contents |
  |---|---|
  | Armory | M16 rifle, shotgun, 3 pistols, 3 rifle mags, 6 pistol mags, 3 boxes 5.56, 4 boxes 9mm, 3 boxes shells, 3 hunting knives, machete, crowbar |
  | Soldier kits | Three of each: ALICE pack, bulletproof vest, army helmet, camo jacket, trousers and shirt, army boots, gloves, holster, military walkie-talkie, flashlight, fanny pack, lighter. Also 9 batteries. |
  | Medical | First aid kit, 15 bandages, alcohol wipes, disinfectant, painkillers, beta blockers, antidepressants, vitamins, suture needles, tweezers, splints |
  | Rations | About 60 cans and snacks, plus rice, pasta, can openers and a pot |
  | Water | 12 full water bottles and a full pot |
  | Tools | 2 hammers, saw, screwdriver, 3 boxes of nails, 12 planks, sledgehammer, axe, duct tape, rope, sheet rope, 2 full gas cans and 2 empty ones |

- Leaves a generator and a notebook called "Orders" on the floor.

Each new character is then moved onto a clear tile inside the station. Nobody
starts outside in the lot or stuck on furniture.

There's enough here for a fair start, but not so much that you can stay inside
forever. Food lasts the three of you a couple of weeks. After that you have to go
out and loot.

To change the loadout, edit the lists at the top of
`common/media/lua/shared/SoldiersLeftBehind_Shared.lua`. If an item name doesn't
exist in your build, the mod skips it and logs a
`[SoldiersLeftBehind] ... skipped` line in `console.txt`.

## Installing

Copy the `SoldiersLeftBehind` folder (or unzip `release/SoldiersLeftBehind.zip`)
into `Zomboid/mods/`:

- Windows: `C:\Users\<you>\Zomboid\mods\`
- Linux / Mac: `~/Zomboid/mods/`

Every player needs the mod in their own `Zomboid/mods/` folder, and so does the
server, because it isn't on the Workshop.

### Hosting from the game (Host button)

1. Main menu → **Host** → **Manage settings** → make a new settings file.
2. **Mods**: turn on *Soldiers Left Behind*.
3. **Map**: Muldraugh, KY (the default).
4. Save the settings and close the game. Open
   `Zomboid/Server/<settings name>.ini` and set `SpawnPoint=10638,10420,0`.
   The Host button reads the same file, so this sends everyone to the station.
5. Host the game, and have your two friends join.

### Dedicated server

Set these lines in `Zomboid/Server/<server name>.ini` (`servertest.ini` by
default). They're also in `server/servertest_settings.ini`.

```ini
Mods=\SoldiersLeftBehind
WorkshopItems=
Map=Muldraugh, KY
SpawnPoint=10638,10420,0
MaxPlayers=4
PVP=false
```

`Mods=` needs the backslash on Build 42. On Build 41 it's
`Mods=SoldiersLeftBehind`. `SpawnPoint` sends everyone to the station and skips
the spawn list. If you'd rather keep the list, leave `SpawnPoint=0,0,0` and copy
`server/servertest_spawnregions.lua` into `Zomboid/Server/` as
`<server name>_spawnregions.lua`.

### Single player or split screen

Turn the mod on under **Mods**, start a new game in Muldraugh, and choose the
*Muldraugh - Police Station (Soldiers Left Behind)* spawn region.

### Suggested sandbox settings

These are optional, for the "left behind" feel:

- **Start**: 0 days. The army has only just pulled out.
- **Water and electricity shut off**: 0–30 days, so the generator matters later.
- **Zombie population**: normal or high. You're well armed, and Muldraugh is busy.
- **Loot**: normal. You have a head start, so you don't need extra.
- **Professions**: anything works. A veteran, or the *Brave* and
  *Desensitized* traits, fits the story.

## Things to know

- **Test it once before game night.** The setup runs on the first in-game minute
  after the station has loaded. Check `Zomboid/console.txt` (or the server's own
  console) for `[SoldiersLeftBehind] done: ...` to see how many
  windows were boarded and where each crate went.
- **The building is found by searching, not hard-coded.** The mod looks within 45
  tiles of 10638, 10420 for the nearest building with a police room. If a game
  update moves the station a few tiles, the mod still finds it.
- **It runs once per world.** For a fresh base, start a new world. Wiping just the
  players isn't enough.
- **Untested in the real game.** It was written without a copy of the game here and
  checked against a mock of the game's Lua API. Lua calls that might differ
  between builds are guarded, so a failure is skipped and logged instead of
  breaking the setup. Barricades, door locks and the move inside are the parts
  most likely to need a fix after the first run.
