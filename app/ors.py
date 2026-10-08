import os
from concurrent.futures import ThreadPoolExecutor

import httpx

URL = "https://api.openrouteservice.org/v2/directions/{profile}/geojson"
GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"
PROFILES = {"road": "cycling-road", "gravel": "cycling-regular", "mtb": "cycling-mountain"}
NEAR_KM = 200
# In hilly areas ORS overshoots the target loop length a lot (40 km asked, 145 km back near Lecco),
# so also ask for shorter loops and let the planner keep the ones closest to the target.
LOOP_TRIES = [(seed, 1.0) for seed in range(3)] + [(seed, 0.6) for seed in range(3, 6)]


def search(text: str, near: tuple[float, float] | None = None, size: int = 5) -> list[dict]:
    """Towns, addresses and venues (e.g. a climbing gym); tolerant of small typos.
    With near=(lat, lon) it only searches within NEAR_KM of that point, so "Orobia" doesn't land in Uganda."""
    params = {"api_key": os.environ["ORS_API_KEY"], "text": text, "size": size}
    if near:
        params |= {
            "focus.point.lat": near[0],
            "focus.point.lon": near[1],
            "boundary.circle.lat": near[0],
            "boundary.circle.lon": near[1],
            "boundary.circle.radius": NEAR_KM,
        }
    r = httpx.get(GEOCODE_URL, params=params, timeout=15)
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
    r = httpx.post(
        URL.format(profile=PROFILES[bike]),
        json=payload,
        headers={"Authorization": os.environ["ORS_API_KEY"]},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["features"]


def loops(lat: float, lon: float, length_km: float, bike: str, priority: str) -> list[dict]:
    """Round trips from one start; each seed sends the loop in another direction."""

    def one(try_):
        seed, factor = try_
        round_trip = {"length": length_km * 1000 * factor, "points": 5, "seed": seed}
        return _directions(bike, priority, {"coordinates": [[lon, lat]]}, {"round_trip": round_trip})

    with ThreadPoolExecutor(len(LOOP_TRIES)) as pool:
        return [f for features in pool.map(one, LOOP_TRIES) for f in features]


def a_to_b(start: tuple[float, float], end: tuple[float, float], bike: str, priority: str) -> list[dict]:
    return _directions(
        bike,
        priority,
        {
            "coordinates": [[start[1], start[0]], [end[1], end[0]]],
            "alternative_routes": {"target_count": 3, "share_factor": 0.6, "weight_factor": 1.6},
        },
    )
