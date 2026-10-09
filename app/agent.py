import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from functools import cache

import httpx
import sentry_sdk
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

log = logging.getLogger("spokestory.agent")
BACKBOARD_URL = "https://app.backboard.io/api"
ASSISTANT = "Spoke & Story"

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

OUTLINE_PROMPT = f"""{VOICE}

Plan the spoken story for this whole ride: one continuous, funny tale told in chapters, one chapter at each place the
rider passes, in order. Write:
- thread: 2-3 sentences on the running story that ties the ride together (a quest, a rivalry, a legend the rider is
  chasing...). Keep it light, about this rider and this route.
- beats: for each place, in order, one sentence on what happens in that chapter and how it follows from the one before.
  Use the place facts you are given.
- intro: 3-4 sentences that start the tale, like "This is the journey to ...". Joke about the distance or climbing.
- turn_lines: 6 short, playful ways to announce a turn (under 10 words each). Each one must contain {{turn}} exactly
  once; it gets replaced by an instruction like "turn left onto Via Roma", so the sentence must stay clear.
  Examples: "Hold that thought, {{turn}}!", "Plot twist: {{turn}}.", "Before I say anything else, {{turn}}."
- finish: 2-3 sentences that close the tale at the arrival.
Reply with JSON only:
{{"thread": "...", "beats": ["..."], "intro": "...", "turn_lines": ["..."], "finish": "..."}}"""

CHAPTER_PROMPT = f"""{VOICE}

You are telling one continuous tale over a bike ride, one chapter per place. Write the chapter you are asked for, to say
as the rider rolls past that place: 4-6 sentences (60-100 words). Pick up from the previous beat, use this place's
facts, and end with a hook that leads to the next beat. Bring it back to the ride: the climb, the speed, their legs.
Use only facts from the given Wikipedia text; never invent history, dates or numbers.
Reply with JSON only: {{"text": "..."}}"""
# Chapters are written in parallel, so a different flavour per chapter keeps them from sounding alike.
FLAVOURS = [
    "a joke",
    "a playful dare",
    "a surprising fact told like gossip",
    "a bit of drama",
    "a cheeky compliment",
    "a tiny mystery",
]

LIVE_PROMPT = f"""{VOICE}

You are riding along with the rider right now and say one line, live. You get what you have to say, the bike and the
moment: speed_kmh, top_speed_kmh, km_done, km_left, grade_pct (the slope right now), climb_done_m, local_time.
Use a number only when it is notable (a big top speed, a steep slope, almost there, lunchtime, getting late);
never read out a list of stats. Never repeat a line, a joke or an opening from "Already said".
Use only the facts you are given; never invent history, dates or numbers. Plain spoken English: no emojis, no lists.
Reply with JSON only: {{"text": "..."}}"""
LIVE_TASKS = {
    "chapter": "Tell the chapter of the tale for this place (place) as the rider rolls up to it. Rewrite the "
    "prepared chapter (base) for this moment: keep its facts and the running joke (thread), follow its beat. "
    "50-90 words, and end with a hook to what comes next.",
    "top_speed": "The rider just hit a new top speed. React in 1-2 sentences, at most 30 words: celebrate it and wink "
    "at safety without lecturing.",
    "grind": "The rider is crawling up a steep climb. Cheer them on in 1-2 sentences, at most 30 words.",
    "spot": "The rider is about to pass this spot (place); it is just ahead, so speak in the present tense. Say 1-2 "
    "playful sentences about it, at most 35 words, using only the facts in base. For example, dare them to stop for a "
    "coffee, a gelato or a beer, whatever fits.",
    "reroute": "The rider left the planned route and a new route was just found. Announce it like a plot twist in the "
    "tale, at most 30 words. No blame.",
}

TASKS = {
    CHAT_PROMPT: "chat",
    PICK_PROMPT: "pick route",
    WEEKLY_PROMPT: "weekly ideas",
    OUTLINE_PROMPT: "story outline",
    CHAPTER_PROMPT: "story chapter",
    LIVE_PROMPT: "live line",
}


@cache
def _client() -> genai.Client:
    # google-genai has no timeout by default; a hung call would hold a server thread forever.
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(timeout=60_000))


def _headers() -> dict:
    return {"X-API-Key": os.environ["BACKBOARD_API_KEY"]}


