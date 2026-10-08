from collections import defaultdict, deque
from time import monotonic

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# Endpoints that spend Backboard credit or ORS/Overpass quota.
LIMITED = {
    "/api/plan",
    "/api/pick",
    "/api/story",
    "/api/chat",
    "/api/weekly",
    "/api/live",
    "/api/spots",
    "/api/reroute",
}
MAX_CALLS = 30
WINDOW_S = 60
MAX_CLIENTS = 10_000
SLOW_DOWN = "Easy there, speedster! Too many requests in a minute. Drink some water and try again shortly."


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def install(app: FastAPI) -> None:
    calls: dict[str, deque] = defaultdict(deque)

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        if request.url.path not in LIMITED:
            return await call_next(request)
        # A client can send a new made-up X-Forwarded-For on every call; this keeps memory bounded.
        if len(calls) > MAX_CLIENTS:
            calls.clear()
        now = monotonic()
        recent = calls[client_key(request)]
        while recent and recent[0] <= now - WINDOW_S:
            recent.popleft()
        if len(recent) >= MAX_CALLS:
            return JSONResponse(status_code=429, content={"detail": SLOW_DOWN})
        recent.append(now)
        return await call_next(request)
