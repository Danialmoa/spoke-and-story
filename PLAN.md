# Spoke & Story: plan and progress

Bike route planner with a funny storytelling ride guide. Built for the **DEV Hacktoberfest 2026 Week 1 challenge
"Touch Grass"** (open-source AI at the core, a project that gets people outside).

- **Deadline: Sunday 11 Oct 2026.** Winners the week of 12 Oct.
- Submit one DEV post with tags `#devchallenge #hf26challenge` and the submission template. Writing quality counts most.
- Bonus points for taking it outside and telling how it went.
- Last updated: 8 Oct 2026, 22:30. Latest commit: `3826f00`. Nothing pushed yet (no GitHub remote).

## Where we are

### Works today (localhost:8000)

- **Where to?** tab: destination + from ("My location" by default) + priority (Scenic / Flattest / Fastest) + bike.
  - Suggestions while typing, tolerant of typos ("orobio climbing" finds Orobia climbing, Curno).
  - Routes show in ~5-8 s, then the agent's pick and explanation fill in (~2 s).
  - The button switches between "Let's search →" and "Start ride ▶".
  - Fun messages for empty fields. A jump-the-stones biker game plays while it loads.
- **Ride buddy** tab: a chat that asks at most 2 questions, then plans (loops like "50 km gravel loop" too).
- **Map:** minimal OpenStreetMap tiles (grey in light mode, dark in dark mode), satellite toggle, "you are here" dot.
- **Ride mode** (Start ride = GPS, Simulate = fast-forward):
  - Banner shows only real turns: heading change of 35° or more, so 66 steps become 13 announcements on Lecco to Orobia.
  - Bike-computer stats, off-route warning.
- **Story:** one connected tale per ride. An outline (running joke, one beat per place), then a chapter per place
  about every 3 km. Places come from Wikipedia near the route.
  - It's written in the background as soon as routes show (~11 s now).
  - Simulate waits up to 20 s for it, with a countdown.
- **Voice:** only Santa (Kokoro, open-weight, runs in the browser). Every line is generated in the background
  before the ride. The browser voice is used only when Kokoro can't run at all.
- **Usage counts:** anonymous, area rounded to ~10 km, at `/api/stats`. Sentry tracing is ready (needs a DSN).
- **Tests:** 31 backend tests, ruff clean. Headless-Chrome checks were run for every UI change.

### How it works

| Part | Uses | File |
|---|---|---|
| Place search, start/end lookup | **Photon** (open source, OSM, no key) with a 200 km limit around the start and a cache | `app/photon.py` |
| Routing (A to B, loops) | **OpenRouteService** directions only (its geocode quota ran out once) | `app/ors.py` |
| Route stats | climb, steepest 200 m, paved / quiet share | `app/route_stats.py` |
| Scenery score | **Overpass** (OSM): water, forest, viewpoints. Capped at 8 s because public servers often fail | `app/scenery.py` |
| Ranking | Scenic / Flattest / Fastest, loops within 25% of the distance | `app/planner.py` |
| LLM (pick, chat, story) | **Gemma 4 26B-A4B through Backboard → OpenRouter (~2 s)**. Google AI Studio is the fallback (~20-40 s) | `app/agent.py` |
| Story places | **Wikipedia** geosearch along the route + summaries | `app/story.py` |
| Voice | **Kokoro** (`am_santa`) in a web worker | `web/js/voice.js` |
| Ride, narration, turns | | `web/js/ride.js` |
| Search UI, suggestions, button | | `web/js/search.js` |
| Logs / stats / tracing | | `app/usage.py`, `app/tracing.py` |

### Run it

```bash
cd grassroute
source ~/Main/envs/main-env/bin/activate
uvicorn app.main:app --reload          # http://localhost:8000
python -m pytest -q && ruff check .    # tests + lint
```

`.env` holds `ORS_API_KEY`, `GEMINI_API_KEY`, `BACKBOARD_API_KEY`, `BACKBOARD_MODEL` and an optional `SENTRY_DSN`.
See `.env.example`.

## Decisions (and why)

