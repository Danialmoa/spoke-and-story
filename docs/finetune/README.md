# Fine-tuning plan: faster, more precise route picking and route suggestions

This folder is the starting point for a new work session. Read it top to bottom once, then follow
"Step-by-step plan" at the end. Everything here was checked on 2026-10-08.

Files:
- `README.md`: this plan.
- `examples.jsonl`: 6 training examples (3 per task) in the exact format to generate.

---

## 1. Goal, in one paragraph

Today every "smart" step in Spoke & Story calls **Gemma 4 26B-A4B** through Google AI Studio (`app/agent.py`).
Each call takes **15-25 s**. We measured 18-25 s for `gemma-4-26b-a4b-it` and 16-20 s for `gemma-4-31b-it`
on the same pick prompt. AI Studio can't turn Gemma's "thinking" off for these models: both `thinking_budget`
and `thinking_level` return `"Thinking budget/level is not supported for this model"`. The model spends about
300 hidden thinking tokens before every answer, and that time is most of the wait.

**Goal:** train a **small** model that answers in **about 1-3 s** and is **at least as good** at two jobs:

1. **Route selection (pick).** Input: what the rider asked for, plus 2-4 candidate routes with stats.
   Output: the best route id and a 2-3 sentence explanation.
2. **Route suggestion (chat).** Input: the chat so far, and the rider's location if the browser shared it.
   Output: either one short question, or a full plan request the route planner can run.

The stories in ride mode stay on the big Gemma for now. They run in the background, so speed matters less there.

---

## 2. What the model sees and must answer today (exact formats)

Match these formats exactly in the training data. The small model will get the same input the app sends today.

### 2a. Pick: `agent.pick_route()` in `app/agent.py`

- System prompt: `PICK_PROMPT` in `app/agent.py`.
- User message, built in `pick_route()`:

  ```
  Rider asked for:
  {"start": "Lecco, Italy", "end": "Orobia Climbing", "distance_km": 50, "bike": "road", "priority": "scenic", "prefer": []}

  Candidates:
  [{"distance_km": 36.7, "duration_min": 95, "climb_m": 620, ..., "id": "A"}, ...]
  ```

  - The request is `PlanRequest` in `app/planner.py`: `start`, `end` (null for a loop), `distance_km` (max 100,
    because ORS round trips are limited to 100 km), `bike` (`road|gravel|mtb`), `priority` (`scenic|flat|fast`),
    `prefer` (subset of `water|forest|views|quiet`).
  - Each candidate has every field `planner.plan()` returns except `coords`, `profile` and `steps`:
    `distance_km, duration_min, climb_m, max_grade_pct, paved_pct, quiet_pct, water_km, forest_km, viewpoints,
    score, misfit, fits, id`.
  - When OpenStreetMap scenery failed, one more line is added:
    `Scenery data is unavailable: ignore water_km, forest_km and viewpoints, and don't mention them.`
- Expected answer: `{"best": "C", "text": "..."}`. If `best` isn't a real id, the code falls back to the
  first route.
- Where it's called: `planner.pick()` in `app/planner.py`, from `POST /api/pick` in `app/main.py`.
- Important: `planner.plan()` already **sorts** the routes by the rider's priority (`RANKING` in
  `app/planner.py`). Routes that fit come first, then the best by priority. So in production, route **A is the
  rule-based favourite**. The model's real value is (1) spotting the exceptions (for example a road bike on a
  route that is only 78% paved) and (2) explaining the choice well.

### 2b. Chat: `agent.chat_step()` in `app/agent.py`

- System prompt: `CHAT_PROMPT` in `app/agent.py`.
- User message: the **whole conversation as one text**, one line per turn:

  ```
  (The user's current location is 45.85590,9.39704.)

  user: take me to orobio climbing, my legs are tired
  assistant: Road, gravel or MTB?
  user: road
  ```

  - The location line only appears when the browser shared a location.
  - After 2 assistant turns, the code appends `(You already asked 2 questions. Reply with a plan now.)`.
- Expected answer, one of:
  - `{"type": "question", "text": "Road, gravel or MTB?"}`
  - `{"type": "plan", "request": {"start": "...", "end": null, "distance_km": 50, "bike": "road", "priority": "scenic", "prefer": []}}`
