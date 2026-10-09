import json
import logging
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone

from . import agent, destination, news

log = logging.getLogger("spokestory.brief")
KINDS = {"news", "destination", "weather", "water", "repair"}
MAX_ITEMS = 4
# The lookups run in parallel and whatever isn't back by then is left out. GDELT is often much slower than the rest,
# so news gets a shorter wait; a late answer is cached for the next brief. Gemma took 2.5-15 s for this reply.
LOOKUP_S = 6
NEWS_WAIT_S = 2.5
LLM_S = 12
# Fountains are everywhere in some towns: a few km marks, at least this far apart, are enough to plan stops.
MARK_GAP_KM = 2
MAX_MARKS = 6
EMPTY = {"items": [], "spoken": ""}

BRIEF_PROMPT = f"""{agent.VOICE}

The rider is about to set off. Right after the opening of the tale you tell them what is going on before they roll,
using only the facts you get: headlines from the start town (any language), the destination, the weather when they
arrive, and the drinking water and bike repair stations along the route.

News: pick at most 2 headlines. First anything that changes the roads for a rider: a bike race, roadworks, closures.
Then the most entertaining: fun, surprising or curious local stories, events, festivals, markets, sports, and crime
only when it is odd or funny (a stolen cheese wheel, a runaway cow). Skip politics and boring admin news. Skip
tragedies, deaths, serious violence, accidents with victims and disasters, and every story tied to one, even its
lighter side (a pet saved from an explosion is still about an explosion). Never name private people: you joke about
this in a jolly voice. If no headline fits, leave the news out; never force a boring one.

Write:
- items: 2-4 lines for the screen, one sentence each, under 25 words, most useful first. Each one tells the rider
  something they can use, with the numbers and times given in digits (17:40, 21°C, km 2.2), and a wink. kind is one of "news", "destination",
  "weather", "water", "repair". Worth a line: a headline you picked; the weather when they arrive with its time and
  temperature (and lights if they arrive after sunset or within an hour of it, rain, strong wind); where the first
  water is, or that there is none on the map; a repair station; the destination's opening hours or what's on there.
  A "destination" line only when the destination is a venue; never a line that just names a place.
- spoken: a short scene that sounds like a chapter of the tale, 40-70 words, opening like "Before we roll...". With a
  fun headline, start there: "Word from the streets of Bergamo...". Connected sentences that weave the best facts
  together, never a list read out, and end with a hook into the ride.

Write in English whatever the language of the source; translate the headlines. Never invent facts: no events, times,
numbers or details that aren't given. Without headlines, say nothing about the town's news, gossip or mood.
Fewer items is fine when there is little worth saying.
Reply with JSON only: {{"items": [{{"kind": "...", "text": "..."}}], "spoken": "..."}}"""


def _is_coords(place: str | None) -> bool:
    try:
        [float(x) for x in (place or "").split(",")]
        return True
    except ValueError:
        return False


def _marks(stops: list[dict], kind: str) -> list[float]:
    marks = []
    for s in stops:
        if s["kind"] == kind and (not marks or s["km"] - marks[-1] >= MARK_GAP_KM):
            marks.append(s["km"])
    return marks[:MAX_MARKS]


def _facts(start: str, end: str | None, route: dict, found: dict) -> str | None:
    """What Gemma gets; None when there's nothing worth telling. A lookup that failed is left out, so "unknown"
    never reads as "none". Coordinates never go in: "My location" becomes the town's name."""
    local = found.get("news") or {}
    ride = {
        "from": local.get("town") if _is_coords(start) else start,
        "to": ("the spot picked on the map" if _is_coords(end) else end) if end else "a loop back to the start",
        "distance_km": route.get("distance_km"),
        "ride_time_min": route.get("duration_min"),
    }
    parts = []
    if local.get("headlines"):
        where = ", ".join(x for x in (local["town"], local["country"]) if x)
        parts.append(
            f"Headlines naming {where} from the last week:\n" + "\n".join(f"- {h}" for h in local["headlines"])
        )
    along = found.get("along") or {}
    if along.get("venue"):
        parts.append(f"The destination is a venue:\n{json.dumps(along['venue'], ensure_ascii=False)}")
    if found.get("weather"):
        parts.append(f"Weather at the destination when the rider arrives (local time):\n{json.dumps(found['weather'])}")
    if along.get("stops") is not None:
        for kind, label in (("water", "Drinking water fountains"), ("repair", "Bike repair stations")):
            count = sum(s["kind"] == kind for s in along["stops"])
            marks = _marks(along["stops"], kind)
            parts.append(f"{label} along the route: {count}" + (f", e.g. at km {marks}" if marks else ""))
    if not parts:
        return None
    # Told only in general, Gemma still made things up for missing facts: "Lecco is behaving itself today",
    # "no water on the map" when Overpass simply hadn't answered.
    if not local.get("headlines"):
        parts.append("No headlines today: say nothing about local news, gossip or the town's mood.")
    if along.get("stops") is None:
        parts.append("Water and repair stations are unknown today: don't mention them.")
    return "\n\n".join([f"Ride:\n{json.dumps(ride, ensure_ascii=False)}", *parts])


def _ask(content: str) -> dict:
    pool = ThreadPoolExecutor(1)
    future = pool.submit(agent._complete, BRIEF_PROMPT, content, 0.9)
    pool.shutdown(wait=False)  # a slow Google fallback finishes in the background; the brief doesn't wait for it
    try:
        return future.result(timeout=LLM_S)
    except Exception as exc:  # optional extra: slow, failed or garbled all mean "no brief", never an error
        log.warning("brief call failed: %s", type(exc).__name__)
        return {}


def _clean(reply: dict) -> dict:
    items = [
        {"kind": i["kind"], "text": i["text"].strip()}
        for i in reply.get("items") or []
        if isinstance(i, dict) and i.get("kind") in KINDS and isinstance(i.get("text"), str) and i["text"].strip()
    ]
    spoken = reply.get("spoken")
    return {"items": items[:MAX_ITEMS], "spoken": spoken.strip() if isinstance(spoken, str) else ""}


def build(start: str, end: str | None, route: dict) -> dict:
    """A "before you go" brief: {"items": [{"kind", "text"}], "spoken"}. Never raises: lookups that fail or are slow
    are left out, and EMPTY comes back when there's nothing to tell or Gemma fails."""
    coords = route["coords"]
    arrive = datetime.now(timezone.utc) + timedelta(minutes=route.get("duration_min") or 0)
    pool = ThreadPoolExecutor(3)
    lookups = {
        "news": pool.submit(news.near, coords[0][0], coords[0][1], NEWS_WAIT_S),
        "along": pool.submit(destination.along, None if _is_coords(end) else end, coords),
        "weather": pool.submit(destination.weather, coords[-1][0], coords[-1][1], arrive),
    }
    pool.shutdown(wait=False)
    wait(lookups.values(), timeout=LOOKUP_S)
    found = {}
    for name, future in lookups.items():
        if not future.done():
            log.info("brief lookup %s was too slow", name)
        elif future.exception():
            log.warning("brief lookup %s failed: %s", name, type(future.exception()).__name__)
        else:
            found[name] = future.result()
    content = _facts(start, end, route, found)
    return _clean(_ask(content)) if content else EMPTY
