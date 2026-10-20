"""Overture Maps as KnoxMap features, for a town.

KnoxMap reads OpenStreetMap from Overpass. Where Overpass cannot be reached,
Overture Maps has the same data on S3: OpenStreetMap's land use, water and
infrastructure with their original tags (`source_tags`), and roads, rail,
buildings and businesses in Overture's own schema. This turns all of it into
KnoxMap's `OSMFeature`s with OSM tags, so KnoxMap's classifier, renderer and
knoxbuild treat it exactly as they would a download from Overpass.

What has to be translated rather than copied:

* roads and rail: Overture's class becomes `highway=*` / `railway=*`, and a
  segment is cut where Overture marks part of it as a bridge, a tunnel or a
  different level, so overpasses and underpasses come out as KnoxMap draws
  them from OSM;
* buildings: class, name, height and storeys;
* businesses: Overture's category becomes the amenity/shop/office tag knoxbuild
  furnishes a building by (a pizza place gets a pizza kitchen);
* a Great Lake: only the part of it inside the map.
"""
from __future__ import annotations

import os

import pyarrow.parquet as pq
import shapely
from shapely import wkb
from shapely.ops import substring

import isle  # noqa: F401  (KnoxMap on the path)
from generator.osm import OSMFeature

HIGHWAY = {"motorway": "motorway", "trunk": "trunk", "primary": "primary",
           "secondary": "secondary", "tertiary": "tertiary", "residential": "residential",
           "living_street": "living_street", "unclassified": "unclassified",
           "service": "service", "pedestrian": "pedestrian", "footway": "footway",
           "steps": "steps", "cycleway": "cycleway", "path": "path", "track": "track",
           "bridleway": "bridleway", "unknown": "road"}
FOOTWAY_SUBCLASS = {"sidewalk": "sidewalk", "crosswalk": "crossing"}
SERVICE_SUBCLASS = {"alley", "driveway", "parking_aisle"}
MIN_PLACE_CONFIDENCE = 0.6

# Overture place categories, by the words in them, to the OSM tags knoxbuild
# knows how to furnish (knoxbuild/uses.py). First match wins, so the specific
# go before the general.
CATEGORY_RULES = [
    (("pizza",), {"amenity": "restaurant", "cuisine": "pizza"}),
    (("italian_restaurant",), {"amenity": "restaurant", "cuisine": "italian"}),
    (("chinese_restaurant",), {"amenity": "restaurant", "cuisine": "chinese"}),
    (("fast_food", "burger", "sandwich", "hot_dog", "chicken_wings"), {"amenity": "fast_food"}),
    (("coffee", "cafe", "tea_room", "bubble_tea"), {"amenity": "cafe"}),
    (("ice_cream", "frozen_yogurt", "dessert"), {"amenity": "ice_cream"}),
    (("restaurant", "diner", "bistro", "eatery", "steakhouse", "sushi", "taco"),
     {"amenity": "restaurant"}),
    (("barber", "hair"), {"shop": "hairdresser"}),
    (("massage",), {"shop": "massage"}),
    (("pub",), {"amenity": "pub"}),
    (("bar", "brewery", "lounge", "nightlife"), {"amenity": "bar"}),
    (("bakery",), {"shop": "bakery"}),
    (("supermarket", "grocery"), {"shop": "supermarket"}),
    (("convenience",), {"shop": "convenience"}),
    (("liquor", "wine_store", "beer_store"), {"shop": "alcohol"}),
    (("butcher",), {"shop": "butcher"}),
    (("deli",), {"shop": "deli"}),
    (("pharmacy", "drugstore"), {"amenity": "pharmacy"}),
    (("bank", "credit_union"), {"amenity": "bank"}),
    (("veterinar", "animal_hospital"), {"amenity": "veterinary"}),
    (("dentist", "dental", "orthodont"), {"amenity": "dentist"}),
    (("hospital", "emergency", "medical_center", "urgent_care", "clinic"), {"amenity": "clinic"}),
    (("doctor", "physician", "medicine", "ologist", "pediatric", "surgeon", "chiropract",
      "therap", "psychiatr", "health"), {"amenity": "doctors"}),
    (("library",), {"amenity": "library"}),
    (("post_office",), {"amenity": "post_office"}),
    (("police",), {"amenity": "police"}),
    (("movie", "cinema"), {"amenity": "cinema"}),
    (("theater", "theatre", "performing_arts"), {"amenity": "theatre"}),
    (("museum",), {"tourism": "museum"}),
    (("art_gallery",), {"tourism": "gallery"}),
    (("child_care", "day_care", "preschool"), {"amenity": "childcare"}),
    (("gas_station", "fuel"), {"amenity": "fuel"}),
    (("auto_parts",), {"shop": "car_parts"}),
    (("automotive", "auto_body", "car_repair", "tire_shop"), {"shop": "car_repair"}),
    (("nail", "beauty", "spa", "cosmetic", "skin_care"), {"shop": "beauty"}),
    (("tattoo",), {"shop": "tattoo"}),
    (("shoe",), {"shop": "shoes"}),
    (("clothing", "fashion", "apparel", "boutique"), {"shop": "clothes"}),
    (("department_store",), {"shop": "department_store"}),
    (("jewelry",), {"shop": "jewelry"}),
    (("book", "bookstore"), {"shop": "books"}),
    (("flower", "florist"), {"shop": "florist"}),
    (("gift",), {"shop": "gift"}),
    (("furniture",), {"shop": "furniture"}),
    (("hardware", "home_improvement"), {"shop": "hardware"}),
    (("mobile_phone",), {"shop": "mobile_phone"}),
    (("computer",), {"shop": "computer"}),
    (("electronics",), {"shop": "electronics"}),
    (("bicycle", "bike"), {"shop": "bicycle"}),
    (("sporting_goods", "outdoor_gear"), {"shop": "sports"}),
    (("toy", "game_store", "hobby"), {"shop": "toys"}),
    (("music", "record_store"), {"shop": "music"}),
    (("thrift", "second_hand", "antique", "variety"), {"shop": "variety_store"}),
    (("laundromat", "laundry"), {"shop": "laundry"}),
    (("dry_clean",), {"shop": "dry_cleaning"}),
    (("gym", "fitness", "yoga", "pilates", "martial_arts"), {"leisure": "fitness_centre"}),
    (("dance",), {"leisure": "dance"}),
    (("hotel", "inn"), {"tourism": "hotel"}),
    (("motel",), {"tourism": "motel"}),
    (("hostel",), {"tourism": "hostel"}),
    (("bed_and_breakfast", "guest_house"), {"tourism": "guest_house"}),
    (("real_estate", "insurance", "lawyer", "attorney", "accountant", "financial",
      "professional_services", "corporate_office", "consultant", "agency"), {"office": "company"}),
]


