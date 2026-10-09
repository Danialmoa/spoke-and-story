import logging
import os
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field

from . import (
    agent,
    area,
    brief,
    buddy,
    limits,
    memory,
    openmeteo,
    ors,
    photon,
    planner,
    route_stats,
    spots,
    story,
    tracing,
    tts,
    usage,
)
from .planner import PlanRequest, Priority

load_dotenv()
tracing.init()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
# httpx logs full request URLs at INFO, which include the ORS key, typed addresses and GPS points.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)
log = logging.getLogger("spokestory.api")

app = FastAPI(title="Spoke & Story")
limits.install(app)
WEB = Path(__file__).parent.parent / "web"
Bike = Literal["road", "gravel", "mtb"]
Text = Annotated[str, Field(max_length=1500)]
Point = Annotated[list[float], Field(min_length=2, max_length=3)]


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: Text


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=20)
    location: str | None = None
    # The anonymous ID from "Remember me"; None = memory off.
    rider: UUID | None = None


class PickRequest(BaseModel):
    request: PlanRequest
    routes: list[dict]
    scenery: bool = True


class StoryRequest(BaseModel):
    request: PlanRequest
    route: dict
    rider: UUID | None = None


class Rider(BaseModel):
    rider: UUID


class Told(BaseModel):
    place: Annotated[str, Field(max_length=300)]
    gist: Annotated[str, Field(max_length=300)] = ""


class RideSummary(BaseModel):
    """Sent by the page when a ride with Remember me on ends: place names only, never a position."""

    rider: UUID
    start: Annotated[str, Field(max_length=200)] | None = None
    end: Annotated[str, Field(max_length=200)] | None = None
    told: list[Told] = Field([], max_length=12)


class Event(BaseModel):
    """Sent by the page for things only the browser sees. area is already rounded to ~10 km by the page."""

    event: Literal["page_view", "ride_start", "ride_finish"]
    area: str | None = Field(None, pattern=r"^-?\d{1,2}\.\d,-?\d{1,3}\.\d$")
    mode: Literal["gps", "sim"] | None = None
    km: float | None = None


class LiveContext(BaseModel):
    place: Text | None = None
    beat: Text | None = None
    base: Text | None = None
    thread: Text | None = None
    reason: Literal["top_speed", "grind"] | None = None
    speed_kmh: float | None = None
    top_speed_kmh: float | None = None
    km_done: float | None = None
    km_left: float | None = None
    grade_pct: float | None = None
    climb_done_m: float | None = None
    local_time: str | None = Field(None, pattern=r"^\d{1,2}:\d{2}$")
    said: list[Text] = Field([], max_length=3)


class LiveRequest(BaseModel):
    event: Literal["chapter", "speed", "spot", "reroute"]
    bike: Bike = "road"
    context: LiveContext = LiveContext()


class AreaRequest(BaseModel):
    point: tuple[Annotated[float, Field(ge=-90, le=90)], Annotated[float, Field(ge=-180, le=180)]]
    bike: Bike = "road"
    thread: Text | None = None
    # Place names already told this ride.
    told: list[Text] = Field([], max_length=40)
    context: LiveContext = LiveContext()


class SpotsRequest(BaseModel):
    coords: list[Point] = Field(min_length=2)


class RerouteRequest(BaseModel):
    """points: the current position, points on the rest of the planned route, the end."""

    points: list[Annotated[list[float], Field(min_length=2, max_length=2)]] = Field(min_length=2, max_length=25)
    bike: Bike = "road"
    priority: Priority = "scenic"


class BriefRoute(BaseModel):
    coords: list[Point] = Field(min_length=2, max_length=20_000)
    distance_km: float | None = None
    duration_min: float = Field(0, ge=0, le=1440)


class BriefRequest(BaseModel):
    start: Text
    end: Text | None = None
    route: BriefRoute


class SpeakRequest(BaseModel):
    text: Annotated[str, Field(min_length=1, max_length=tts.MAX_CHARS)]


