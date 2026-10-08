import re

import numpy as np
from shapely.geometry import LineString, Point

from . import scenery, story

KINDS = ("restaurant", "cafe", "bar", "pub", "ice_cream", "biergarten")
FUN_KINDS = {"ice_cream", "biergarten", "pub"}
FAMOUS_TAGS = ("wikipedia", "wikidata", "historic", "heritage")
# A start_date only makes a place an old favourite when it isn't a recent opening.
OLD_BEFORE = 2000
LABELS = {"ice_cream": "ice cream shop", "biergarten": "beer garden"}
NEAR_M = 60
# The query line is the route simplified by up to ~20 m, so it searches a bit wider than NEAR_M.
SIMPLIFY_DEG = 0.0002
QUERY_M = 90
GAP_M = 2000
SKIP_ENDS_M = 500
MAX_SPOTS = 8


def _query(coords: list) -> str:
    lats, lons = [p[0] for p in coords], [p[1] for p in coords]
    bbox = f"{min(lats) - 0.001:.5f},{min(lons) - 0.001:.5f},{max(lats) + 0.001:.5f},{max(lons) + 0.001:.5f}"
    path = LineString(list(zip(lats, lons))).simplify(SIMPLIFY_DEG)
    line = ",".join(f"{lat:.5f},{lon:.5f}" for lat, lon in path.coords)
    kinds = "|".join(KINDS)
    # Tags first, then the distance: "around" first loads everything near the route and ran out of memory.
    # A small declared timeout and maxsize help a busy public server admit the query.
    return (
        f"[out:json][timeout:10][maxsize:16000000][bbox:{bbox}];"
        f'nwr["amenity"~"^({kinds})$"]["name"]->.c;nwr.c(around:{QUERY_M},{line});out center tags;'
    )


def _year(tags: dict) -> int | None:
    match = re.match(r"\d{4}", tags.get("start_date", ""))
    return int(match.group()) if match else None


def _note(tags: dict) -> str:
    parts = [LABELS.get(tags["amenity"], tags["amenity"])]
    if tags.get("cuisine"):
        parts.append("food: " + ", ".join(tags["cuisine"].split(";")[:3]).replace("_", " "))
    year = _year(tags)
    if year:
        parts.append(f"since {year}")
    if "historic" in tags or "heritage" in tags:
        parts.append("historic")
    if "wikipedia" in tags:
        parts.append("on Wikipedia")
    return ", ".join(parts)


def _rank(tags: dict) -> int:
    year = _year(tags)
    if any(k in tags for k in FAMOUS_TAGS) or (year and year < OLD_BEFORE):
        return 0
    return 1 if tags["amenity"] in FUN_KINDS else 2


def choose(coords: list, elements: list[dict]) -> list[dict]:
    """Named places the route passes right by, the old and fun ones first, spread out along the route."""
    line, along_m, lat0 = story._geometry(coords)
    found = []
    for e in elements:
        tags = e["tags"]
        if "brand" in tags:  # chains aren't fun stops
            continue
        lat, lon = (e["lat"], e["lon"]) if e["type"] == "node" else (e["center"]["lat"], e["center"]["lon"])
        point = Point(scenery._xy(lat, lon, lat0))
        off_m = line.distance(point)
        passed_at = line.project(point)
        if off_m > NEAR_M or not SKIP_ENDS_M <= passed_at <= along_m[-1] - SKIP_ENDS_M:
            continue
        index = int(min(np.searchsorted(along_m, passed_at), len(coords) - 1))
        spot = {"name": tags["name"], "kind": tags["amenity"], "note": _note(tags), "lat": lat, "lon": lon}
        found.append({**spot, "index": index, "along_m": passed_at, "rank": _rank(tags), "off_m": off_m})

    # On long rides a wider gap spreads the spots over the whole route instead of the first 8 good ones.
    gap = max(GAP_M, along_m[-1] / MAX_SPOTS / 2)
    chosen = []
    for s in sorted(found, key=lambda s: (s["rank"], s["off_m"])):
        if all(abs(s["along_m"] - c["along_m"]) >= gap for c in chosen):
            chosen.append(s)
        if len(chosen) == MAX_SPOTS:
            break
    return [
        {k: s[k] for k in ("name", "kind", "note", "lat", "lon", "index")}
        for s in sorted(chosen, key=lambda s: s["along_m"])
    ]


def along(coords: list) -> list[dict]:
    """[] when no Overpass server answered within scenery's budget: the ride just has no spots."""
    elements = scenery._overpass_within_budget(_query(coords))
    return choose(coords, elements) if elements else []