def _name(r):
    n = r.get("names")
    return (n or {}).get("primary") if isinstance(n, dict) else None


def _tags(r) -> dict:
    st = r.get("source_tags")
    if isinstance(st, list):
        return {k: v for k, v in st}
    return dict(st) if isinstance(st, dict) else {}


def _matches(word: str, cat: str) -> bool:
    """Whether a rule's word names this category. Whole words only, so "bar"
    is not "barber" and "deli" is not "delivery"; a longer stem may start or
    end a word ("therap" in "physical_therapy", "ologist" in "neurologist")."""
    if "_" in word:
        return word in cat
    tokens = cat.split("_")
    if word in tokens:
        return True
    return len(word) >= 5 and any(t.startswith(word) or t.endswith(word) for t in tokens)


def place_tags(r) -> dict | None:
    """The OSM tags of an Overture place, or None for one knoxbuild has no use for."""
    if (r.get("confidence") or 0) < MIN_PLACE_CONFIDENCE:
        return None
    if r.get("operating_status") not in (None, "open"):
        return None
    cat = ((r.get("categories") or {}).get("primary") or r.get("basic_category") or "").lower()
    if not cat:
        return None
    for words, tags in CATEGORY_RULES:
        if any(_matches(w, cat) for w in words):
            out = dict(tags)
            if _name(r):
                out["name"] = _name(r)
            return out
    return None


def _pieces(line, r):
    """The parts of a road or rail segment and what each is: (line, level, flags).
    Overture gives levels and bridge/tunnel flags along a fraction of a segment."""
    cuts = {0.0, 1.0}
    spans = []
    for rule in r.get("level_rules") or []:
        b = rule.get("between") or [0.0, 1.0]
        cuts.update(b)
        spans.append(("level", rule.get("value") or 0, b))
    for rule in r.get("road_flags") or []:
        b = rule.get("between") or [0.0, 1.0]
        cuts.update(b)
        for v in rule.get("values") or []:
            spans.append(("flag", v, b))
    for rule in r.get("rail_flags") or []:
        b = rule.get("between") or [0.0, 1.0]
        cuts.update(b)
        for v in rule.get("values") or []:
            spans.append(("flag", v, b))
    cuts = sorted(c for c in cuts if 0.0 <= c <= 1.0)
    out = []
    for a, b in zip(cuts, cuts[1:]):
        if b - a < 1e-6:
            continue
        mid = (a + b) / 2
        level, flags = 0, set()
        for kind, v, (lo, hi) in spans:
            if lo <= mid <= hi:
                if kind == "level":
                    level = v
                else:
                    flags.add(v)
        part = substring(line, a, b, normalized=True) if (a, b) != (0.0, 1.0) else line
        if part.geom_type == "LineString" and len(part.coords) >= 2:
            out.append((part, level, flags))
    return out


