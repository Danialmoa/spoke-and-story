# GrassRoute

Scenic bike routes, picked by an open-source AI. Built for the DEV Hacktoberfest 2026 "Touch Grass" challenge.

## How it works

1. **OpenRouteService** builds 4 candidate loops from your start (or 3 alternatives from A to B), with elevation.
2. **OpenStreetMap** data (Overpass API) scores each route: km along water, km through forest, viewpoints nearby,
   share of quiet roads and paved surface. Steepest 200 m stretch comes from the elevation profile.
3. **Gemma 4** (open-weight, via Google AI Studio) is the agent:
   - **Navigate tab**: picks the best candidate and explains why.
   - **Chat tab**: asks up to 2 questions, then plans the ride.
   - **This week**: reads the 7-day forecast (Open-Meteo) and suggests which days to ride and what kind of ride.

## Run locally

```bash
cp .env.example .env   # add ORS_API_KEY and GEMINI_API_KEY
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://localhost:8000.

## API

| Endpoint | What it does |
|---|---|
| `POST /api/plan` | `{"start", "end", "distance_km", "max_climb_m", "bike", "prefer"}` → scored routes + best pick |
| `POST /api/chat` | `{"messages": [...]}` → a question, or a plan |
| `GET /api/weekly?place=Lecco&bike=road` | 7-day forecast + ride ideas |

## Tests

```bash
python -m pytest
```
