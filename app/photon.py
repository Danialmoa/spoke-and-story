from functools import lru_cache

import httpx

from . import tracing
from .ors import straight_km

# Photon: Komoot's open-source geocoder on OpenStreetMap data. Free, no key, built for search-as-you-type,
# and tolerant of typos ("orobio climbing"). ORS's geocoder quota ran out in a day of autocomplete.
URL = "https://photon.komoot.io/api/"
USER_AGENT = "SpokeAndStory/0.1 (DEV Hacktoberfest project; https://dev.to/challenges/hf26)"
NEAR_KM = 200


def _label(p: dict) -> str:
    first = p.get("name") or " ".join(x for x in (p.get("street"), p.get("housenumber")) if x)
    parts = [first, p.get("city") or p.get("county"), p.get("country")]
    return ", ".join(dict.fromkeys(x for x in parts if x))


@lru_cache(maxsize=1024)
def _search(text: str, near: tuple[float, float] | None, size: int) -> tuple[dict, ...]:
    params = {"q": text, "limit": size * 2, "lang": "en"}
    if near:
        params |= {"lat": near[0], "lon": near[1]}
    with tracing.span("Photon search"):
        r = httpx.get(URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=10)
    r.raise_for_status()
    results = {}
    for f in r.json()["features"]:
        lon, lat = f["geometry"]["coordinates"]
        name = _label(f["properties"])
        # One village comes back as several OSM objects (node, boundary...); keep the first of each name.
        if name in results or (near and straight_km(near, (lat, lon)) > NEAR_KM):
            continue
        results[name] = {"name": name, "lat": lat, "lon": lon}
    return tuple(results.values())[:size]


def search(text: str, near: tuple[float, float] | None = None, size: int = 5) -> list[dict]:
    """Towns, addresses and venues (e.g. a climbing gym). With near=(lat, lon), nearby matches come first and
    anything farther than NEAR_KM is dropped, so "Orobia" doesn't land in Uganda."""
    key = (round(near[0], 2), round(near[1], 2)) if near else None
    return [dict(r) for r in _search(text.strip(), key, size)]


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
