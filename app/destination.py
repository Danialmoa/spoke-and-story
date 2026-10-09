import html
import ipaddress
import logging
import re
import socket
import time
from datetime import datetime
from urllib.parse import urlparse

import httpx
import numpy as np
from shapely.geometry import LineString, Point

from . import openmeteo, photon, scenery, story, tracing
from .ors import straight_km

log = logging.getLogger("spokestory.destination")
STOPS = {"drinking_water": "water", "bicycle_repair_station": "repair"}
# Towns, roads and areas aren't venues; a gym, a hut or a museum is, and has opening hours worth telling.
NOT_VENUES = {"place", "boundary", "highway", "landuse", "natural", "waterway", "railway"}
OSM_TYPES = {"N": "node", "W": "way", "R": "relation"}
VENUE_TAGS = {
    "opening_hours": "opening_hours",
    "phone": "phone",
    "contact:phone": "phone",
    "website": "website",
    "contact:website": "website",
}
# Photon's best match has to be where the route ends, or it's some other place with the same name.
VENUE_MAX_KM = 2
NEAR_M = 150
# The query line is the route simplified by up to ~20 m, so it searches a bit wider than NEAR_M.
SIMPLIFY_DEG = 0.0002
QUERY_M = 180
TIMEOUT_S = 3
SITE_BYTES = 200_000
MAX_REDIRECTS = 3


def _venue(end: str, coords: list) -> dict | None:
    """The destination's OSM type, id and kind when it's a venue, found with Photon like the planner found it."""
    params = {"q": end, "limit": 1, "lang": "en", "lat": coords[0][0], "lon": coords[0][1]}
    with tracing.span("Photon search"):
        r = httpx.get(photon.URL, params=params, headers={"User-Agent": photon.USER_AGENT}, timeout=TIMEOUT_S)
    r.raise_for_status()
    features = r.json()["features"]
    if not features:
        return None
    lon, lat = features[0]["geometry"]["coordinates"]
    p = features[0]["properties"]
    if p.get("osm_key") in NOT_VENUES or straight_km((lat, lon), tuple(coords[-1][:2])) > VENUE_MAX_KM:
        return None
    return {
        "type": OSM_TYPES[p["osm_type"]],
        "id": p["osm_id"],
        "name": p.get("name") or end,
        "kind": p.get("osm_value", "").replace("_", " "),
    }


def _query(coords: list, venue: dict | None) -> str:
    """Water and repair stations along the route, and the venue's tags, in one query: public Overpass servers
    allow only a couple of queries at a time per client."""
    lats, lons = [p[0] for p in coords], [p[1] for p in coords]
    bbox = f"{min(lats) - 0.002:.5f},{min(lons) - 0.002:.5f},{max(lats) + 0.002:.5f},{max(lons) + 0.002:.5f}"
    path = LineString(list(zip(lats, lons))).simplify(SIMPLIFY_DEG)
    line = ",".join(f"{lat:.5f},{lon:.5f}" for lat, lon in path.coords)
    by_id = f"{venue['type']}({venue['id']});" if venue else ""
    return (
        "[out:json][timeout:10][maxsize:16000000];"
        f'nwr["amenity"~"^(drinking_water|bicycle_repair_station)$"]({bbox})->.c;'
        f"(nwr.c(around:{QUERY_M},{line});{by_id});out center tags;"
    )


def stops(coords: list, elements: list[dict]) -> list[dict]:
    """Drinking water and bike repair stations within NEAR_M of the route, in route order."""
    line, along_m, lat0 = story._geometry(coords)
    found = []
    for e in elements:
        tags = e.get("tags", {})
        kind = STOPS.get(tags.get("amenity"))
        if not kind or tags.get("access") in ("private", "no"):
            continue
        lat, lon = (e["lat"], e["lon"]) if e["type"] == "node" else (e["center"]["lat"], e["center"]["lon"])
        point = Point(scenery._xy(lat, lon, lat0))
        if line.distance(point) > NEAR_M:
            continue
        passed_at = line.project(point)
        index = int(min(np.searchsorted(along_m, passed_at), len(coords) - 1))
        found.append({"kind": kind, "name": tags.get("name"), "index": index, "km": round(passed_at / 1000, 1)})
    return sorted(found, key=lambda s: s["km"])


def _public(host: str) -> bool:
    """A website tag is a public OSM edit: never let one point the server at its own network."""
    try:
        return all(ipaddress.ip_address(info[4][0]).is_global for info in socket.getaddrinfo(host, 443))
    except (OSError, ValueError):
        return False


def _meta(page: str) -> str:
    title = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    parts = [title.group(1)] if title else []
    for tag in re.findall(r"<meta\b[^>]*>", page, re.I):
        content = re.search(r"content\s*=\s*(\"[^\"]*\"|'[^']*')", tag, re.I)
        if content and re.search(r"(name|property)\s*=\s*[\"'](og:)?description[\"']", tag, re.I):
            parts.append(content.group(1)[1:-1])
            break
    return " | ".join(" ".join(html.unescape(p).split()) for p in parts if p.strip())[:300]


def site(url: str) -> str:
    """The title and description of the venue's website: one line on what it is or what's on. "" when it fails."""
    page = b""
    # httpx's timeout is per read: a server trickling bytes could otherwise keep this thread busy for minutes.
    deadline = time.monotonic() + TIMEOUT_S
    try:
        with tracing.span("Venue website"):
            for _ in range(MAX_REDIRECTS + 1):
                # Every hop is checked: a public site could redirect to the server's own network.
                host = urlparse(url).hostname
                if not url.startswith(("http://", "https://")) or not host or not _public(host):
                    return ""
                with httpx.stream("GET", url, headers={"User-Agent": photon.USER_AGENT}, timeout=TIMEOUT_S) as r:
                    if r.next_request:
                        url = str(r.next_request.url)
                        continue
                    if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
                        return ""
                    for chunk in r.iter_bytes():
                        page += chunk
                        if len(page) >= SITE_BYTES or time.monotonic() > deadline:
                            break
                    return _meta(page.decode(r.encoding or "utf-8", errors="ignore"))
    except httpx.HTTPError:
        return ""
    return ""


def along(end: str | None, coords: list) -> dict:
    """{"venue": the destination's hours, phone and website (None for a town or a loop),
    "stops": water and repair stations along the route (None when Overpass didn't answer)}."""
    venue = None
    if end:
        try:
            venue = _venue(end, coords)
        except (httpx.HTTPError, ValueError, LookupError) as exc:
            log.warning("Photon search failed: %s", type(exc).__name__)
    elements = scenery._overpass(_query(coords, venue))
    if elements is None:
        return {"venue": None, "stops": None}
    if venue:
        tags = next((e["tags"] for e in elements if (e["type"], e["id"]) == (venue["type"], venue["id"])), {})
        venue = {"name": venue["name"], "kind": venue["kind"]}
        venue |= {VENUE_TAGS[k]: v for k, v in tags.items() if k in VENUE_TAGS}
        says = site(venue["website"]) if "website" in venue else ""
        if says:
            venue["website_says"] = says
    return {"venue": venue, "stops": stops(coords, elements)}


def weather(lat: float, lon: float, arrive: datetime) -> dict:
    """Weather at the destination when the rider arrives, and the sunset; {} when Open-Meteo fails."""
    try:
        return openmeteo.at(lat, lon, arrive)
    except (httpx.HTTPError, ValueError, LookupError, TypeError) as exc:
        log.warning("Open-Meteo failed: %s", type(exc).__name__)
        return {}