- Where it's called: `POST /api/chat` in `app/main.py`. Nulls in `request` are dropped, then the result is
  validated with `PlanRequest`. Bad values (for example `bike: "hybrid"`) become a 400 error.
- Place names: the model should **keep the rider's own words** for places ("orobio climbing"). It must not
  invent or "fix" them. The geocoder (`ors.search` in `app/ors.py`) already handles small typos near the start.

---

## 3. Can Tinker fine-tune Gemma? No. Pick a path.

**Tinker** (Thinking Machines, a sponsor: "Best Use of Tinker" needs a clear improvement in performance, latency
or cost over a baseline; you have a $10 credit) **does not support Gemma today.** Its model list is Inkling,
Nemotron 3/3.5, GLM-5.3, Kimi-K2.6, Qwen3/3.5/3.6/3.8, gpt-oss and DeepSeek-V3.1.
The smallest models it has are **Qwen3.5-4B** and **Qwen3-8B**.
Sources: [Tinker models & pricing](https://tinker-docs.thinkingmachines.ai/tinker/models/),
[Tinker page](https://thinkingmachines.ai/tinker/).

How Tinker works, in short:
- It only does **LoRA** fine-tuning. That means it trains a small add-on instead of changing all the weights.
- You write the training loop in Python (`forward_backward`, then `optim_step`), and Tinker runs it on its GPUs.
  See [Your first SFT](https://tinker-docs.thinkingmachines.ai/tutorials/basics/first-sft/).
- You can sample from the trained model through an **OpenAI-compatible API**. It's in beta, "for testing and
  internal use", so it isn't for production
  ([docs](https://tinker-docs.thinkingmachines.ai/tinker/compatible-apis/openai/)).
- You can download the LoRA weights and merge them into a normal Hugging Face model
  ([export](https://tinker-docs.thinkingmachines.ai/cookbook/deployment/export-hf/)).
- Cost for Qwen3.5-4B: $0.737 per million training tokens and $0.33/$1.005 per million prefill/sample tokens.
  2,000 examples × ~900 tokens × 3 epochs ≈ 5.4 M tokens ≈ **$4**. That fits the $10 credit.

**Gemma 4 has two small models** that fit this job: **E2B** (about 2B effective parameters) and **E4B** (about 4B).
Both have 128K context and an open license that allows commercial use
([Gemma 4 overview](https://ai.google.dev/gemma/docs/core)). **Unsloth** fine-tunes them on a **free Colab or
Kaggle T4 GPU** with QLoRA ([Unsloth Gemma 4 guide](https://unsloth.ai/docs/models/gemma-4/train)).
Self-hosted, you can turn thinking **off**, which is the main speed win
([Gemma thinking docs](https://ai.google.dev/gemma/docs/capabilities/thinking)).

| | Path A: Gemma 4 E4B + Unsloth (recommended) | Path B: Qwen3.5-4B on Tinker |
|---|---|---|
| Model family | Gemma, same as the app, so the "open Gemma" story holds | Qwen |
| Prize fit | Gemma category | Tinker category |
| Training cost | Free (Colab/Kaggle T4) | ~$4 of your $10 credit |
| Thinking | Off: you control the chat template | Off: Qwen "hybrid" models also allow no-think |
| Serving | GGUF → Ollama / llama.cpp, anywhere | Tinker OAI API (beta), or export → vLLM / GGUF |
| Risk | Free GPUs are not guaranteed | Beta inference API, not for production |

**Recommendation:** do **Path A** (Gemma 4 E4B, QLoRA, thinking off). Build the dataset once. If time is left,
train **Path B** on the same data and compare. That gives a clean "before/after" table for both prizes. If E4B is
too slow on the serving box, try E2B; it's about half the size.

---

## 4. Serving the tuned model (this is where the speed comes from)

The tuned model can't go back into AI Studio: AI Studio only serves Google's own models. You host it yourself.

| Option | Expected speed for one pick | Cost | Notes |
|---|---|---|---|
| **Ollama on your Mac** (demo) | ~1-3 s | Free | Best for the demo video and local testing |
| **Cloud Run with a GPU + Ollama** | ~1-2 s warm, slow cold start | Pay per GPU-second while an instance is up | Google's [official tutorial](https://docs.cloud.google.com/run/docs/tutorials/gpu-gemma-with-ollama) uses `gemma4:e2b` / `gemma4:e4b`. GPU instances bill for their whole lifetime, so set `--max-instances 1` |
| **Render, CPU only** | ~4-10 s (guess, measure it) | Bigger instance needed (~4 GB RAM for E4B Q4) | Render has **no GPUs**. OK for JSON-only answers, slow for explanations |
| **Tinker OAI API** (Path B only) | Unknown, beta | Per token | "Testing and internal use" only |
| **Hugging Face Inference Endpoint** | ~1-2 s warm | Hourly GPU price, can scale to zero | More setup, cold starts |

These speed numbers are estimates. **Measure p50/p95 yourself** (section 6).

Two more speed levers that come almost for free with fine-tuning:
- **Short system prompt.** Once the model learned the job, the long `PICK_PROMPT`/`CHAT_PROMPT` isn't needed.
  Train with a 2-3 line system prompt (see `examples.jsonl`). Fewer input tokens means a faster answer.
- **Short answers.** Train on 2-3 sentence explanations, not paragraphs.

### How the app switches to it (small change in `app/agent.py`)

Ollama, vLLM and Tinker all speak the **OpenAI chat API**, and the `openai` package is already installed in
`main-env`. Add three env vars and one branch in `_complete()`:

```python
# .env
LLM_PROVIDER=openai                       # "google" (default) keeps today's behaviour
LLM_BASE_URL=http://localhost:11434/v1    # Ollama; or the Cloud Run URL + /v1
PICK_MODEL=spoke-e4b                      # tuned model for /api/pick
CHAT_MODEL=spoke-e4b                      # tuned model for /api/chat
```

```python
@cache
def _openai():
    from openai import OpenAI
    return OpenAI(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ.get("LLM_API_KEY", "none"))


def _complete(instructions, content, temperature=0.3, model=None):
    if model and os.environ.get("LLM_PROVIDER") == "openai":
        r = _openai().chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[{"role": "system", "content": instructions}, {"role": "user", "content": content}],
        )
        return parse_json(r.choices[0].message.content)
    ...  # today's google-genai path, unchanged
```

Then `pick_route()` passes `model=os.environ.get("PICK_MODEL")` and the short tuned system prompt, and
`chat_step()` does the same with `CHAT_MODEL`. Keep `parse_json()`, the `best`-id fallback in `pick_route()` and
the `PlanRequest` validation in `main.py`: they are the safety net. Add `openai` to `requirements.txt`.

---

## 5. Dataset plan

Target: **~1,000 pick examples + ~1,000 chat examples**, plus a test set the model never sees.
Format: **JSONL, one example per line**, `{"task": ..., "messages": [system, user, assistant]}`.
Both Unsloth and the Tinker cookbook read this chat format. See `examples.jsonl`.

Put the code for this in a new top-level folder `finetune/` (scripts + `finetune/data/`). Keep `docs/finetune/`
for docs.

### 5a. Pick data: generated with our own pipeline

1. **Collect real candidate routes.** Write `finetune/make_pick_data.py`. It calls `planner.plan()` directly in
   Python, not over HTTP, and saves each raw result to `finetune/data/raw/<id>.json`, so every route is fetched
   only once.
   - Regions (start points): Lecco, Bergamo, Como, Milan, Brescia, Lake Garda (Riva), Trento, Bolzano,
     plus 2-3 outside Italy (Lyon, Munich, Ljubljana).
   - For each region: ~60 A-to-B rides (destinations from `ors.search()` near the start: towns, climbing gyms,
     lakes, cafés) and ~40 loops (20-100 km).
   - Randomize `bike`, `priority` and `prefer` per request.
   - Quota: ORS free is about 2,000 directions per day (check your dashboard). A loop uses 6 calls and A-to-B
     uses 1. Run it over 2 days, or mostly A-to-B. Overpass (scenery) is flaky; keep the result anyway with
     `scenery: false`. That's a useful training case too.
2. **Label the best route with rules first.** The label is the first fitting route by `planner.RANKING`, with one
   override. For `bike == "road"`, if the rule pick has `paved_pct < 85` and another fitting route is within 10%
   on the priority metric with `paved_pct >= 95`, pick that one.
3. **Write the explanation with a teacher.** Ask `gemma-4-31b-it` (AI Studio, slow is fine offline) to explain
   **the given label** in 2-3 sentences, in the app's tone, using only numbers from the stats.
4. **Auto-check every example.** Throw it away if:
   - it isn't valid JSON;
   - `best` isn't the label;
   - any number in `text` isn't in the stats (allow the same rounding);
   - it mentions water, forest or views when `scenery` is false;
   - it's longer than 3 sentences.
5. **Shuffle the candidate order** in half of the examples and give the routes new letters. Otherwise the model
   just learns "always say A". Keep the other half in production order (A first), so it also learns the real
   input.
6. **Human check:** read 100 random examples. Fix the rules or the teacher prompt if more than ~5% are wrong.

### 5b. Chat data: built from slots, so the labels are exact

1. Write `finetune/make_chat_data.py`. It builds each example from **slots** and computes the target JSON from
   those slots, so the answer is always right:
   - **start:** location given / town named / missing.
   - **destination:** town ("Varenna"), venue ("Orobia Climbing"), address, a typo ("orobio climbing"), or none
     (a loop).
   - **distance:** "50 km", "an hour" (→ ask), or none.
   - **bike words:** road / "bici da corsa" / gravel / mtb / "mountain bike" / missing (→ ask).
   - **priority cues:**
     - flat: "tired legs", "gambe stanche", "nothing steep"
     - fast: "quick", "in a hurry"
     - scenic: anything else
   - **prefer cues:**
     - water: lake, lago, river
     - forest: forest, woods, bosco
     - views: views, panorama
     - quiet: quiet, no traffic
   - **language:** English, Italian, mixed, with typos.
2. **Turn rules** (copied from `CHAT_PROMPT`):
   - Ask only for what's missing, one short question.
   - Ask at most 2 questions in total.
   - Use the location as the start when it's given.
   - Plan once start, bike and distance-or-destination are known.
   - Build 1-, 2- and 3-turn conversations. Include the "(You already asked 2 questions...)" line on turn 3.
3. **Variety:** ask the teacher model to **rephrase** the rider's message 3 ways while keeping every slot. The
   target JSON stays the one computed from the slots, so labels never drift.
4. Auto-check: valid JSON; `request` passes `PlanRequest(**request)`; questions never ask for a slot that's
   already filled.

### 5c. Split without leaking

- **Pick:** split by **region**. Keep two regions (for example Trento and Lake Garda) out of training as the
  test set. Same routes must never appear in both.
- **Chat:** split by **template family**. The test set uses message templates and phrasings the model never saw.
- Sizes: 80% train, 10% validation, 10% test. Freeze the test set before any training.

---

## 6. Evaluation

**Baseline to beat:** today's setup on the same test set: `gemma-4-26b-a4b-it` on AI Studio with the current
long prompts. Also run the **untuned** E4B with thinking off. That shows what the fine-tuning itself adds.

| Metric | Pick | Chat |
|---|---|---|
| Valid JSON rate | ✓ | ✓ |
| Agreement with the label (`best`) | ✓ | |
| Road bike + `paved_pct < 85` handled (switched or warned) | ✓ | |
| Explanation uses only numbers from the stats | ✓ | |
| Question vs plan correct (`type`) | | ✓ |
| Field accuracy: start, end, distance_km, bike, priority, prefer (as a set) | | ✓ |
| Never more than 2 questions in a conversation | | ✓ |
| Latency p50 / p95, measured from the app server | ✓ | ✓ |
| Cost per 1,000 calls | ✓ | ✓ |

Tiny eval script outline (`finetune/eval.py`):

```python
# python -m finetune.eval --task pick --model spoke-e4b --test finetune/data/pick_test.jsonl
for ex in read_jsonl(args.test):
    system, user, gold = ex["messages"]
    t = time.perf_counter()
    out = call_model(args.model, system["content"], user["content"])  # same _complete() path as the app
    latency.append(time.perf_counter() - t)
    pred = try_parse_json(out)
    score_example(args.task, pred, json.loads(gold["content"]), user["content"])
print(table(metrics), "p50", p50(latency), "p95", p95(latency))
```

"Done" means: the tuned model **matches or beats the baseline** on pick agreement and field accuracy, has a
JSON rate of at least 99%, makes **no invented numbers** on the test set, and has **p50 under 3 s** where the
demo runs.

---

## 7. Step-by-step plan

This fits the Week 2 challenge of the same Hacktoberfest series (Oct 12-18) **if its theme allows it**. Week 2's
theme is announced on Oct 12.

| Day | Work | Time |
|---|---|---|
| 1 | Write `finetune/make_pick_data.py` and start collecting routes (quota spreads over 2 days). Write `make_chat_data.py` | 4-5 h |
| 2 | Finish collection. Rule labels, teacher explanations, auto-checks. Human check of 100. Freeze the test set | 4-5 h |
| 3 | `finetune/eval.py`. Run the **baseline** (AI Studio 26B) and the untuned E4B on the test set | 3 h |
| 4 | Train E4B with Unsloth on Colab/Kaggle (QLoRA, about 30-60 min per run, 2-3 runs). Evaluate each run | 4 h |
| 5 | Export GGUF, run it in Ollama, add the `LLM_PROVIDER` switch in `agent.py`, measure latency. Optional: Path B on Tinker with the same data | 4 h |
| 6 | Write-up: before/after table (quality, p50/p95, cost) | 3 h |
| 7 | Buffer | |

Commands you will need:

```bash
# local env (always)
source ~/Main/envs/main-env/bin/activate

# Path A, in Colab/Kaggle: start from Unsloth's Gemma 4 E4B notebook, switch it to text-only.
#   https://colab.research.google.com/github/unslothai/notebooks/blob/main/nb/Gemma4_(E4B)-Vision.ipynb
#   - load "unsloth/gemma-4-E4B-it" in 4-bit; LoRA r=8..16; keep vision layers frozen
#   - chat template "gemma-4" (the NON-thinking one)
#   - train_on_responses_only(trainer, instruction_part="<|turn>user\n", response_part="<|turn>model\n")
#   - export: model.save_pretrained_gguf("spoke-e4b", tokenizer, quantization_method="q4_k_m")

# serve it locally
ollama create spoke-e4b -f Modelfile      # Modelfile: FROM ./spoke-e4b/<file>.gguf (+ the same chat template)
ollama run spoke-e4b

# Path B (optional): Tinker
uv pip install tinker tinker-cookbook     # or: pip install tinker tinker-cookbook
export TINKER_API_KEY=...                 # from the Tinker console; never commit it
#   base model "Qwen/Qwen3.5-4B", LoRA rank 16, learning rate ~2e-4 (their tutorial's value for 4B)
```

### Risks

- **Free GPUs aren't guaranteed** on Colab. Kaggle gives a weekly GPU quota and is the backup.
- **Thinking sneaks back in.** If the export or Ollama uses a thinking chat template, answers get slow again.
  Check that the number of output tokens per answer stays small.
- **The small model invents numbers.** The "numbers only from stats" check in the data and the eval catches this.
  Keep the training explanations short and factual.
- **Quotas:** ORS (directions per day) and AI Studio (teacher calls) are rate-limited. Cache everything you
  fetch in `finetune/data/raw/`.
- **Render can't run a GPU.** For the hosted demo, either keep AI Studio as the default and use the tuned model
  locally for the speed demo, or use Cloud Run with a GPU.
- **Prize rules:** the Tinker category requires training on Tinker, so it can't be Gemma. The Gemma category
  wants Gemma. Doing both paths covers both.

### Checklist

- [ ] `finetune/data/{pick,chat}_{train,val,test}.jsonl` exist, test sets frozen
- [ ] Baseline numbers recorded (AI Studio 26B + untuned E4B)
- [ ] Tuned E4B beats or matches the baseline on quality, p50 < 3 s
- [ ] `LLM_PROVIDER=openai` switch merged in `app/agent.py`, defaults unchanged
- [ ] Before/after table written for the post
