import logging
import random
from concurrent.futures import ThreadPoolExecutor

import httpx

from . import agent, news, story, topics

log = logging.getLogger("spokestory.area")
NEAR_M = 1000
# One geosearch finds both the places right here and the towns around, as ideas for topics.
RADIUS_M = 10_000
NEARBY_TOWNS = 6
# Most villages have no Wikipedia coordinate type, so the dull kinds go to the back instead of typed ones to the front.
DULL_TYPES = {"railwaystation", "edu", "airport"}
# Half the area stories try a topic about the area first, half the nearest untold place; each is the other's fallback.
TOPIC_SHARE = 0.5
# Two Gemma calls (~2 s each, sometimes 4-5) plus ~1 s of lookups. A quiet stretch can wait: the page gives up after
# 25 s, and 8 s lost area stories on the first slow minute of a real ride.
BUDGET_S = 20
EMPTY = {"place": None, "topic": None, "text": ""}


def _articles(lat: float, lon: float) -> list[dict]:
    params = {
        "action": "query",
        "list": "geosearch",
        "gscoord": f"{lat}|{lon}",
        "gsradius": RADIUS_M,
        "gslimit": 100,
        "gsprop": "type",
        "format": "json",
    }
    return story._get(f"{story.WIKI}/w/api.php", params).get("query", {}).get("geosearch", [])


def _nearest(articles: list[dict], seen: set[str]) -> dict | None:
    near = sorted((a for a in articles if a["dist"] <= NEAR_M), key=lambda a: (a.get("type") in DULL_TYPES, a["dist"]))
    fresh = [a["title"] for a in near if a["title"].lower() not in seen]
    facts = story._summary(fresh[0]) if fresh else ""
    return {"place": fresh[0], "topic": None, "facts": facts} if facts else None


def _topic(town: dict, articles: list[dict], told: list[str]) -> dict | None:
    nearby = [a["title"] for a in articles if a.get("type") == "city" and a["title"] != town["town"]]
    return topics.find(town, nearby[:NEARBY_TOWNS], told)


def _town(town: dict, seen: set[str]) -> dict | None:
    name = town["town"]
    if name.lower() in seen:
        return None
    return {"place": name, "topic": None, "facts": story._summary(name) or f"{name}, {town['country']}"}


def _subject(lat: float, lon: float, told: list[str]) -> dict | None:
    """{"place", "topic", "facts"}: the nearest untold place or a topic about the area, else the town itself."""
    seen = {t.lower() for t in told}
    with ThreadPoolExecutor(1) as pool:
        town = pool.submit(news.town_at, lat, lon)
        articles = _articles(lat, lon)

    def where() -> dict | None:
        # Photon down or no town here: the nearest place can still be told.
        try:
            return town.result()
        except (httpx.HTTPError, LookupError, ValueError):
            return None

    steps = [lambda: _nearest(articles, seen), lambda: (w := where()) and _topic(w, articles, told)]
    if random.random() < TOPIC_SHARE:
        steps.reverse()
    steps.append(lambda: (w := where()) and _town(w, seen))
    for step in steps:
        found = step()
        if found:
            return found
    return None


def _story(lat: float, lon: float, bike: str, thread: str | None, told: list[str], context: dict) -> dict:
    found = _subject(lat, lon, told)
    if not found:
        return EMPTY
    place, topic = found["place"], found["topic"]
    line = {**context, "place": place, "topic": topic, "base": found["facts"], "thread": thread}
    text = agent.live_line("area", bike, line)
    return {"place": place, "topic": topic, "text": text} if text else EMPTY


def tell(point: tuple[float, float], bike: str, thread: str | None, told: list[str], context: dict) -> dict:
    """{"place", "topic", "text"} about where the rider is right now, or EMPTY. Never raises."""
    lat, lon = round(point[0], 3), round(point[1], 3)
    pool = ThreadPoolExecutor(1)
    future = pool.submit(_story, lat, lon, bike, thread, told, context)
    pool.shutdown(wait=False)  # a slow Google fallback finishes in the background; the rider doesn't wait for it
    try:
        return future.result(timeout=BUDGET_S)
    except Exception as exc:  # noqa: BLE001 - slow, failed or garbled all mean the page stays quiet, never an error
        log.warning("area story failed: %s", type(exc).__name__)
        return EMPTY
