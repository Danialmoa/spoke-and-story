import logging
from concurrent.futures import ThreadPoolExecutor

from . import agent, news, story

log = logging.getLogger("spokestory.area")
RADIUS_M = 2000
# Most villages have no Wikipedia coordinate type, so the dull kinds go to the back instead of typed ones to the front.
DULL_TYPES = {"railwaystation", "edu", "airport"}
BUDGET_S = 5
EMPTY = {"place": None, "text": ""}


def _articles(lat: float, lon: float) -> list[str]:
    params = {
        "action": "query",
        "list": "geosearch",
        "gscoord": f"{lat}|{lon}",
        "gsradius": RADIUS_M,
        "gslimit": 10,
        "gsprop": "type",
        "format": "json",
    }
    found = story._get(f"{story.WIKI}/w/api.php", params).get("query", {}).get("geosearch", [])
    return [a["title"] for a in sorted(found, key=lambda a: (a.get("type") in DULL_TYPES, a["dist"]))]


def _subject(lat: float, lon: float, told: set[str]) -> tuple[str, str] | None:
    """(place, facts): the nearest Wikipedia article not told yet, else the town around the point."""
    fresh = [title for title in _articles(lat, lon) if title not in told]
    if fresh:
        facts = story._summary(fresh[0])
        if facts:
            return fresh[0], facts
    town, country = news.town_at(lat, lon)
    if town in told:
        return None
    return town, story._summary(town) or f"{town}, {country}"


def _story(lat: float, lon: float, bike: str, thread: str | None, told: list[str], context: dict) -> dict:
    found = _subject(lat, lon, set(told))
    if not found:
        return EMPTY
    place, facts = found
    text = agent.live_line("area", bike, {**context, "place": place, "base": facts, "thread": thread})
    return {"place": place, "text": text} if text else EMPTY


def tell(point: tuple[float, float], bike: str, thread: str | None, told: list[str], context: dict) -> dict:
    """{"place", "text"} about where the rider is right now, or EMPTY. Never raises."""
    lat, lon = round(point[0], 3), round(point[1], 3)
    pool = ThreadPoolExecutor(1)
    future = pool.submit(_story, lat, lon, bike, thread, told, context)
    pool.shutdown(wait=False)  # a slow Google fallback finishes in the background; the rider doesn't wait for it
    try:
        return future.result(timeout=BUDGET_S)
    except Exception as exc:  # optional line: slow, failed or garbled all mean the page stays quiet, never an error
        log.warning("area story failed: %s", type(exc).__name__)
        return EMPTY
