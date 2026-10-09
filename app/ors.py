import os
from concurrent.futures import ThreadPoolExecutor
from math import asin, cos, radians, sin, sqrt

import httpx

from . import tracing

URL = "https://api.openrouteservice.org/v2/directions/{profile}/geojson"
PROFILES = {"road": "cycling-road", "gravel": "cycling-regular", "mtb": "cycling-mountain"}
# In hilly areas ORS overshoots the target loop length a lot (40 km asked, 145 km back near Lecco),
# so also ask for shorter loops and let the planner keep the ones closest to the target.
LOOP_TRIES = [(seed, 1.0) for seed in range(3)] + [(seed, 0.6) for seed in range(3, 6)]
# ORS only computes alternative routes up to 100 km of road; roads run ~1.3x the straight line.
ALTERNATIVES_MAX_KM = 70
# ORS hung for minutes on alternatives with some weightings (gravel + flat); one route came back in ~11 s.
ALTERNATIVES_S = 15
TIMEOUT_S = 30


def _headers() -> dict:
    # The key goes in a header, never in the URL, so it can't leak through logged URLs.
    return {"Authorization": os.environ["ORS_API_KEY"]}


def straight_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    h = (
        sin(radians(b[0] - a[0]) / 2) ** 2
        + cos(radians(a[0])) * cos(radians(b[0])) * sin(radians(b[1] - a[1]) / 2) ** 2
    )
    return 2 * 6371 * asin(sqrt(h))


def _directions(
    bike: str, priority: str, body: dict, options: dict | None = None, timeout: float = TIMEOUT_S
) -> list[dict]:
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
    profile = PROFILES[bike]
    if priority == "bike_lanes" and bike == "road":
        # cycling-road slows cycleways to 8 km/h to keep road bikes off them; cycling-regular prefers them.
        profile = "cycling-regular"
    with tracing.span("ORS directions"):
        r = httpx.post(URL.format(profile=profile), json=payload, headers=_headers(), timeout=timeout)
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


def through(points: list[list[float]], bike: str, priority: str) -> dict:
    """One route through [lat, lon] points, in order."""
    return _directions(bike, priority, {"coordinates": [[p[1], p[0]] for p in points]})[0]


def a_to_b(start: tuple[float, float], end: tuple[float, float], bike: str, priority: str) -> list[dict]:
    body = {"coordinates": [[start[1], start[0]], [end[1], end[0]]]}
    if straight_km(start, end) < ALTERNATIVES_MAX_KM:
        alternatives = {"target_count": 3, "share_factor": 0.6, "weight_factor": 1.6}
        try:
            return _directions(bike, priority, {**body, "alternative_routes": alternatives}, timeout=ALTERNATIVES_S)
        except httpx.TimeoutException:
            pass
    return _directions(bike, priority, body)
