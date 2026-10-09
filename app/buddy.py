import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import sentry_sdk

from . import agent, memory, photon, planner
from .planner import PlanRequest

log = logging.getLogger("spokestory.buddy")
MAX_STEPS = 5
# The rider is waiting in the chat; after this the buddy answers with what it has.
LOOP_S = 30
TIMEOUT_S = 20
MAX_KNOWN = 30
HERE = "here"
# The model never sees the rider's position: it says "here" and plan_routes swaps in the real one. Nothing it could
# remember or repeat holds a GPS point.
HERE_WORDS = {HERE, "my location", "current location", "my current location"}
# Saving a memory takes 1-2 s; the rider doesn't wait for it.
_saver = ThreadPoolExecutor(2)

PROMPT = """You are Spoke & Story's ride buddy: a friendly cycling buddy who plans bike rides for the rider with tools.

To plan you need a start, a bike ("road", "gravel" or "mtb") and either an end (A to B) or a loop distance_km.
- start: a town, address or place. Use "here" for the rider's current location when it is known and they name no
  other start.
- priority: "scenic" (default), "flat" (least climbing), "fast" or "bike_lanes" (most of the ride on bike paths).
  Always "flat" when the rider has tired legs or dislikes climbs, now or in what you remember. "bike_lanes" when they
  want bike lanes or paths, or to stay away from traffic.
- prefer: any of "water", "forest", "views", "quiet".
- Fill in what the rider doesn't say this time from what you remember (their bike, priority, likes). What they say
  now wins.
- Ask at most 2 short questions in the whole chat, one per reply. Never ask for something you already know or
  remember. Once you know start, bike and distance (or end), call plan_routes.
- If a tool returns an error, fix the call or ask the rider.
- After plan_routes, pick the best route for the rider and explain the pick in 2-3 short, friendly sentences with
  concrete numbers. If you used something you remember, say so ("you hate climbs, so...").

Your final reply is JSON only, either {"text": "<your question>"} or, after planning,
{"best": "<route id>", "text": "<explanation>"}."""

