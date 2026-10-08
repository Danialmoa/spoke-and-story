import os
from concurrent.futures import ThreadPoolExecutor
from math import asin, cos, radians, sin, sqrt

import httpx

from . import tracing

URL = "https://api.openrouteservice.org/v2/directions/{profile}/geojson"
GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"
PROFILES = {"road": "cycling-road", "gravel": "cycling-regular", "mtb": "cycling-mountain"}
NEAR_KM = 200
# In hilly areas ORS overshoots the target loop length a lot (40 km asked, 145 km back near Lecco),
# so also ask for shorter loops and let the planner keep the ones closest to the target.
LOOP_TRIES = [(seed, 1.0) for seed in range(3)] + [(seed, 0.6) for seed in range(3, 6)]
# ORS only computes alternative routes up to 100 km of road; roads run ~1.3x the straight line.
ALTERNATIVES_MAX_KM = 70


def _headers() -> dict:
    # The key goes in a header, never in the URL, so it can't leak through logged URLs.
    return {"Authorization": os.environ["ORS_API_KEY"]}


def _straight_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    h = (
        sin(radians(b[0] - a[0]) / 2) ** 2
        + cos(radians(a[0])) * cos(radians(b[0])) * sin(radians(b[1] - a[1]) / 2) ** 2
    )
    return 2 * 6371 * asin(sqrt(h))


def search(text: str, near: tuple[float, float] | None = None, size: int = 5) -> list[dict]:
    """Towns, addresses and venues (e.g. a climbing gym); tolerant of small typos.
    With near=(lat, lon) it only searches within NEAR_KM of that point, so "Orobia" doesn't land in Uganda."""
    params = {"text": text, "size": size}
    if near:
        params |= {
            "focus.point.lat": near[0],
            "focus.point.lon": near[1],
            "boundary.circle.lat": near[0],
            "boundary.circle.lon": near[1],
            "boundary.circle.radius": NEAR_KM,
        }
    with tracing.span("ORS geocode"):
        r = httpx.get(GEOCODE_URL, params=params, headers=_headers(), timeout=15)
    r.raise_for_status()
    return [
        {
            "name": f["properties"]["label"],
            "lat": f["geometry"]["coordinates"][1],
            "lon": f["geometry"]["coordinates"][0],
        }
        for f in r.json()["features"]
    ]


def geocode(place: str, near: tuple[float, float] | None = None) -> dict:
    """Accepts "lat,lon" or anything search() understands."""
    try:
        lat, lon = (float(x) for x in place.split(","))
        return {"name": place, "lat": lat, "lon": lon}
    except ValueError:
        pass
    results = search(place, near, size=1)
    if not results:
        raise ValueError(f"Place not found{f' within {NEAR_KM} km of your start' if near else ''}: {place}")
    return results[0]


def _directions(bike: str, priority: str, body: dict, options: dict | None = None) -> list[dict]:
    options = dict(options or {})
    if priority == "flat":
        options["profile_params"] = {"weightings": {"steepness_difficulty": 0}}
    payload = {
        "elevation": True,
        "instructions": True,
        "extra_info": ["surface", "waytype"],
        "preference": "fastest" if priority == "fast" else "recommended",
        **body,
    }
    if options:
        payload["options"] = options
    with tracing.span("ORS directions"):
        r = httpx.post(URL.format(profile=PROFILES[bike]), json=payload, headers=_headers(), timeout=60)
    r.raise_for_status()
    return r.json()["features"]


def loops(lat: float, lon: float, length_km: float, bike: str, priority: str) -> list[dict]:
    """Round trips from one start; each seed sends the loop in another direction.
    A seed ORS can't route (or a rate-limit hit) is skipped; only fails if every seed failed."""

    def one(try_):
        seed, factor = try_
        round_trip = {"length": length_km * 1000 * factor, "points": 5, "seed": seed}
        try:
            return _directions(bike, priority, {"coordinates": [[lon, lat]]}, {"round_trip": round_trip})
        except httpx.HTTPStatusError as exc:
            return exc

    with ThreadPoolExecutor(len(LOOP_TRIES)) as pool:
        results = list(pool.map(one, LOOP_TRIES))
    errors = [r for r in results if isinstance(r, Exception)]
    features = [f for r in results if not isinstance(r, Exception) for f in r]
    if not features and errors:
        raise errors[0]
    return features


def a_to_b(start: tuple[float, float], end: tuple[float, float], bike: str, priority: str) -> list[dict]:
    body = {"coordinates": [[start[1], start[0]], [end[1], end[0]]]}
    if _straight_km(start, end) < ALTERNATIVES_MAX_KM:
        body["alternative_routes"] = {"target_count": 3, "share_factor": 0.6, "weight_factor": 1.6}
    return _directions(bike, priority, body)
