import logging
from pathlib import Path
from typing import Annotated, Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field

from . import agent, limits, openmeteo, ors, photon, planner, route_stats, spots, story, tracing, usage
from .planner import PlanRequest

load_dotenv()
tracing.init()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
# httpx logs full request URLs at INFO, which include the ORS key, typed addresses and GPS points.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)

app = FastAPI(title="Spoke & Story")
limits.install(app)
WEB = Path(__file__).parent.parent / "web"
Bike = Literal["road", "gravel", "mtb"]
Text = Annotated[str, Field(max_length=1500)]


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    location: str | None = None


class PickRequest(BaseModel):
    request: PlanRequest
    routes: list[dict]
    scenery: bool = True


class StoryRequest(BaseModel):
    request: PlanRequest
    route: dict


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


class SpotsRequest(BaseModel):
    coords: list[Annotated[list[float], Field(min_length=2, max_length=3)]] = Field(min_length=2)


class RerouteRequest(BaseModel):
    """points: the current position, points on the rest of the planned route, the end."""

    points: list[Annotated[list[float], Field(min_length=2, max_length=2)]] = Field(min_length=2, max_length=25)
    bike: Bike = "road"
    priority: Literal["scenic", "flat", "fast"] = "scenic"


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


@app.get("/api/health")
async def health():
    return {"ok": True}


def _plan(req: PlanRequest, source: str) -> dict:
    result = planner.plan(req)
    start = result["start"]
    kind = "a_to_b" if req.end else "loop"
    usage.track("plan", source=source, area=usage.area(start["lat"], start["lon"]), kind=kind, bike=req.bike)
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
    usage.track("story")
    return story.build(body.request, body.route)


@app.post("/api/live")
def live(body: LiveRequest):
    usage.track("live", kind=body.event)
    return {"text": agent.live_line(body.event, body.bike, body.context.model_dump())}


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
    usage.track("chat_message")
    step = agent.chat_step([m.model_dump() for m in body.messages], body.location)
    if step.get("type") != "plan":
        return {"type": "question", "text": step.get("text") or "Where would you like to ride?"}
    # The model sometimes sends nulls (e.g. "distance_km": null for an A-to-B ride); let the defaults apply.
    req = PlanRequest(**{k: v for k, v in (step.get("request") or {}).items() if v is not None})
    result = _plan(req, source="chat")
    text = f"I found {len(result['routes'])} routes. Give me a moment to pick the best one for you."
    return {"type": "plan", "text": text, "request": req.model_dump(), "result": result}


@app.get("/api/weekly")
def weekly(place: str, bike: Literal["road", "gravel", "mtb"] = "road"):
    loc = photon.geocode(place)
    days = openmeteo.forecast(loc["lat"], loc["lon"])
    return {"place": loc, "days": days, **agent.weekly_ideas(days, bike)}


@app.get("/", response_class=HTMLResponse)
def index():
    # web/index.html is also published as an Artifact, which doesn't allow a doctype in the file.
    return "<!doctype html>\n" + (WEB / "index.html").read_text()


app.mount("/", StaticFiles(directory=WEB), name="web")
