from fastapi.testclient import TestClient

from app import agent, main, story
from app.planner import PlanRequest

client = TestClient(main.app)


def _route_north(km: float) -> list:
    """Straight route going north from (45.0, 9.0), one point every ~100 m."""
    return [[45.0 + i * 0.0009, 9.0, 200] for i in range(int(km * 10) + 1)]


ARTICLES = [
    {"title": "Startville", "lat": 45.0005, "lon": 9.001},  # at the start: the intro covers it
    {"title": "Lakeside", "lat": 45.0270, "lon": 9.005},  # km 3, 390 m off the road
    {"title": "Farville", "lat": 45.0270, "lon": 9.05},  # 3.9 km off the road
    {"title": "Old Tower", "lat": 45.0451, "lon": 9.002},  # km 5, 160 m off
    {"title": "Hamlet", "lat": 45.0460, "lon": 9.008},  # km 5 too but farther off: loses to the tower
    {"title": "Midtown", "lat": 45.0631, "lon": 9.003},  # km 7
]


def test_places_along_and_choose_spread_stories_along_the_route():
    coords = _route_north(10)
    places = story.places_along(coords, ARTICLES + ARTICLES)
    assert sorted(p["name"] for p in places) == ["Hamlet", "Lakeside", "Midtown", "Old Tower", "Startville"]

    chosen = story.choose(places, total_m=10_000, n=3)
    assert [p["name"] for p in chosen] == ["Lakeside", "Old Tower", "Midtown"]
    assert [round(p["index"] / 10) for p in chosen] == [3, 5, 7]


def test_choose_prefers_places_not_told_on_earlier_rides():
    places = story.places_along(_route_north(10), ARTICLES)
    # Hamlet is farther off the road than Old Tower, but the rider hasn't heard about it yet.
    assert [p["name"] for p in story.choose(places, 10_000, 3, {"Old Tower"})] == ["Lakeside", "Hamlet", "Midtown"]
    # Nothing fresh left around km 5: the closest told place comes back.
    chosen = story.choose(places, 10_000, 3, {"Old Tower", "Hamlet"})
    assert [p["name"] for p in chosen] == ["Lakeside", "Old Tower", "Midtown"]


def test_a_returning_rider_gets_new_places_new_facts_and_a_welcome_back(monkeypatch):
    monkeypatch.setattr(story, "_articles_near", lambda point: ARTICLES)
    monkeypatch.setattr(story, "_summary", lambda title: f"Lead of {title}")
    monkeypatch.setattr(story, "_section", lambda title: f"Another section of {title}")
    sent = []

    def fake_llm(instructions, content, temperature=0.3):
        assert instructions == agent.OUTLINE_PROMPT
        sent.append(content)
        return {"intro": "Round three!", "beats": ["b1", "b2", "b3"]}

    monkeypatch.setattr(agent, "_complete", fake_llm)
    history = [
        {"place": "Old Tower", "gist": "the bell that fell on the mayor"},
        {"place": "Hamlet", "gist": "the cheese that won a war"},
    ]
    route = {"coords": _route_north(10), "distance_km": 10.0, "climb_m": 120}
    script = story.build(PlanRequest(start="Lecco", end="Midtown"), route, history, rides_before=2)

    [outline] = sent
    assert '"ride_number_on_this_route": 3' in outline
    assert "Already told on earlier rides" in outline and "- Old Tower: the bell that fell on the mayor" in outline
    assert '"name": "Old Tower", "facts": "Another section of Old Tower", "told_before": true' in outline
    # The live chapter gets the new facts and what was told before, so it can find a new angle.
    lake, tower, midtown = script["stories"]
    assert (tower["place"], tower["facts"]) == ("Old Tower", "Another section of Old Tower")
    assert tower["told_before"] == "the bell that fell on the mayor"
    assert (lake["facts"], "told_before" in lake, "told_before" in midtown) == ("Lead of Lakeside", False, False)


def test_a_repeat_ride_continues_the_last_episode_and_leaves_a_new_cliffhanger(monkeypatch):
    monkeypatch.setattr(story, "_articles_near", lambda point: ARTICLES)
    monkeypatch.setattr(story, "_summary", lambda title: f"Lead of {title}")
    sent = []
    outline = {"intro": "Episode three!", "thread": "The bell hunt goes on.", "cliffhanger": "The mayor wants it back."}
    monkeypatch.setattr(agent, "_complete", lambda instructions, content, temperature: sent.append(content) or outline)
    route = {"coords": _route_north(10), "distance_km": 10.0, "climb_m": 120}
    last = {"thread": "The rider hunts the lost bell.", "cliffhanger": "Next time: the bell rings in Varenna."}

    script = story.build(PlanRequest(start="Lecco", end="Varenna"), route, [], rides_before=2, last_episode=last)

    [prompt] = sent
    assert '"ride_number_on_this_route": 3' in prompt
    assert "Last episode" in prompt and "Next time: the bell rings in Varenna." in prompt
    assert "The rider hunts the lost bell." in prompt
    assert (script["intro"], script["thread"], script["cliffhanger"]) == (
        "Episode three!",
        "The bell hunt goes on.",
        "The mayor wants it back.",
    )

    # The page lost count of the rides but still has the last episode: this is at least episode 2.
    story.build(PlanRequest(start="Lecco", end="Varenna"), route, last_episode=last)
    assert '"ride_number_on_this_route": 2' in sent[1]


