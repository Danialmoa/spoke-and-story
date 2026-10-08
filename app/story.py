from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import httpx
import numpy as np
from shapely.geometry import LineString, Point

from . import agent, scenery, tracing

MAX_STORIES = 8
KM_PER_STORY = 3
# Wikipedia's geosearch finds articles around a point; ask every SAMPLE_M along the route.
SAMPLE_M = 2000
SEARCH_RADIUS_M = 1500
NEAR_M = 1000
# The intro and finish lines already cover the start and the destination.
SKIP_ENDS_M = 1000
WIKI = "https://en.wikipedia.org"
USER_AGENT = "SpokeAndStory/0.1 (DEV Hacktoberfest project; https://dev.to/challenges/hf26)"


def _get(url: str, params: dict | None = None) -> dict:
    """Wikipedia is optional flavour: when it's unreachable the ride just has fewer stories."""
    try:
        with tracing.span("Wikipedia"):
            r = httpx.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=10, follow_redirects=True)
    except httpx.TransportError:
        return {}
    return r.json() if r.status_code == 200 else {}


def _articles_near(point: list) -> list[dict]:
    params = {
        "action": "query",
        "list": "geosearch",
        "gscoord": f"{point[0]}|{point[1]}",
        "gsradius": SEARCH_RADIUS_M,
        "gslimit": 10,
        "format": "json",
    }
    return _get(f"{WIKI}/w/api.php", params).get("query", {}).get("geosearch", [])


def _summary(title: str) -> str:
    return _get(f"{WIKI}/api/rest_v1/page/summary/{quote(title.replace(' ', '_'), safe='')}").get("extract", "")[:1200]


def _geometry(coords: list) -> tuple[LineString, np.ndarray, float]:
    lat0 = coords[0][0]
    x, y = scenery._xy([p[0] for p in coords], [p[1] for p in coords], lat0)
    along_m = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
    return LineString(np.column_stack([x, y])), along_m, lat0


def places_along(coords: list, articles: list[dict]) -> list[dict]:
    """Articles the route passes within NEAR_M of, with the route point where that happens."""
    line, along_m, lat0 = _geometry(coords)
    found = {}
    for a in articles:
        point = Point(scenery._xy(a["lat"], a["lon"], lat0))
        off_m = line.distance(point)
        if off_m > NEAR_M or a["title"] in found:
            continue
        passed_at = line.project(point)
        found[a["title"]] = {
            "name": a["title"],
            "lat": a["lat"],
            "lon": a["lon"],
            "index": int(min(np.searchsorted(along_m, passed_at), len(coords) - 1)),
            "along_m": passed_at,
            "off_m": off_m,
        }
    return list(found.values())


def choose(places: list[dict], total_m: float, n: int) -> list[dict]:
    """Up to n places spread along the route, preferring the ones right next to it."""
    gap = total_m / (n + 1) / 2
    chosen = []
    for p in sorted(places, key=lambda p: p["off_m"]):
        if not SKIP_ENDS_M <= p["along_m"] <= total_m - SKIP_ENDS_M:
            continue
        if all(abs(p["along_m"] - c["along_m"]) >= gap for c in chosen):
            chosen.append(p)
        if len(chosen) == n:
            break
    return sorted(chosen, key=lambda p: p["along_m"])


def _label(place: str) -> str:
    try:
        [float(x) for x in place.split(",")]
        return "your current location"
    except ValueError:
        return place


def build(req, route: dict) -> dict:
    """The spoken script for ride mode: intro, stories pinned to route points, playful turn lines, finish."""
    coords = route["coords"]
    _, along_m, _ = _geometry(coords)
    samples = [
        coords[min(int(np.searchsorted(along_m, d)), len(coords) - 1)] for d in np.arange(0, along_m[-1], SAMPLE_M)
    ]
    with ThreadPoolExecutor(8) as pool:
        articles = [a for near in pool.map(_articles_near, samples) for a in near]
        n = min(MAX_STORIES, max(2, int(route["distance_km"] // KM_PER_STORY)))
        places = choose(places_along(coords, articles), route["distance_km"] * 1000, n)
        facts = list(pool.map(lambda p: _summary(p["name"]), places))

    ride = {
        "from": _label(req.start),
        "to": req.end or "back to the start (it's a loop)",
        "distance_km": route["distance_km"],
        "climb_m": route["climb_m"],
        "bike": req.bike,
    }
    script = agent.ride_story(ride, [{"name": p["name"], "facts": f} for p, f in zip(places, facts)])
    beats = script.get("beats", [])
    # lat/lon: after a reroute the page pins the stories to the new route.
    return {
        "intro": script.get("intro", ""),
        "finish": script.get("finish", ""),
        "thread": script.get("thread", ""),
        "turn_lines": [t for t in script.get("turn_lines", []) if t.count("{turn}") == 1],
        "stories": [
            {
                "index": p["index"],
                "place": p["name"],
                "text": text,
                "beat": beats[i] if i < len(beats) else "",
                "lat": p["lat"],
                "lon": p["lon"],
            }
            for i, (p, text) in enumerate(zip(places, script["stories"]))
            if text
        ],
    }
