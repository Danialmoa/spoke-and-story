import logging
from pathlib import Path
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field

from . import agent, openmeteo, ors, planner, story, usage
from .planner import PlanRequest

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

app = FastAPI(title="GrassRoute")
INDEX = Path(__file__).parent.parent / "web" / "index.html"


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
    area: str | None = Field(None, max_length=20)
    mode: Literal["gps", "sim"] | None = None
    km: float | None = None


@app.exception_handler(ValueError)
def bad_input(_: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(httpx.HTTPStatusError)
def upstream_http(_: Request, exc: httpx.HTTPStatusError):
    detail = f"{exc.request.url.host} returned {exc.response.status_code}: {exc.response.text[:300]}"
    return JSONResponse(status_code=502, content={"detail": detail})


@app.exception_handler(genai_errors.APIError)
def upstream_llm(_: Request, exc: genai_errors.APIError):
    return JSONResponse(status_code=502, content={"detail": f"LLM error: {exc}"})


@app.get("/api/health")
def health():
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
    point = ors.geocode(near) if near else None
    return ors.search(q, (point["lat"], point["lon"]) if point else None)


@app.post("/api/pick")
def pick(body: PickRequest):
    return planner.pick(body.request, body.routes, body.scenery)


@app.post("/api/story")
def ride_story(body: StoryRequest):
    usage.track("story")
    return story.build(body.request, body.route)


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
    if step["type"] == "question":
        return {"type": "question", "text": step["text"]}
    # The model sometimes sends nulls (e.g. "distance_km": null for an A-to-B ride); let the defaults apply.
    req = PlanRequest(**{k: v for k, v in step["request"].items() if v is not None})
    result = _plan(req, source="chat")
    text = f"I found {len(result['routes'])} routes. Give me a moment to pick the best one for you."
    return {"type": "plan", "text": text, "request": req.model_dump(), "result": result}


@app.get("/api/weekly")
def weekly(place: str, bike: Literal["road", "gravel", "mtb"] = "road"):
    loc = ors.geocode(place)
    days = openmeteo.forecast(loc["lat"], loc["lon"])
    return {"place": loc, "days": days, **agent.weekly_ideas(days, bike)}


@app.get("/", response_class=HTMLResponse)
def index():
    # web/index.html is also published as an Artifact, which doesn't allow a doctype in the file.
    return "<!doctype html>\n" + INDEX.read_text()