@cache
def _assistant() -> str:
    """One assistant for every call: without an assistant_id each message creates a new "Default Assistant"."""
    r = httpx.get(f"{BACKBOARD_URL}/assistants", headers=_headers(), params={"name": ASSISTANT, "limit": 1}, timeout=30)
    r.raise_for_status()
    found = [a for a in r.json() if a["name"] == ASSISTANT]
    if found:
        return found[0]["assistant_id"]
    body = {
        "name": ASSISTANT,
        "system_prompt": "The Spoke & Story ride guide. Each message brings its own instructions.",
    }
    r = httpx.post(f"{BACKBOARD_URL}/assistants", headers=_headers(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()["assistant_id"]


def _backboard(instructions: str, content: str) -> str:
    """Gemma 4 through Backboard and OpenRouter: ~2 s per call, because those hosts run it without the
    "thinking" pass that Google AI Studio can't switch off (~20-40 s per call)."""
    model = os.environ.get("BACKBOARD_MODEL", "google/gemma-4-26b-a4b-it")
    body = {
        "content": content,
        "system_prompt": instructions,
        "assistant_id": _assistant(),
        "llm_provider": "openrouter",
        "model_name": model,
        "json_output": True,
        "memory": "off",
        "stream": False,
    }
    with sentry_sdk.start_span(op="gen_ai.chat", name=f"chat {model}") as span:
        r = httpx.post(f"{BACKBOARD_URL}/threads/messages", headers=_headers(), json=body, timeout=60)
        r.raise_for_status()
        reply = r.json()
        span.set_data("gen_ai.request.model", model)
        span.set_data("gen_ai.usage.input_tokens", reply.get("input_tokens"))
        span.set_data("gen_ai.usage.output_tokens", reply.get("output_tokens"))
    # Backboard answers HTTP 200 even when the model call failed; the status says so.
    if reply.get("status") != "COMPLETED":
        raise ValueError(f"Backboard status {reply.get('status')}")
    return reply.get("content") or ""


def _google(instructions: str, content: str, temperature: float) -> str:
    response = _client().models.generate_content(
        model=os.environ.get("LLM_MODEL", "gemma-4-26b-a4b-it"),
        contents=content,
        config=types.GenerateContentConfig(system_instruction=instructions, temperature=temperature),
    )
    return response.text


def _complete(instructions: str, content: str, temperature: float = 0.3) -> dict:
    """Backboard first when BACKBOARD_API_KEY is set, Google AI Studio otherwise or when Backboard fails.
    Backboard doesn't take a temperature, so it only applies to the Google path."""
    # Names the agent step in Sentry traces; the gen_ai spans inside come from _backboard or the google-genai integration.
    with sentry_sdk.start_span(op="function", name=f"agent: {TASKS.get(instructions, 'llm')}"):
        if os.environ.get("BACKBOARD_API_KEY"):
            try:
                return parse_json(_backboard(instructions, content))
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("Backboard call failed, using Google instead: %s", type(exc).__name__)
        return parse_json(_google(instructions, content, temperature))


def _try_complete(instructions: str, content: str, temperature: float) -> dict:
    """For the optional ride narration: a failed, slow or garbled reply just means fewer lines.
    A ServerError (Gemma's free tier answering "overloaded") gets retried after a short pause."""
    for attempt in range(3):
        try:
            return _complete(instructions, content, temperature)
        except genai_errors.ServerError as exc:
            log.warning("narration call failed: %s (attempt %d)", type(exc).__name__, attempt + 1)
            time.sleep(3 * (attempt + 1))
        except (genai_errors.APIError, httpx.TimeoutException, ValueError) as exc:
            log.warning("narration call failed: %s", type(exc).__name__)
            return {}
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
    hidden = {"coords", "profile", "steps"}
    if not has_scenery:
        # Without scenery data every score is 0, which the model read as "all routes have low scenery".
        hidden |= {"score", "water_km", "forest_km", "viewpoints"}
    stats = [{k: v for k, v in r.items() if k not in hidden} for r in routes]
    if request.get("end"):
        # distance_km is only the loop length; left in, the model judges A-to-B routes against the default 50 km.
        request = {k: v for k, v in request.items() if k != "distance_km"}
    content = f"Rider asked for:\n{json.dumps(request)}\n\nCandidates:\n{json.dumps(stats)}"
    if not has_scenery:
        content += "\n\nScenery data is unavailable: don't mention scenery scores, water, forest or views."
    pick = _complete(PICK_PROMPT, content)
    if pick.get("best") not in {r["id"] for r in routes}:
        pick["best"] = routes[0]["id"]
    return pick


def weekly_ideas(days: list[dict], bike: str) -> dict:
    return _complete(WEEKLY_PROMPT, f"Bike: {bike}\n\nForecast:\n{json.dumps(days)}")


def _chapter(ride: dict, outline: dict, places: list[dict], i: int) -> str:
    beats = outline.get("beats", [])
    plan = [
        {"chapter": n + 1, "place": p["name"], "beat": beats[n] if n < len(beats) else ""} for n, p in enumerate(places)
    ]
    content = (
        f"Ride:\n{json.dumps(ride)}\n\nThe tale: {outline.get('thread', '')}\n\n"
        f"Chapters:\n{json.dumps(plan, ensure_ascii=False)}\n\n"
        f"Write chapter {i + 1} ({places[i]['name']}). Flavour: {FLAVOURS[i % len(FLAVOURS)]}.\n"
        f"Facts about {places[i]['name']}:\n{places[i]['facts']}"
    )
    return _try_complete(CHAPTER_PROMPT, content, 0.9).get("text", "")


def ride_story(ride: dict, places: list[dict]) -> dict:
    """An outline first (the running tale, one beat per place, intro, turn lines, finish), then all chapters in
    parallel, each seeing the whole outline so they connect. One call writing everything took ~90 s.
    stories[i] belongs to places[i] ("" if that one failed)."""
    short = [{"name": p["name"], "facts": p["facts"][:300]} for p in places]
    outline = _try_complete(
        OUTLINE_PROMPT, f"Ride:\n{json.dumps(ride)}\n\nPlaces in order:\n{json.dumps(short, ensure_ascii=False)}", 0.9
    )
    # Four at a time: all eight at once made Gemma's free tier answer "overloaded" more often.
    with ThreadPoolExecutor(4) as pool:
        stories = list(pool.map(lambda i: _chapter(ride, outline, places, i), range(len(places))))
    return {**outline, "stories": stories}


def live_line(event: str, bike: str, context: dict) -> str:
    """One line said while riding ("" when the model fails; the page then plays its pre-written line)."""
    task = LIVE_TASKS[(context.get("reason") or "top_speed") if event == "speed" else event]
    moment = {
        k: round(v, 1) if isinstance(v, float) else v for k, v in context.items() if v is not None and k != "said"
    }
    content = f"{task}\n\nBike: {bike}\n\nThe moment:\n{json.dumps(moment, ensure_ascii=False)}"
    if context.get("said"):
        content += "\n\nAlready said:\n" + "\n".join(context["said"])
    text = _try_complete(LIVE_PROMPT, content, 0.9).get("text")
    return text.strip() if isinstance(text, str) else ""