def _segment_tags(r) -> dict | None:
    sub, cls = r["subtype"], r.get("class")
    if sub == "rail":
        # The CTA 'L' is tagged as a subway in OSM, which KnoxMap leaves out;
        # in Evanston the Purple Line runs on an embankment, not underground.
        return {"railway": "rail"}
    if sub != "road":
        return None
    hw = HIGHWAY.get(cls or "unknown", "road")
    tags = {"highway": hw}
    if _name(r):
        tags["name"] = _name(r)
    if hw == "footway" and r.get("subclass") in FOOTWAY_SUBCLASS:
        tags["footway"] = FOOTWAY_SUBCLASS[r["subclass"]]
    if hw == "service" and r.get("subclass") in SERVICE_SUBCLASS:
        tags["service"] = r["subclass"]
    surf = r.get("road_surface") or []
    if surf and not surf[0].get("between"):
        tags["surface"] = surf[0].get("value")
    widths = r.get("width_rules") or []
    if len(widths) == 1 and widths[0].get("value"):
        tags["width"] = f"{float(widths[0]['value']):.1f}"
    return tags


def features(folder: str, window) -> list[OSMFeature]:
    """Every feature of the fetched data that touches `window` (lon/lat polygon)."""
    out: list[OSMFeature] = []
    ident = [1]

    def emit(g, tags, kind_hint=None):
        for part in getattr(g, "geoms", None) or [g]:
            if part.is_empty:
                continue
            if part.geom_type == "Polygon":
                outer = [(y, x) for x, y in part.exterior.coords]
                holes = [[(y, x) for x, y in ring.coords] for ring in part.interiors]
                if holes:
                    f = OSMFeature(ident[0], "relation", dict(tags), [outer] + holes)
                    f.role_geoms = [("outer", outer)] + [("inner", h) for h in holes]
                else:
                    f = OSMFeature(ident[0], "way", dict(tags), outer)
            elif part.geom_type == "LineString":
                f = OSMFeature(ident[0], "way", dict(tags), [(y, x) for x, y in part.coords])
            elif part.geom_type == "Point":
                f = OSMFeature(ident[0], "node", dict(tags), [(part.y, part.x)])
            else:
                continue
            ident[0] += 1
            out.append(f)

    prepared = shapely.prepared.prep(window)

    def rows(kind):
        p = os.path.join(folder, f"{kind}.parquet")
        return pq.read_table(p).to_pylist() if os.path.exists(p) else []

    def geom(r):
        g = wkb.loads(r["geometry"])
        return g if prepared.intersects(g) else None

    for r in rows("land"):
        if r["subtype"] == "land":
            continue
        g = geom(r)
        if g is not None and _tags(r):
            emit(g, _tags(r))
    for r in rows("water"):
        if r["subtype"] == "physical":
            continue
        g = geom(r)
        if g is None:
            continue
        if r["class"] == "lake" and g.area > window.area:
            # A Great Lake: only the part in the window, as plain water.
            emit(g.intersection(window), {"natural": "water", "name": _name(r) or ""})
            continue
        tags = _tags(r)
        if not tags:
            tags = {"natural": "water"} if g.geom_type in ("Polygon", "MultiPolygon") else {}
        if tags:
            emit(g, tags)
    for kind in ("land_use", "infrastructure"):
        for r in rows(kind):
            g = geom(r)
            if g is not None and _tags(r):
                emit(g, _tags(r))
    for r in rows("segment"):
        tags = _segment_tags(r)
        g = geom(r) if tags else None
        if g is None or g.geom_type != "LineString":
            continue
        for part, level, flags in _pieces(g, r):
            if "is_indoor" in flags:
                continue          # corridors through buildings, not streets
            t = dict(tags)
            if "is_tunnel" in flags or level < 0:
                t["tunnel"] = "yes"
                t["layer"] = str(min(-1, level))
            elif "is_bridge" in flags or level > 0:
                t["bridge"] = "yes"
                t["layer"] = str(max(1, level))
            emit(part, t)
    for r in rows("building"):
        g = geom(r)
        if g is None or g.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        if r.get("is_underground"):
            continue
        tags = {"building": r.get("class") or "yes"}
        if _name(r):
            tags["name"] = _name(r)
        if r.get("height"):
            tags["height"] = f"{float(r['height']):.1f}"
        if r.get("num_floors"):
            tags["building:levels"] = str(int(r["num_floors"]))
        if r.get("roof_shape"):
            tags["roof:shape"] = r["roof_shape"]
        if r.get("facade_material"):
            tags["building:material"] = r["facade_material"]
        emit(g, tags)
    for r in rows("place"):
        tags = place_tags(r)
        g = geom(r) if tags else None
        if g is not None and g.geom_type == "Point":
            emit(g, tags)
    return out


def outline(folder: str, name: str, subtype: str = "locality"):
    """A town's boundary from Overture's divisions, as a lon/lat polygon."""
    for r in rows_of(folder, "division_area"):
        if _name(r) == name and r["subtype"] == subtype and r.get("class") == "land":
            return wkb.loads(r["geometry"])
    raise SystemExit(f"no {subtype} called {name} in the divisions data")


def rows_of(folder, kind):
    return pq.read_table(os.path.join(folder, f"{kind}.parquet")).to_pylist()

