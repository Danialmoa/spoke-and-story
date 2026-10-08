from typing import Literal

from pydantic import BaseModel, Field

from . import agent, ors, route_stats, scenery

Preference = Literal["water", "forest", "views", "quiet"]
NO_SCENERY_NOTE = "(OpenStreetMap scenery data was unavailable, so water, forest and views were not compared.)"
MAX_ROUTES = 4
DISTANCE_TOLERANCE = 0.25
RANKING = {
    "scenic": lambda r: -r["score"],
    "flat": lambda r: r["climb_m"],
    "fast": lambda r: r["duration_min"],
}


class PlanRequest(BaseModel):
    start: str
    end: str | None = None
    distance_km: float = Field(50, gt=0, le=200)
    bike: Literal["road", "gravel", "mtb"] = "road"
    priority: Literal["scenic", "flat", "fast"] = "scenic"
    prefer: list[Preference] = []


def score(route: dict, req: PlanRequest) -> float:
    """Scenery score in [0, 1], averaged over what the rider asked for (everything if nothing)."""
    km = route["distance_km"]
    parts = {
        "water": route["water_km"] / km,
        "forest": route["forest_km"] / km,
        "views": min(route["viewpoints"], 5) / 5,
        "quiet": route["quiet_pct"] / 100,
    }
    wanted = req.prefer or list(parts)
    return round(sum(parts[p] for p in wanted) / len(wanted), 2)


def misfit(route: dict, req: PlanRequest) -> str | None:
    if req.end:
        return None
    off = route["distance_km"] - req.distance_km
    if abs(off) <= DISTANCE_TOLERANCE * req.distance_km:
        return None
    return "too long" if off > 0 else "too short"


def plan(req: PlanRequest) -> dict:
    start = ors.geocode(req.start)
    if req.end:
        end = ors.geocode(req.end, near=(start["lat"], start["lon"]))
        features = ors.a_to_b((start["lat"], start["lon"]), (end["lat"], end["lon"]), req.bike, req.priority)
    else:
        features = ors.loops(start["lat"], start["lon"], req.distance_km, req.bike, req.priority)

    routes = [route_stats.summarize(f) for f in features]
    if not req.end:
        routes = sorted(routes, key=lambda r: abs(r["distance_km"] - req.distance_km))[:MAX_ROUTES]
    extras = scenery.scenery([r["coords"] for r in routes])
    for route, extra in zip(routes, extras or [scenery.EMPTY] * len(routes)):
        route.update(extra)
        route["score"] = score(route, req)
        route["misfit"] = misfit(route, req)
        route["fits"] = route["misfit"] is None
    routes.sort(key=lambda r: (not r["fits"], RANKING[req.priority](r)))
    for route, route_id in zip(routes, "ABCDEFGH"):
        route["id"] = route_id
    # The agent's pick is slow (Gemma takes 15-25 s), so it's a separate call: routes show up first.
    return {"start": start, "routes": routes, "best": routes[0]["id"], "scenery": extras is not None}


def pick(req: PlanRequest, routes: list[dict], has_scenery: bool) -> dict:
    result = agent.pick_route(req.model_dump(), routes, has_scenery=has_scenery)
    explanation = result["text"] if has_scenery else f"{NO_SCENERY_NOTE} {result['text']}"
    return {"best": result["best"], "explanation": explanation}
