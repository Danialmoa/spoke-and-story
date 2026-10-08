# Spoke & Story

Scenic bike routes, picked by an open-source AI, with a riding buddy that tells you stories about the places you pass.
Built for the DEV Hacktoberfest 2026 "Touch Grass" challenge.

## How it works

1. **OpenRouteService** finds your start and destination (typo-tolerant, limited to 200 km around the start) and builds
   up to 3 routes from A to B, or several loops from one start, with elevation and turn-by-turn steps.
2. **OpenStreetMap** data (Overpass API) scores each route: km along water, km through forest, viewpoints nearby.
   Quiet-road and paved shares come from ORS. Scenery is optional: if Overpass doesn't answer within 8 s, the routes
   are ranked without it.
3. Routes are ranked by your priority: **scenic**, **flat** (least climbing) or **fast** (shortest ride time).
4. **Gemma 4** (open-weight, via Google AI Studio) is the agent:
   - picks the best route and explains why (a separate call, so routes show up before the 15-25 s pick),
   - chats: asks up to 2 questions, then plans the ride,
   - writes the ride story: an intro, playful turn lines and one short story per place you pass, using facts from
     Wikipedia articles near the route.

## Run locally

```bash
cp .env.example .env   # add ORS_API_KEY and GEMINI_API_KEY
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://localhost:8000.

## Deploy (Render)

`render.yaml` describes the service. Set `ORS_API_KEY`, `GEMINI_API_KEY` and (optionally) `SENTRY_DSN` in the Render
dashboard. The start command is:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT --no-access-log
```

`--no-access-log` keeps typed addresses and positions out of the logs (they travel in query strings).

## API

| Endpoint | What it does |
|---|---|
| `POST /api/plan` | `{"start", "end", "distance_km", "bike", "priority", "prefer"}` → scored routes, best first. `end: null` = loop (`distance_km` ≤ 100) |
| `POST /api/pick` | `{"request", "routes", "scenery"}` → Gemma's pick + explanation |
| `POST /api/story` | `{"request", "route"}` → spoken ride script: intro, turn lines, stories pinned to route points, finish |
| `POST /api/chat` | `{"messages": [...], "location": "lat,lon"}` → a question, or a plan |
| `GET /api/places?q=...&near=lat,lon` | place suggestions while typing |
| `POST /api/event` | anonymous page events (`page_view`, `ride_start`, `ride_finish`) with an area rounded to ~10 km |
| `GET /api/stats` | event counts since the server started |
| `GET /api/weekly?place=Lecco&bike=road` | 7-day forecast + ride ideas |

## Privacy

Usage is counted with a rough area (one decimal of latitude/longitude, about 10 km), never an exact position, an
address or an IP. Request URLs aren't logged.

## Tracing (Sentry)

Set `SENTRY_DSN` to trace every request: the Gemma calls (model, input/output/thinking tokens, latency) through
sentry-sdk's google-genai integration, plus timing spans for OpenRouteService, Overpass and Wikipedia. Prompts,
query strings and request bodies are not sent.

## Tests

```bash
pip install pytest
python -m pytest
```