| Decision | Why |
|---|---|
| Gemma 4 through **Backboard** (OpenRouter), Google as fallback | Same open model, ~2 s instead of 20-40 s (Google can't switch off "thinking"). Keeps the Gemma prize and adds the Backboard prize. ~$0.0005 per story, so $5 ≈ 10,000 stories. |
| One Backboard assistant "Spoke & Story" | Without an `assistant_id` every message creates a new "Default Assistant". |
| **Photon** for place search | ORS's free geocode quota ran out after a day of search-as-you-type. |
| **Kokoro, Santa only**, one voice | Open source and "local inference". Mixing in the browser voice sounded like two people. |
| Outline + chapters, written in the background | One connected tale; parallel calls keep it fast. |
| Only real turns are spoken | Turn-by-turn in towns was constant chatter. |
| Folder still named `grassroute/` | Renaming it would break the running server. Rename at deploy time. |

## Known issues / limits

- **Kokoro speed:** about 5-15 s to turn one line into audio, so lines are made ahead of time. A late turn line
  stays on the banner without voice.
- **Overpass** (scenery) is often down. Routes still come, with a "scenery unavailable" note.
- **Loops near mountains** (e.g. Lecco) come out longer than asked. A-to-B routes are fine.
- Mobile browsers pause pages when the screen locks. We keep the screen awake. Real screen-off audio needs a native app.
- **Clean-up:** 5 "Default Assistant"s in the Backboard account, left over from tests. Safe to delete.
- Someone else edited `game.js`, `logo.js`, `ui.js`, `styles.css` (pixel bike logo and game). Check who owns those
  before big changes there.

## Next steps

### 1. Live guide (next to build, ~2-3 h, two agents: backend + page)

Gemma now answers in ~2 s, so the guide can react **while riding**:

- [ ] **Chapters written live:** plan only the outline at the start (~3 s). Write each chapter shortly before the place,
      with live context (speed, the last climb, time of day, what was already said), so it never repeats.
- [ ] **Speed reactions:** above ~35 km/h downhill, a new top speed, or grinding a climb under 8 km/h. At most one
      comment every ~2 min.
- [ ] **Off route → reroute:** after ~15 s off route, plan a new route from the current position (ORS directions), swap
      it in, and say something fresh ("Plot twist, scenic detour…").
- [ ] **Fun places passed:** at ride start, find old restaurants, cafés, bars, pubs, gelato and historic spots near the
      route (OSM: amenity + `start_date` / `historic` / wikipedia tags). As the rider approaches one, write a short fun
      line about it.
- Timing: the text takes ~2 s but Santa's audio takes 5-15 s, so start each line ~20-40 s before the place
  (~150-250 m at riding speed).
- Build: a `POST /api/live` endpoint (event type + live context → a short line), a reroute endpoint, and
  `web/js/live.js`, which watches the ride and triggers events. Test it in Simulate and live.

### 2. Ship it (Friday)

- [ ] Create a GitHub repo (e.g. `spoke-and-story`) and push.
- [ ] Deploy on Render with `render.yaml` (starter plan, Python 3.12.8, `--no-access-log`). Set the env vars there.
- [ ] Optional: create a Sentry project, set `SENTRY_DSN` locally and on Render, and grab trace screenshots for the post.
- [ ] Rename the folder and the Render service if needed. Delete the leftover Backboard assistants.

### 3. Ride and record (Saturday)

- [ ] Ride a real route with the app (Start ride, GPS) and note what was fun or broken. This counts for the bonus.
- [ ] Record the demo video: search, routes, Simulate with Santa's story.

### 4. DEV post (Sunday)

- [ ] What it is and who it's for. The demo link and video. Code (GitHub).
- [ ] How it's built: the open models (Gemma 4, Kokoro), OpenStreetMap, Photon, Wikipedia, ORS.
- [ ] Why open innovation matters for it: every data source and model is open.
- [ ] The ride report (bonus). Prize categories used: **Gemma, Render, Backboard, Sentry**.
- [ ] A short privacy note: anonymous counts, ~10 km area, no exact location.

### 5. Real agent for Ride buddy (after the live guide)

- Today's chat is one LLM decision; our code does the rest. A real agent gets tools and decides itself, in a loop
  (max ~5 steps): `search_place`, `plan_routes(start, end, priority)`, `get_weather`, `find_stories`.
- Backboard supports this: tool calling, OpenAI format, `REQUIRES_ACTION` → submit tool outputs. Tested with Gemma 4:
  "legs are tired" → it called `plan_routes(priority="flat")` and then picked the flatter route (2.0 s + 3.5 s).
- Add **Backboard memory** so the buddy remembers the rider (bike, likes lake views, hates climbs). This fits the
  Backboard prize ("give an assistant memory").

### Later (Week 2 and beyond)

- **Fine-tuning:** see `docs/finetune/README.md`. Tinker can't train Gemma, so the plan is Gemma 4 E4B with Unsloth
  on Colab/Kaggle, optionally Qwen on Tinker for the Tinker prize. Goal: faster, more precise route picks and suggestions.
- A fast public site without Backboard: Gemma + Ollama on Cloud Run GPU (scales to zero).
- Bring back the weekly weather ideas (hidden in the page, `/api/weekly` still works).

## Ideas parked

- A clickable "grab a beer 🍺" button for an empty destination (rejected for now).
- Voice picker with several Kokoro voices (we kept Santa only).
