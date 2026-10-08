import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from functools import cache

import httpx
import sentry_sdk
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

log = logging.getLogger("spokestory.agent")

CHAT_PROMPT = """You are Spoke & Story, a friendly cycling buddy who plans scenic bike rides.
In this chat you find out what ride the user wants, then hand a plan request to the route planner.

You need:
- start: where the ride starts (a town, address or place like a climbing gym, or "lat,lon")
- bike: "road", "gravel" or "mtb"
- distance_km for a loop, OR end: the place they want to ride to
Nice to have (guess sensibly if the user doesn't say):
- priority: "scenic" (default), "flat" (least climbing, e.g. tired legs) or "fast" (quickest ride)
- prefer: any of "water", "forest", "views", "quiet"

Rules:
- If the user's current location is given and they don't name another start, use it as start and don't ask for it.
- Ask at most 2 questions in total, one short question per reply. Never ask for something the user already told you.
- Once you know start, bike and distance (or end), stop asking and plan.
- Reply with JSON only, one of:
  {"type": "question", "text": "<your question>"}
  {"type": "plan", "request": {"start": "...", "end": null, "distance_km": 50, "bike": "road", "priority": "scenic", "prefer": []}}"""

PICK_PROMPT = """You are Spoke & Story, picking the best bike ride for a rider from a few candidate routes.
Route fields: distance_km, duration_min, climb_m, max_grade_pct (steepest 200 m stretch), paved_pct,
quiet_pct (share on streets, paths, tracks and cycleways), water_km and forest_km (km of the route within 150 m
of water / forest), viewpoints (viewpoints within 300 m), score (scenery match for the rider's wishes, 0-1),
fits (false = more than 25% off the rider's distance; misfit says "too long" or "too short").
The rider's priority: "scenic" = best scenery for their wishes, "flat" = least climbing, "fast" = shortest ride time.

Pick the route that best matches the rider's priority and wishes; prefer routes that fit.
Warn the rider if the pick has a lot of unpaved surface on a road bike.
Explain the pick in 2-3 short, friendly sentences with concrete numbers, like a riding buddy would.
Reply with JSON only: {"best": "<route id>", "text": "<explanation>"}"""

WEEKLY_PROMPT = """You are Spoke & Story, a cycling buddy who plans the rider's week outdoors.
From the 7-day forecast, pick the best 1-3 days to ride and suggest a kind of ride for each
(for example: a long lake loop on a dry, calm day; a short forest loop when it's windy; skip the days it pours).
Keep it short and concrete, and mention the weather numbers.
Reply with JSON only:
{"summary": "<one sentence about the week>", "ideas": [{"date": "YYYY-MM-DD", "title": "<short ride idea>", "why": "<short reason>"}]}"""

VOICE = """You are the voice of Spoke & Story: a funny, warm riding buddy who talks to the rider through their earphones.
You joke, exaggerate, tease their legs on the climbs and cheer them on. Everything is read aloud:
no emojis, no markdown, short sentences."""

STORY_PROMPT = f"""{VOICE}

The rider is about to roll past a place. Write 2-3 sentences to say right then, in the flavour you are given.
Tie it to the ride: the climb, the speed, their legs, the view.
Use only facts from the given Wikipedia text. If there is none, be playful but never invent history, dates or numbers.
Tone example: "Coming up: Caprino. Wave at the bell tower. It has watched a thousand cyclists suffer up this hill
and it is still not impressed."
Reply with JSON only: {{"text": "..."}}"""
# Each place gets its own call, so a different flavour per place keeps the stories from sounding alike.
FLAVOURS = [
    "a joke",
    "a playful dare",
    "a surprising fact told like gossip",
    "a bit of drama",
    "a cheeky compliment",
    "a tiny mystery",
]

LINES_PROMPT = f"""{VOICE}

Write the fixed lines for this ride:
- intro: 2-3 sentences that kick off the adventure, like "This is the journey to ...". Joke about the distance or climbing.
- turn_lines: 6 short, playful ways to announce a turn (under 10 words each). Each one must contain {{turn}} exactly once; it gets replaced by an
  instruction like "turn left onto Via Roma", so the sentence must stay clear. Examples: "Hold that thought, {{turn}}!",
  "Plot twist: {{turn}}.", "Before I say anything else, {{turn}}."
- finish: 1-2 sentences celebrating the arrival.
Reply with JSON only: {{"intro": "...", "turn_lines": ["..."], "finish": "..."}}"""


