import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from functools import lru_cache

import httpx

from . import photon, tracing

log = logging.getLogger("spokestory.news")
# GDELT: open, free, no key, news from around the world in 65+ languages.
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
REVERSE_URL = "https://photon.komoot.io/reverse"
MAX_HEADLINES = 15
TTL_S = 3600
# An empty answer is often GDELT saying "too many requests", so it's asked again sooner.
RETRY_S = 300
# GDELT asks for at most one request every 5 seconds.
GAP_S = 5
MAX_TOWNS = 500
# Its TLS handshake alone took ~9 s in testing: requests run in the background with a long timeout, so a late answer
# still lands in the cache for the next brief, and one kept-alive client skips the handshake next time.
FETCH_TIMEOUT_S = 25

_gdelt = ThreadPoolExecutor(1)  # one worker: requests go out one at a time, GAP_S apart
_client = httpx.Client(headers={"User-Agent": photon.USER_AGENT}, timeout=FETCH_TIMEOUT_S)
_cache: dict[tuple[str, str], tuple[float, Future]] = {}
_lock = threading.Lock()
_last_done = 0.0


@lru_cache(maxsize=1024)
def town_at(lat: float, lon: float) -> dict:
    """{"town", "region", "country", "country_code"} at a point, with Photon's reverse geocoder. Raises when Photon
    fails, so failures aren't cached."""
    with tracing.span("Photon reverse"):
        r = httpx.get(
            REVERSE_URL,
            params={"lat": lat, "lon": lon, "lang": "en", "limit": 1},
            headers={"User-Agent": photon.USER_AGENT},
            timeout=3,
        )
    r.raise_for_status()
    p = r.json()["features"][0]["properties"]
    return {
        "town": p.get("city") or p.get("county") or p["name"],
        "region": p.get("state", ""),
        "country": p.get("country", ""),
        "country_code": p.get("countrycode", ""),
    }


def _fetch(town: str, country: str) -> list[str]:
    global _last_done
    time.sleep(max(0.0, _last_done + GAP_S - time.monotonic()))
    # sourcecountry keeps it local, and out of other languages' words: "Como" is also Spanish for "how".
    query = f'"{town}"' + (f" sourcecountry:{country.replace(' ', '').lower()}" if country else "")
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": MAX_HEADLINES,
        "timespan": "7d",
        "sort": "hybridrel",
    }
    try:
        with tracing.span("GDELT news"):
            r = _client.get(GDELT_URL, params=params)
        # A rate limit or a bad query comes back as plain text, not JSON.
        articles = r.json().get("articles", []) if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("GDELT failed: %s", type(exc).__name__)
        articles = []
    finally:
        _last_done = time.monotonic()
    return list(dict.fromkeys(" ".join(a["title"].split()) for a in articles if a.get("title")))


def _fresh(asked_at: float, future: Future) -> bool:
    if not future.done():
        return True
    return time.monotonic() - asked_at < (TTL_S if future.result() else RETRY_S)


def headlines(town: str, country: str, wait_s: float) -> list[str]:
    """Up to 15 recent headlines naming the town, in any language; cached per town for an hour.
    [] when GDELT fails or hasn't answered within wait_s (the answer still lands in the cache)."""
    key = (town.lower(), country)
    with _lock:
        entry = _cache.get(key)
        if entry is None or not _fresh(*entry):
            if len(_cache) >= MAX_TOWNS:
                _cache.clear()
            entry = _cache[key] = (time.monotonic(), _gdelt.submit(_fetch, town, country))
    try:
        return entry[1].result(timeout=wait_s)
    except TimeoutError:
        return []


def near(lat: float, lon: float, wait_s: float) -> dict:
    """{"town", "country", "headlines"} for the town at a point; {} when Photon can't name it."""
    try:
        found = town_at(round(lat, 2), round(lon, 2))
    except (httpx.HTTPError, ValueError, LookupError) as exc:
        log.warning("Photon reverse failed: %s", type(exc).__name__)
        return {}
    town, country = found["town"], found["country"]
    return {"town": town, "country": country, "headlines": headlines(town, country, wait_s)}
