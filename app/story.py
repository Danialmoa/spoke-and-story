import random
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import httpx
import numpy as np
from shapely.geometry import LineString, Point

from . import agent, scenery, tracing

# A place about every 2 km: at every 3 km most of a real ride was silence.
MAX_STORIES = 12
KM_PER_STORY = 2
# Wikipedia's geosearch finds articles around a point; ask every SAMPLE_M along the route.
SAMPLE_M = 2000
SEARCH_RADIUS_M = 1500
NEAR_M = 1000
# The intro and finish lines already cover the start and the destination.
SKIP_ENDS_M = 1000
WIKI = "https://en.wikipedia.org"
USER_AGENT = "SpokeAndStory/0.1 (DEV Hacktoberfest project; https://dev.to/challenges/hf26)"
# About 4-7 rides of stories; places told longer ago count as fresh again.
HISTORY_MAX = 30
# What the live chapter is written from, sent to the page and back.
FACTS_CHARS = 800
DULL_SECTIONS = {"See also", "References", "External links", "Further reading", "Notes", "Sources", "Bibliography"}


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


def _section(title: str) -> str:
    """A random section of the article instead of the lead, so a place told on an earlier ride gets new facts."""
    params = {
        "action": "query",
        "prop": "extracts",
        "explaintext": 1,
        "exsectionformat": "wiki",
        "titles": title,
        "redirects": 1,
        "format": "json",
        "formatversion": 2,
    }
    pages = _get(f"{WIKI}/w/api.php", params).get("query", {}).get("pages", [])
    # [lead, heading, text, heading, text, ...]
    parts = re.split(r"^==+ (.+?) ==+$", pages[0].get("extract", "") if pages else "", flags=re.M)
    sections = [
        f"{head}: {text.strip()}"
        for head, text in zip(parts[1::2], parts[2::2])
        if head not in DULL_SECTIONS and len(text.strip()) >= 150
    ]
    return random.choice(sections)[:1200] if sections else _summary(title)


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


def choose(places: list[dict], total_m: float, n: int, told: set[str] = frozenset()) -> list[dict]:
    """Up to n places spread along the route, preferring ones not told on earlier rides, then the ones right next
    to it. A told place still fills a stretch that has nothing fresh."""
    gap = total_m / (n + 1) / 2
    chosen = []
    for p in sorted(places, key=lambda p: (p["name"] in told, p["off_m"])):
        if not SKIP_ENDS_M <= p["along_m"] <= total_m - SKIP_ENDS_M:
            continue
        if all(abs(p["along_m"] - c["along_m"]) >= gap for c in chosen):
            chosen.append(p)
        if len(chosen) == n:
            break
    return sorted(chosen, key=lambda p: p["along_m"])


PINNED = "a spot picked on the map"


def _label(place: str, coords_name: str = "your current location") -> str:
    try:
        [float(x) for x in place.split(",")]
        return coords_name
    except ValueError:
        return place


def build(
    req, route: dict, history: list[dict] | None = None, rides_before: int = 0, last_episode: dict | None = None
) -> dict:
    """The ride's tale, quickly: intro, turn lines, finish, cliffhanger, and the places with their beat and facts.
    Each chapter is written live from those as the rider reaches the place (/api/live "chapter").
    history: what the rider heard on earlier rides, most recent first: [{"place": <Wikipedia title>, "gist": ...}].
    rides_before: how many earlier rides had this same start and end.
    last_episode: {"thread", "cliffhanger"} of the last ride on this route; this ride is its next episode."""
    told = (history or [])[:HISTORY_MAX]
    gists = {}
    for h in told:
        gists.setdefault(h["place"], h.get("gist") or "")
    coords = route["coords"]
    _, along_m, _ = _geometry(coords)
    samples = [
        coords[min(int(np.searchsorted(along_m, d)), len(coords) - 1)] for d in np.arange(0, along_m[-1], SAMPLE_M)
    ]
    with ThreadPoolExecutor(8) as pool:
        articles = [a for near in pool.map(_articles_near, samples) for a in near]
        n = min(MAX_STORIES, max(2, int(route["distance_km"] // KM_PER_STORY)))
        places = choose(places_along(coords, articles), route["distance_km"] * 1000, n, set(gists))
        facts = list(pool.map(lambda p: (_section if p["name"] in gists else _summary)(p["name"]), places))

    ride = {
        "from": _label(req.start),
        # A pinned destination or Home is "lat,lon": never send coordinates to the model.
        "to": _label(req.end, PINNED) if req.end else "back to the start (it's a loop)",
        "distance_km": route["distance_km"],
        "climb_m": route["climb_m"],
        "bike": req.bike,
    }
    number = max(rides_before + 1, 2) if last_episode else rides_before + 1
    if number > 1:
        ride["ride_number_on_this_route"] = number
    outline = agent.ride_outline(
        ride, [{"name": p["name"], "facts": f} for p, f in zip(places, facts)], told, last_episode
    )
    beats = outline.get("beats", [])
    stories = []
    for i, (p, f) in enumerate(zip(places, facts)):
        beat = beats[i] if i < len(beats) else ""
        if not (f or beat):
            continue
        # lat/lon: after a reroute the page pins the stories to the new route.
        place = {
            "index": p["index"],
            "place": p["name"],
            "lat": p["lat"],
            "lon": p["lon"],
            "beat": beat,
            "facts": f[:FACTS_CHARS],
        }
        if p["name"] in gists:
            place["told_before"] = gists[p["name"]] or "a chapter about it"
        stories.append(place)
    return {
        "intro": outline.get("intro", ""),
        "finish": outline.get("finish", ""),
        "thread": outline.get("thread", ""),
        "cliffhanger": outline.get("cliffhanger", ""),
        "turn_lines": [t for t in outline.get("turn_lines", []) if t.count("{turn}") == 1],
        "stories": stories,
    }
