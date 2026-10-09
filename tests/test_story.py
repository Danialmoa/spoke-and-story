from app import agent, story
from app.planner import PlanRequest


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


def test_a_returning_rider_gets_new_angles_and_a_welcome_back(monkeypatch):
    monkeypatch.setattr(story, "_articles_near", lambda point: ARTICLES)
    monkeypatch.setattr(story, "_summary", lambda title: f"Lead of {title}")
    monkeypatch.setattr(story, "_section", lambda title: f"Another section of {title}")
    sent = {agent.OUTLINE_PROMPT: [], agent.CHAPTER_PROMPT: []}

    def fake_llm(instructions, content, temperature=0.3):
        sent[instructions].append(content)
        return {"intro": "Round three!"} if instructions == agent.OUTLINE_PROMPT else {"text": "A chapter."}

    monkeypatch.setattr(agent, "_complete", fake_llm)
    history = [
        {"place": "Old Tower", "gist": "the bell that fell on the mayor"},
        {"place": "Hamlet", "gist": "the cheese that won a war"},
    ]
    route = {"coords": _route_north(10), "distance_km": 10.0, "climb_m": 120}
    script = story.build(PlanRequest(start="Lecco", end="Midtown"), route, history, rides_before=2)

    assert [s["place"] for s in script["stories"]] == ["Lakeside", "Old Tower", "Midtown"]
    [outline] = sent[agent.OUTLINE_PROMPT]
    assert '"ride_number_on_this_route": 3' in outline
    assert "Already told on earlier rides" in outline and "- Old Tower: the bell that fell on the mayor" in outline
    assert '"name": "Old Tower", "facts": "Another section of Old Tower", "told_before": true' in outline
    tower = next(c for c in sent[agent.CHAPTER_PROMPT] if "Write chapter 2 (Old Tower)" in c)
    lake = next(c for c in sent[agent.CHAPTER_PROMPT] if "Write chapter 1 (Lakeside)" in c)
    assert "find a new angle" in tower and "Another section of Old Tower" in tower and "the cheese that won" in tower
    assert "find a new angle" not in lake and "Lead of Lakeside" in lake


def test_section_picks_a_real_section_and_falls_back_to_the_summary(monkeypatch):
    monks = "Built by monks who argued a lot. " * 6
    extract = f"The lead.\n\n== History ==\n\n=== Early years ===\n{monks}\n\n== Trivia ==\nShort.\n\n"
    extract += "== See also ==\n" + "Another tower\n" * 20
    monkeypatch.setattr(story, "_get", lambda url, params=None: {"query": {"pages": [{"extract": extract}]}})
    assert story._section("Old Tower") == f"Early years: {monks.strip()}"

    monkeypatch.setattr(story, "_get", lambda url, params=None: {"query": {"pages": [{"extract": "Only a lead."}]}})
    monkeypatch.setattr(story, "_summary", lambda title: "The summary.")
    assert story._section("Old Tower") == "The summary."


def test_build_writes_connected_chapters_pinned_to_route_points(monkeypatch):
    monkeypatch.setattr(story, "_articles_near", lambda point: ARTICLES)
    monkeypatch.setattr(story, "_summary", lambda title: f"Facts about {title}")
    chapter_calls = []

    def fake_llm(instructions, content, temperature=0.3):
        if instructions == agent.OUTLINE_PROMPT:
            assert "your current location" in content
            assert "Already told" not in content and "ride_number_on_this_route" not in content
            return {
                "thread": "Chasing the lake legend.",
                "beats": ["beat one", "beat two", "beat three"],
                "intro": "This is the journey to the lake!",
                "turn_lines": ["Plot twist: {turn}.", "No placeholder here.", "{turn} and {turn}"],
                "finish": "You made it!",
            }
        chapter_calls.append(content)
        if "Write chapter 2 (Old Tower)" in content:
            raise ValueError("The model did not answer with JSON")
        return {"text": "A chapter."}

    monkeypatch.setattr(agent, "_complete", fake_llm)
    route = {"coords": _route_north(10), "distance_km": 10.0, "climb_m": 120}
    script = story.build(PlanRequest(start="45.0,9.0", end="Midtown"), route)

    assert script["turn_lines"] == ["Plot twist: {turn}."]
    # Old Tower's chapter fails, so it's dropped; the others keep their route order.
    assert [(s["place"], s["text"]) for s in script["stories"]] == [
        ("Lakeside", "A chapter."),
        ("Midtown", "A chapter."),
    ]
    # Every chapter sees the whole tale, which is what lets them connect.
    assert len(chapter_calls) == 3
    assert all("Chasing the lake legend." in c and "beat three" in c for c in chapter_calls)
    assert any("Facts about Midtown" in c for c in chapter_calls)
    assert not any("Already told" in c or "new angle" in c for c in chapter_calls)


def test_a_failed_outline_still_leaves_the_chapters(monkeypatch):
    monkeypatch.setattr(story, "_articles_near", lambda point: ARTICLES)
    monkeypatch.setattr(story, "_summary", lambda title: "")

    def fake_llm(instructions, content, temperature=0.3):
        if instructions == agent.OUTLINE_PROMPT:
            raise ValueError("The model did not answer with JSON")
        return {"text": "A chapter."}

    monkeypatch.setattr(agent, "_complete", fake_llm)
    route = {"coords": _route_north(10), "distance_km": 10.0, "climb_m": 120}
    script = story.build(PlanRequest(start="Lecco", end="Midtown"), route)
    assert (script["intro"], script["turn_lines"]) == ("", [])
    assert len(script["stories"]) == 3


def test_an_overloaded_model_gets_one_retry(monkeypatch):
    calls = []

    def flaky(instructions, content, temperature=0.3):
        calls.append(1)
        if len(calls) == 1:
            raise agent.genai_errors.ServerError(503, {"error": {"message": "overloaded"}})
        return {"text": "A chapter."}

    monkeypatch.setattr(agent, "_complete", flaky)
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)
    assert agent._try_complete(agent.CHAPTER_PROMPT, "x", 0.9) == {"text": "A chapter."}
    assert len(calls) == 2  # one failure, then success