@app.exception_handler(ValueError)
def bad_input(_: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(httpx.HTTPStatusError)
def upstream_http(_: Request, exc: httpx.HTTPStatusError):
    detail = f"{exc.request.url.host} returned {exc.response.status_code}: {exc.response.text[:300]}"
    return JSONResponse(status_code=502, content={"detail": detail})


@app.exception_handler(httpx.TimeoutException)
def upstream_timeout(_: Request, exc: httpx.TimeoutException):
    return JSONResponse(status_code=504, content={"detail": f"{exc.request.url.host} took too long to answer."})


@app.exception_handler(genai_errors.APIError)
def upstream_llm(_: Request, exc: genai_errors.APIError):
    return JSONResponse(status_code=502, content={"detail": f"LLM error: {exc}"})


@app.exception_handler(tts.Unavailable)
def voice_unavailable(_: Request, exc: tts.Unavailable):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/voice")
def voice():
    """Lets the page skip ElevenLabs when this server has no key or today's budget is spent."""
    return {"elevenlabs": tts.available()}


@app.post("/api/tts")
def speak(body: SpeakRequest, request: Request):
    """Not in limits.LIMITED: a ride voices a line every few minutes and a Kokoro fallback re-voices many. tts has its own caps."""
    return Response(tts.speak(body.text, limits.client_key(request)), media_type="audio/mpeg")


def _track_plan(req: dict, result: dict, source: str) -> None:
    start = result["start"]
    kind = "a_to_b" if req["end"] else "loop"
    usage.track("plan", source=source, area=usage.area(start["lat"], start["lon"]), kind=kind, bike=req["bike"])


def _plan(req: PlanRequest, source: str) -> dict:
    result = planner.plan(req)
    _track_plan(req.model_dump(), result, source)
    return result


@app.post("/api/plan")
def plan(req: PlanRequest):
    return _plan(req, source="form")


@app.get("/api/places")
def places(q: str = Query(min_length=2, max_length=100), near: str | None = None):
    """Suggestions while typing; near is "lat,lon" of the start, so nearby matches come first."""
    point = photon.geocode(near) if near else None
    return photon.search(q, (point["lat"], point["lon"]) if point else None)


@app.post("/api/pick")
def pick(body: PickRequest):
    return planner.pick(body.request, body.routes, body.scenery)


@app.post("/api/story")
def ride_story(body: StoryRequest):
    usage.track("story", memory=body.rider is not None)
    rides = memory.past_rides(str(body.rider)) if body.rider else []
    req = body.request
    history = [told for ride in rides for told in ride["told"]]
    # Rides are stored with place names only; loops from the same start are different rides.
    start, end = memory._place(req.start), memory._place(req.end)
    rides_before = sum(ride["start"] == start and ride["end"] == end for ride in rides) if end else 0
    return story.build(req, body.route, history=history, rides_before=rides_before)


@app.post("/api/live")
def live(body: LiveRequest):
    usage.track("live", kind=body.event)
    return {"text": agent.live_line(body.event, body.bike, body.context.model_dump())}


@app.post("/api/area")
def area_story(body: AreaRequest):
    """A story about where the rider is right now, for when nothing on the planned route is near."""
    usage.track("live", kind="area")
    return area.tell(body.point, body.bike, body.thread, body.told, body.context.model_dump())


@app.post("/api/spots")
def fun_spots(body: SpotsRequest):
    return {"spots": spots.along(body.coords)}


@app.post("/api/reroute")
def reroute(body: RerouteRequest):
    """No scenery here: Overpass is too slow for a rider waiting off route."""
    return {**route_stats.summarize(ors.through(body.points, body.bike, body.priority)), "id": "R"}


@app.post("/api/event")
def event(body: Event):
    usage.track(**body.model_dump(exclude_none=True))
    return {"ok": True}


@app.get("/api/stats")
def stats():
    return usage.stats()


@app.post("/api/chat")
def chat(body: ChatRequest):
    usage.track("chat_message", memory=body.rider is not None)
    messages = [m.model_dump() for m in body.messages]
    if os.environ.get("BACKBOARD_API_KEY"):
        try:
            reply = buddy.reply(messages, body.location, str(body.rider) if body.rider else None)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            log.warning("Ride buddy agent failed, using the simple chat: %s", type(exc).__name__)
        else:
            if reply["type"] == "plan":
                _track_plan(reply["request"], reply["result"], source="buddy")
            return reply
    step = agent.chat_step(messages, body.location)
    if step.get("type") != "plan":
        return {"type": "question", "text": step.get("text") or "Where would you like to ride?"}
    # The model sometimes sends nulls (e.g. "distance_km": null for an A-to-B ride); let the defaults apply.
    args = {k: v for k, v in (step.get("request") or {}).items() if v is not None}
    # Like the buddy, the model only knows "here"; the position goes in now.
    if body.location and str(args.get("start", buddy.HERE)).strip().lower() in buddy.HERE_WORDS:
        args["start"] = body.location
    req = PlanRequest(**args)
    result = _plan(req, source="chat")
    text = f"I found {len(result['routes'])} routes. Give me a moment to pick the best one for you."
    return {"type": "plan", "text": text, "request": req.model_dump(), "result": result}


@app.post("/api/rider/ride")
def rider_ride(body: RideSummary):
    usage.track("rider_ride")
    memory.remember_ride(str(body.rider), body.model_dump(exclude={"rider"}))
    return {"ok": True}


@app.post("/api/rider/memories")
def rider_memories(body: Rider):
    """What the buddy remembers, so the rider can see it. POST keeps the rider ID out of URLs and access logs."""
    usage.track("rider_memories")
    return {"memories": memory.memories(str(body.rider))}


@app.post("/api/rider/forget")
def rider_forget(body: Rider):
    usage.track("rider_forget")
    memory.forget(str(body.rider))
    return {"ok": True}


@app.get("/api/weekly")
def weekly(place: str, bike: Literal["road", "gravel", "mtb"] = "road"):
    loc = photon.geocode(place)
    days = openmeteo.forecast(loc["lat"], loc["lon"])
    return {"place": loc, "days": days, **agent.weekly_ideas(days, bike)}


@app.get("/", response_class=HTMLResponse)
def index():
    # web/index.html is also published as an Artifact, which doesn't allow a doctype in the file.
    return "<!doctype html>\n" + (WEB / "index.html").read_text()


@app.post("/api/brief")
def before_you_go(body: BriefRequest):
    usage.track("brief")
    return brief.build(body.start, body.end, body.route.model_dump())


app.mount("/", StaticFiles(directory=WEB), name="web")
