from itertools import groupby
from math import cos, radians

import httpx
import numpy as np
import shapely
from shapely.geometry import LineString, Point

# Public Overpass servers are often overloaded (504/500), so try them in order.
OVERPASS_URLS = [
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
NEAR_M = 150
VIEW_M = 300
STEP_M = 100
EMPTY = {"water_km": 0.0, "forest_km": 0.0, "viewpoints": 0}


def _xy(lat, lon, lat0: float):
    """Equirectangular projection to meters; fine at the scale of one ride."""
    return np.asarray(lon) * 111_320 * cos(radians(lat0)), np.asarray(lat) * 110_540


def _query(routes: list[list]) -> str:
    # A plain bbox + tag query is much cheaper for the server than "around" a long route line;
    # the exact distance checks happen locally in measure().
    lats = [p[0] for coords in routes for p in coords]
    lons = [p[1] for coords in routes for p in coords]
    bbox = f"{min(lats) - 0.005},{min(lons) - 0.005},{max(lats) + 0.005},{max(lons) + 0.005}"
    # out geom(bbox) clips huge features like big lakes; points outside the box come back as null.
    return (
        f"[out:json][timeout:20][bbox:{bbox}];"
        '(nwr["natural"~"^(water|wood)$"];nwr["landuse"="forest"];'
        'way["waterway"~"^(river|canal)$"];node["tourism"="viewpoint"];);'
        f"out geom({bbox});"
    )


def _overpass(query: str) -> list[dict] | None:
    for url in OVERPASS_URLS:
        try:
            r = httpx.post(url, data={"data": query}, headers={"User-Agent": "SpokeAndStory/0.1"}, timeout=25)
        except httpx.TransportError:
            continue
        # A 200 with a "remark" means the server hit a runtime error and the data is incomplete.
        if r.status_code == 200 and "remark" not in (data := r.json()):
            return data["elements"]
    return None


def _category(tags: dict) -> str:
    if tags.get("tourism") == "viewpoint":
        return "views"
    if tags.get("natural") == "water" or "waterway" in tags:
        return "water"
    return "forest"


def _shapes(element: dict, lat0: float) -> list:
    if element["type"] == "node":
        return [Point(_xy(element["lat"], element["lon"], lat0))]
    parts = [element.get("geometry")] if element["type"] == "way" else [m.get("geometry") for m in element["members"]]
    lines = []
    for part in parts:
        for is_point, run in groupby(part or [], key=lambda p: p is not None):
            run = list(run)
            if is_point and len(run) >= 2:
                lines.append(LineString(np.column_stack(_xy([p["lat"] for p in run], [p["lon"] for p in run], lat0))))
    # Closed rings become areas, so riding through the middle of a forest still counts.
    return lines + list(shapely.polygonize(lines).geoms)


def measure(coords: list, shapes: dict, lat0: float) -> dict:
    lat, lon = np.array(coords)[:, 0], np.array(coords)[:, 1]
    line = LineString(np.column_stack(_xy(lat, lon, lat0)))
    samples = shapely.line_interpolate_point(line, np.arange(0, line.length, STEP_M))

    def km_near(geoms):
        if not geoms:
            return 0.0
        hits, _ = shapely.STRtree(geoms).query(samples, predicate="dwithin", distance=NEAR_M)
        return round(len(np.unique(hits)) * STEP_M / 1000, 1)

    views = shapes["views"]
    return {
        "water_km": km_near(shapes["water"]),
        "forest_km": km_near(shapes["forest"]),
        "viewpoints": int(shapely.dwithin(line, views, VIEW_M).sum()) if views else 0,
    }


def scenery(routes: list[list]) -> list[dict]:
    """Per route (coords as [lat, lon, ele]): km near water, km near/in forest, viewpoints nearby.
    None when no Overpass server answered."""
    elements = _overpass(_query(routes))
    if elements is None:
        return None
    lat0 = routes[0][0][0]
    shapes = {"water": [], "forest": [], "views": []}
    for element in elements:
        shapes[_category(element.get("tags", {}))] += _shapes(element, lat0)
    return [measure(coords, shapes, lat0) for coords in routes]