def test_story_endpoint_takes_the_rider_s_history_from_the_page(monkeypatch):
    built = []
    monkeypatch.setattr(
        story,
        "build",
        lambda req, route, history, rides_before, last_episode: built.append((history, rides_before, last_episode)),
    )
    told = [{"place": "Fiumelatte", "gist": "A short river."}]
    last = {"thread": "The bell hunt.", "cliffhanger": "The bell rings."}
    body = {"request": {"start": "Lecco", "end": "Varenna"}, "route": {"coords": []}}

    client.post("/api/story", json={**body, "history": told, "rides_before": 2, "last_episode": last})
    client.post("/api/story", json=body)

    assert built == [(told, 2, last), ([], 0, None)]
    for bad in [
        {"history": told * 31},
        {"rides_before": -1},
        {"rides_before": 1001},
        {"last_episode": {"thread": "x" * 1501, "cliffhanger": ""}},
    ]:
        assert client.post("/api/story", json={**body, **bad}).status_code == 422


def test_section_picks_a_real_section_and_falls_back_to_the_summary(monkeypatch):
    monks = "Built by monks who argued a lot. " * 6
    extract = f"The lead.\n\n== History ==\n\n=== Early years ===\n{monks}\n\n== Trivia ==\nShort.\n\n"
    extract += "== See also ==\n" + "Another tower\n" * 20
    monkeypatch.setattr(story, "_get", lambda url, params=None: {"query": {"pages": [{"extract": extract}]}})
    assert story._section("Old Tower") == f"Early years: {monks.strip()}"

    monkeypatch.setattr(story, "_get", lambda url, params=None: {"query": {"pages": [{"extract": "Only a lead."}]}})
    monkeypatch.setattr(story, "_summary", lambda title: "The summary.")
    assert story._section("Old Tower") == "The summary."


def test_build_answers_the_outline_and_each_place_s_facts_without_writing_chapters(monkeypatch):
    monkeypatch.setattr(story, "_articles_near", lambda point: ARTICLES)
    monkeypatch.setattr(story, "_summary", lambda title: f"Facts about {title}. " * 50)
    calls = []

    def fake_llm(instructions, content, temperature=0.3):
        calls.append(instructions)
        assert "your current location" in content
        assert "Already told" not in content and "ride_number_on_this_route" not in content
        assert "Last episode" not in content
        return {
            "thread": "Chasing the lake legend.",
            "beats": ["beat one", "beat two", "beat three"],
            "intro": "This is the journey to the lake!",
            "turn_lines": ["Plot twist: {turn}.", "No placeholder here.", "{turn} and {turn}"],
            "finish": "You made it!",
            "cliffhanger": "Next time: the legend swims back.",
        }

    monkeypatch.setattr(agent, "_complete", fake_llm)
    route = {"coords": _route_north(10), "distance_km": 10.0, "climb_m": 120}
    script = story.build(PlanRequest(start="45.0,9.0", end="Midtown"), route)

    # One LLM call: chapters are written live, only for the places the rider reaches.
    assert calls == [agent.OUTLINE_PROMPT]
    assert (script["thread"], script["intro"], script["finish"]) == (
        "Chasing the lake legend.",
        "This is the journey to the lake!",
        "You made it!",
    )
    assert script["turn_lines"] == ["Plot twist: {turn}."]
    assert script["cliffhanger"] == "Next time: the legend swims back."
    assert [(s["place"], s["beat"], round(s["index"] / 10)) for s in script["stories"]] == [
        ("Lakeside", "beat one", 3),
        ("Old Tower", "beat two", 5),
        ("Midtown", "beat three", 7),
    ]
    lake = script["stories"][0]
    assert lake["facts"].startswith("Facts about Lakeside.") and len(lake["facts"]) == story.FACTS_CHARS
    assert "text" not in lake


def test_a_failed_outline_still_leaves_the_places_with_facts(monkeypatch):
    monkeypatch.setattr(story, "_articles_near", lambda point: ARTICLES)
    # Without facts or a beat there is nothing to tell about a place.
    monkeypatch.setattr(story, "_summary", lambda title: "" if title == "Old Tower" else f"Facts about {title}")

    def fake_llm(instructions, content, temperature=0.3):
        raise ValueError("The model did not answer with JSON")

    monkeypatch.setattr(agent, "_complete", fake_llm)
    route = {"coords": _route_north(10), "distance_km": 10.0, "climb_m": 120}
    script = story.build(PlanRequest(start="Lecco", end="Midtown"), route)
    assert (script["intro"], script["turn_lines"], script["cliffhanger"]) == ("", [], "")
    assert [(s["place"], s["beat"], s["facts"]) for s in script["stories"]] == [
        ("Lakeside", "", "Facts about Lakeside"),
        ("Midtown", "", "Facts about Midtown"),
    ]


def test_an_overloaded_model_gets_one_retry(monkeypatch):
    calls = []

    def flaky(instructions, content, temperature=0.3):
        calls.append(1)
        if len(calls) == 1:
            raise agent.genai_errors.ServerError(503, {"error": {"message": "overloaded"}})
        return {"text": "A chapter."}

    monkeypatch.setattr(agent, "_complete", flaky)
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    assert agent._try_complete(agent.LIVE_PROMPT, "x", 0.9) == {"text": "A chapter."}
    assert len(calls) == 2  # one failure, then success