TASKS = {
    CHAT_PROMPT: "chat",
    PICK_PROMPT: "pick route",
    WEEKLY_PROMPT: "weekly ideas",
    LINES_PROMPT: "story lines",
    STORY_PROMPT: "place story",
}


@cache
def _client() -> genai.Client:
    # google-genai has no timeout by default; a hung call would hold a server thread forever.
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(timeout=60_000))


def _complete(instructions: str, content: str, temperature: float = 0.3) -> dict:
    # Names the agent step in Sentry traces; the gen_ai span inside comes from sentry-sdk's google-genai integration.
    with sentry_sdk.start_span(op="function", name=f"agent: {TASKS.get(instructions, 'llm')}"):
        response = _client().models.generate_content(
            model=os.environ.get("LLM_MODEL", "gemma-4-26b-a4b-it"),
            contents=content,
            config=types.GenerateContentConfig(system_instruction=instructions, temperature=temperature),
        )
    return parse_json(response.text)


def _try_complete(instructions: str, content: str, temperature: float) -> dict:
    """For the optional ride narration: a failed, slow or garbled reply just means fewer lines."""
    try:
        return _complete(instructions, content, temperature)
    except (genai_errors.APIError, httpx.TimeoutException, ValueError) as exc:
        log.warning("narration call failed: %s", type(exc).__name__)
        return {}


def parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        raise ValueError(f"The model did not answer with JSON: {(text or '')[:200]}")
    return json.loads(match.group())


def chat_step(messages: list[dict], location: str | None = None) -> dict:
    """Either {"type": "question", "text"} or {"type": "plan", "request"}. location is "lat,lon" from the browser."""
    conversation = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
    if location:
        conversation = f"(The user's current location is {location}.)\n\n{conversation}"
    if sum(m["role"] == "assistant" for m in messages) >= 2:
        conversation += "\n\n(You already asked 2 questions. Reply with a plan now.)"
    return _complete(CHAT_PROMPT, conversation)


def pick_route(request: dict, routes: list[dict], has_scenery: bool = True) -> dict:
    stats = [{k: v for k, v in r.items() if k not in ("coords", "profile", "steps")} for r in routes]
    if request.get("end"):
        # distance_km is only the loop length; left in, the model judges A-to-B routes against the default 50 km.
        request = {k: v for k, v in request.items() if k != "distance_km"}
    content = f"Rider asked for:\n{json.dumps(request)}\n\nCandidates:\n{json.dumps(stats)}"
    if not has_scenery:
        content += "\n\nScenery data is unavailable: ignore water_km, forest_km and viewpoints, and don't mention them."
    pick = _complete(PICK_PROMPT, content)
    if pick.get("best") not in {r["id"] for r in routes}:
        pick["best"] = routes[0]["id"]
    return pick


def weekly_ideas(days: list[dict], bike: str) -> dict:
    return _complete(WEEKLY_PROMPT, f"Bike: {bike}\n\nForecast:\n{json.dumps(days)}")


def _place_story(ride: dict, place: dict, flavour: str) -> str:
    content = f"Ride:\n{json.dumps(ride)}\n\nFlavour: {flavour}\n\nPlace:\n{json.dumps(place, ensure_ascii=False)}"
    return _try_complete(STORY_PROMPT, content, 0.9).get("text", "")


def ride_story(ride: dict, places: list[dict]) -> dict:
    """Intro/turn lines/finish plus one story per place, all in parallel: a single call writing everything took ~90 s.
    stories[i] belongs to places[i] ("" if that one failed)."""
    with ThreadPoolExecutor(len(places) + 1) as pool:
        lines = pool.submit(_try_complete, LINES_PROMPT, f"Ride:\n{json.dumps(ride)}", 0.9)
        stories = [pool.submit(_place_story, ride, p, FLAVOURS[i % len(FLAVOURS)]) for i, p in enumerate(places)]
        return {**lines.result(), "stories": [s.result() for s in stories]}
