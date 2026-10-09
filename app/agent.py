import json
import logging
import os
import re
import time
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
- start: where the ride starts (a town, address or place like a climbing gym, or "here" for the user's current location)
- bike: "road", "gravel" or "mtb"
- distance_km for a loop, OR end: the place they want to ride to
Nice to have (guess sensibly if the user doesn't say):
- priority: "scenic" (default), "flat" (least climbing, e.g. tired legs), "fast" (quickest ride) or "bike_lanes"
  (as much as possible on bike paths, away from traffic)
- prefer: any of "water", "forest", "views", "quiet"

Rules:
- If the user's current location is known and they don't name another start, use "here" as start and don't ask for it.
- Ask at most 2 questions in total, one short question per reply. Never ask for something the user already told you.
- Once you know start, bike and distance (or end), stop asking and plan.
- Reply with JSON only, one of:
  {"type": "question", "text": "<your question>"}
  {"type": "plan", "request": {"start": "...", "end": null, "distance_km": 50, "bike": "road", "priority": "scenic", "prefer": []}}"""

PICK_PROMPT = """You are Spoke & Story, picking the best bike ride for a rider from a few candidate routes.
Route fields: distance_km, duration_min, climb_m, max_grade_pct (steepest 200 m stretch), paved_pct,
quiet_pct (share on streets, paths, tracks and cycleways), bike_path_pct (share on separate bike paths; painted
lanes on roads don't count), water_km and forest_km (km of the route within 150 m of water / forest), viewpoints
(viewpoints within 300 m), score (scenery match for the rider's wishes, 0-1),
fits (false = more than 25% off the rider's distance; misfit says "too long" or "too short").
The rider's priority: "scenic" = best scenery for their wishes, "flat" = least climbing, "fast" = shortest ride time,
"bike_lanes" = most of the ride on bike paths.

Pick the route that best matches the rider's priority and wishes; prefer routes that fit.
If the priority is "bike_lanes", say how much of the pick is on bike paths.
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
- beats: for each place, in order, one sentence on what happens in that chapter and how it follows from the one before,
  said to the rider (it may be read aloud as it is). Use the place facts you are given.
- intro: 3-4 sentences that start the tale, like "This is the journey to ...". Joke about the distance or climbing.
- turn_lines: 6 short, playful ways to announce a turn (under 10 words each). Each one must contain {{turn}} exactly
  once; it gets replaced by an instruction like "turn left onto Via Roma", so the sentence must stay clear.
  Examples: "Hold that thought, {{turn}}!", "Plot twist: {{turn}}.", "Before I say anything else, {{turn}}."
- finish: 2-3 sentences that close the tale at the arrival.
- cliffhanger: one sentence said right after the finish: a teaser that leaves the tale open for the next ride on this
  route (like "Next time: the miller wants his bell back.").
If you get "Already told on earlier rides", the rider heard those on past rides: never repeat those facts or jokes.
A place marked "told_before" is a returning place: give its beat a new angle (a different fact, a "you again?"
callback, how far the rider has come since). If the ride has ride_number_on_this_route, the rider knows this route:
greet them as a regular in the intro (like "back on the lake road for round three?").
If you get "Last episode", this ride is the next episode of a series on this route, and ride_number_on_this_route is
its episode number. The intro opens like a series recap ("Previously, on the road to <destination>: ..."),
retelling how the last episode ended (its cliffhanger) in a few words, then says which episode this is and what came of
that cliffhanger. The thread moves the last one on with a new twist, in new words, and keeps its running joke. The
cliffhanger is a new one, never the last one again.
Reply with JSON only:
{{"thread": "...", "beats": ["..."], "intro": "...", "turn_lines": ["..."], "finish": "...", "cliffhanger": "..."}}"""

LIVE_PROMPT = f"""{VOICE}

You are riding along with the rider right now and say one line, live. You get what you have to say, the bike and the
moment: speed_kmh, top_speed_kmh, km_done, km_left, grade_pct (the slope right now), climb_done_m, local_time.
Use a number only when it is notable (a big top speed, a steep slope, almost there, lunchtime, getting late);
never read out a list of stats. Never repeat a line, a joke or an opening from "Already said".
Use only the facts you are given; never invent history, dates or numbers. Plain spoken English: no emojis, no lists.
Reply with JSON only: {{"text": "..."}}"""
LIVE_TASKS = {
    "chapter": "Tell the chapter of the tale for this place (place) as the rider rolls up to it. Use its Wikipedia "
    "facts (facts), follow its beat (beat) and keep the running joke (thread); bring it back to the ride. 50-90 words, "
    'and end with a hook to what comes next. If you get "Already told on earlier rides", the rider heard about this '
    'place on an earlier ride: never repeat that; find a new angle (a different fact, a "you again?" callback, how far '
    "they have come since).",
    "top_speed": "The rider just hit a new top speed. React in 1-2 sentences, at most 30 words: celebrate it and wink "
    "at safety without lecturing.",
    "grind": "The rider is crawling up a steep climb. Cheer them on in 1-2 sentences, at most 30 words.",
    "spot": "The rider is about to pass this spot (place); it is just ahead, so speak in the present tense. Say 1-2 "
    "playful sentences about it, at most 35 words, using only the facts in base. For example, dare them to stop for a "
    "coffee, a gelato or a beer, whatever fits.",
    "reroute": "The rider left the planned route and a new route was just found. Announce it like a plot twist in the "
    "tale, at most 30 words. No blame.",
    "area": "Tell a short chapter of the tale about where the rider is right now, using only the facts in base: about "
    "the place, or about the topic when there is one (base then comes from the Wikipedia article named in place). "
    "Continue the running joke (thread). 40-80 words, plain spoken English.",
}

TOPICS_PROMPT = """You help a cycling guide find things worth telling a rider about the area they are riding through.
You get the town, its region and country, the towns nearby, and what the rider was already told. Propose 6 varied
topics about this area that one Wikipedia article answers: history, transport, food, a local legend, nature, a famous
person, sport, architecture, or what a nearby town is known for. Stay within about 20 km. Never one already told, and
nothing generic.
For each topic:
- topic: a short Wikipedia search query in English with the place name in it, like "Bergamo funicular" or
  "Casoncelli pasta Bergamo"
- local: the same search in the country's main language
- place: the town or area it is about, written as in the query
Reply with JSON only: {"topics": [{"topic": "...", "local": "...", "place": "..."}]}"""

TASKS = {
    CHAT_PROMPT: "chat",
    PICK_PROMPT: "pick route",
    WEEKLY_PROMPT: "weekly ideas",
    OUTLINE_PROMPT: "story outline",
    LIVE_PROMPT: "live line",
    TOPICS_PROMPT: "area topics",
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
    """Either {"type": "question", "text"} or {"type": "plan", "request"}. location is "lat,lon" from the browser;
    the model only learns that it is known, and main.chat swaps it in for a "here" start."""
    conversation = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
    if location:
        conversation = f"(The user's current location is known.)\n\n{conversation}"
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


def _told(history: list[dict]) -> str:
    if not history:
        return ""
    lines = "\n".join(f"- {h['place']}: {h['gist']}" for h in history)
    return f"\n\nAlready told on earlier rides (place: what was told):\n{lines}"


def ride_outline(ride: dict, places: list[dict], history: list[dict] = (), last_episode: dict | None = None) -> dict:
    """The running tale, one beat per place, intro, turn lines, finish and cliffhanger. Chapters are written live, as
    the rider reaches each place (live_line "chapter"), so a ride only pays for the places it reaches.
    history: [{"place", "gist"}] the rider heard on earlier rides. last_episode: {"thread", "cliffhanger"} of the last
    ride on this route, which this one continues."""
    told = {h["place"] for h in history}
    short = []
    for p in places:
        short.append({"name": p["name"], "facts": p["facts"][:300]})
        if p["name"] in told:
            short[-1]["told_before"] = True
    content = f"Ride:\n{json.dumps(ride)}\n\nPlaces in order:\n{json.dumps(short, ensure_ascii=False)}{_told(history)}"
    if last_episode:
        content += (
            f"\n\nLast episode:\nThe running story: {last_episode['thread']}\n"
            f"It ended on this cliffhanger: {last_episode['cliffhanger']}"
        )
    return _try_complete(OUTLINE_PROMPT, content, 0.9)


def area_topics(area: dict, told: list[str]) -> list[dict]:
    """[{"topic", "local", "place"}]: Wikipedia searches about the rider's area, [] when the model fails.
    area: {"town", "region", "country", "nearby_towns"}."""
    content = f"The area:\n{json.dumps(area, ensure_ascii=False)}"
    if told:
        content += "\n\nAlready told:\n" + "\n".join(f"- {t}" for t in told)
    topics = _try_complete(TOPICS_PROMPT, content, 0.9).get("topics")
    if not isinstance(topics, list):
        return []
    return [
        {"topic": t["topic"], "place": t["place"], "local": str(t.get("local") or "")}
        for t in topics
        if isinstance(t, dict) and isinstance(t.get("topic"), str) and isinstance(t.get("place"), str)
    ]


def live_line(event: str, bike: str, context: dict) -> str:
    """One line said while riding ("" when the model fails). For a chapter, context["told_before"] is what the rider
    heard about this place on an earlier ride."""
    task = LIVE_TASKS[(context.get("reason") or "top_speed") if event == "speed" else event]
    skip = {"said", "told_before"}
    moment = {
        k: round(v, 1) if isinstance(v, float) else v for k, v in context.items() if v is not None and k not in skip
    }
    content = f"{task}\n\nBike: {bike}\n\nThe moment:\n{json.dumps(moment, ensure_ascii=False)}"
    if context.get("told_before"):
        content += _told([{"place": context.get("place") or "this place", "gist": context["told_before"]}])
    if context.get("said"):
        content += "\n\nAlready said:\n" + "\n".join(context["said"])
    text = _try_complete(LIVE_PROMPT, content, 0.9).get("text")
    return text.strip() if isinstance(text, str) else ""