REMEMBER_PROMPT = """
The rider turned on "Remember me". When they tell you something lasting about themselves (their bike, what they like
or hate on a ride, how far they like to ride...), call remember once per new fact, in a few words. Never remember
places they ride from or to, or anything already in the list below. Newer facts win over older ones.

What you remember about this rider, newest first:
{known}"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_place",
            "description": "Find places by name, to check one exists or which one the rider means.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Place name, e.g. 'Varenna'"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_routes",
            "description": "Build a few bike routes and return their stats: distance, time, climbing, steepest "
            "grade, paved, quiet and bike path share, scenery.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {"type": "string", "description": 'Place name, or "here" for the current location'},
                    "end": {"type": "string", "description": "Destination; leave out for a loop"},
                    "distance_km": {"type": "number", "description": "Loop length, 5-100 km"},
                    "bike": {"type": "string", "enum": ["road", "gravel", "mtb"]},
                    "priority": {"type": "string", "enum": ["scenic", "flat", "fast", "bike_lanes"]},
                    "prefer": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["water", "forest", "views", "quiet"]},
                    },
                },
                "required": ["start", "bike", "priority"],
            },
        },
    },
]
REMEMBER_TOOL = {
    "type": "function",
    "function": {
        "name": "remember",
        "description": "Save one lasting fact about the rider for future chats, e.g. 'Rides a gravel bike'.",
        "parameters": {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]},
    },
}


def _post(path: str, body: dict) -> dict:
    model = body.get("model_name") or "tool outputs"
    with sentry_sdk.start_span(op="gen_ai.chat", name=f"chat {model}") as span:
        r = httpx.post(f"{agent.BACKBOARD_URL}{path}", headers=agent._headers(), json=body, timeout=TIMEOUT_S)
        r.raise_for_status()
        reply = r.json()
        span.set_data("gen_ai.usage.input_tokens", reply.get("input_tokens"))
        span.set_data("gen_ai.usage.output_tokens", reply.get("output_tokens"))
    if reply.get("status") not in {"COMPLETED", "REQUIRES_ACTION"}:
        raise ValueError(f"Backboard status {reply.get('status')}")
    return reply


def _save(rider: str, fact: str) -> None:
    try:
        memory.remember(rider, fact)
    except httpx.HTTPError as exc:
        log.warning("Couldn't save a memory: %s", type(exc).__name__)


def _instructions(messages: list[dict], location: str | None, known: list[str] | None) -> str:
    text = PROMPT
    if known is not None:
        text += REMEMBER_PROMPT.format(known="\n".join(f"- {k}" for k in known) or "(nothing yet)")
    text += "\n\nThe rider's current location is " + ('known: use start "here".' if location else "unknown.")
    if len(messages) > 1:
        text += "\n\nThe chat so far:\n" + "\n".join(f"{m['role']}: {m['content']}" for m in messages[:-1])
    if sum(m["role"] == "assistant" for m in messages) >= 2:
        text += "\n\n(You already asked 2 questions. Plan now.)"
    return text


def _routes_for_model(result: dict) -> list[dict]:
    hidden = {"coords", "profile", "steps", "score"}
    if not result["scenery"]:
        hidden |= {"water_km", "forest_km", "viewpoints"}
    return [{k: v for k, v in r.items() if k not in hidden} for r in result["routes"]]


def _plan(args: dict, location: str | None) -> tuple[dict, tuple]:
    args = {k: v for k, v in args.items() if v not in (None, "")}
    if str(args.get("start", HERE)).strip().lower() in HERE_WORDS:
        if not location:
            raise ValueError("The rider's location is unknown: ask where they start.")
        args["start"] = location
    req = PlanRequest(**args)
    result = planner.plan(req)
    start = "the rider's current location" if req.start == location else result["start"]["name"]
    return {"start": start, "routes": _routes_for_model(result)}, (req, result)


def _run(call: dict, location: str | None, rider: str | None, known: list[str]) -> tuple[dict, tuple | None]:
    """One tool call -> (output for the model, (request, result) when routes were planned)."""
    name = call["function"]["name"]
    try:
        args = json.loads(call["function"].get("arguments") or "{}")
        if name == "plan_routes":
            return _plan(args, location)
        if name == "search_place":
            near = photon.geocode(location) if location else None
            places = photon.search(args["text"], (near["lat"], near["lon"]) if near else None)
            return {"places": [p["name"] for p in places]}, None
        if name == "remember" and rider:
            fact = str(args["fact"]).strip()
            if fact and fact.lower() not in {k.lower() for k in known}:
                _saver.submit(_save, rider, fact)
                known.insert(0, fact)
            return {"ok": True}, None
        return {"error": f"Unknown tool: {name}"}, None
    except (ValueError, KeyError, TypeError) as exc:
        return {"error": str(exc)[:300]}, None
    except httpx.HTTPError as exc:
        log.warning("Ride buddy tool %s failed: %s", name, type(exc).__name__)
        return {"error": "That service isn't answering right now."}, None


def _answer(text: str | None) -> dict:
    try:
        return agent.parse_json(text)
    except ValueError:
        return {"text": (text or "").strip()}


def reply(messages: list[dict], location: str | None = None, rider: str | None = None) -> dict:
    """The ride buddy as an agent: Gemma 4 calls search_place / plan_routes (and remember, with Remember me on) in a
    loop. Same reply shape as the simple chat: {"type": "question", "text"} or {"type": "plan", "text", "request",
    "result"}; the result already carries the buddy's pick (best, explanation)."""
    known = memory.preferences(rider)[:MAX_KNOWN] if rider else []
    tools = TOOLS + [REMEMBER_TOOL] if rider else TOOLS
    body = {
        # Only the rider's newest words go in as the message; the rest of the chat is in the instructions.
        "content": messages[-1]["content"],
        "system_prompt": _instructions(messages, location, known if rider else None),
        "assistant_id": memory.rider_assistant(rider) if rider else agent._assistant(),
        "llm_provider": "openrouter",
        "model_name": os.environ.get("BACKBOARD_MODEL", "google/gemma-4-26b-a4b-it"),
        # OpenRouter's default upstream took 4-20 s per tool-calling step; the fastest one takes ~2 s.
        "openrouter": {"sort": "latency"},
        "tools": tools,
        "memory": "off",
        "stream": False,
    }
    with sentry_sdk.start_span(op="function", name="agent: ride buddy"):
        step = _post("/threads/messages", body)
        planned = None
        deadline = time.monotonic() + LOOP_S
        try:
            for _ in range(MAX_STEPS):
                if step["status"] != "REQUIRES_ACTION" or time.monotonic() > deadline:
                    break
                outputs = []
                for call in step["tool_calls"]:
                    if planned and call["function"]["name"] == "plan_routes":
                        output = {"error": "Already planned"}
                    else:
                        output, plan = _run(call, location, rider, known)
                        planned = plan or planned
                    outputs.append({"tool_call_id": call["id"], "output": json.dumps(output, ensure_ascii=False)})
                step = _post("/threads/tool-outputs", {"thread_id": step["thread_id"], "tool_outputs": outputs})
        # Same errors main.chat falls back on: with routes already planned, falling back would plan them again.
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            if not planned:
                raise
            log.warning("Ride buddy failed after planning, keeping its routes: %s", type(exc).__name__)
            step = {}

    answer = _answer(step.get("content")) if step.get("status") == "COMPLETED" else {}
    if planned:
        req, result = planned
        best = answer.get("best") if answer.get("best") in {r["id"] for r in result["routes"]} else result["best"]
        explanation = answer.get("text") or f"Route {best} looks like the best match for you."
        text = f"I found {len(result['routes'])} routes."
        return {
            "type": "plan",
            "text": text,
            "request": req.model_dump(),
            "result": {**result, "best": best, "explanation": explanation},
        }
    if not answer.get("text"):
        raise ValueError("The ride buddy gave no answer")
    return {"type": "question", "text": answer["text"]}
