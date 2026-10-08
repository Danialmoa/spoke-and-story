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


def test_build_pins_stories_to_route_points_and_drops_broken_turn_lines(monkeypatch):
    monkeypatch.setattr(story, "_articles_near", lambda point: ARTICLES)
    monkeypatch.setattr(story, "_summary", lambda title: f"Facts about {title}")
    story_calls = []

    def fake_llm(instructions, content, temperature=0.3):
        if instructions == agent.LINES_PROMPT:
            assert "your current location" in content
            lines = ["Plot twist: {turn}.", "No placeholder here.", "{turn} and {turn}"]
            return {"intro": "This is the journey to the lake!", "turn_lines": lines, "finish": "You made it!"}
        story_calls.append(content)
        if "Old Tower" in content:
            raise ValueError("The model did not answer with JSON")
        return {"text": "A story."}

    monkeypatch.setattr(agent, "_complete", fake_llm)
    route = {"coords": _route_north(10), "distance_km": 10.0, "climb_m": 120}
    script = story.build(PlanRequest(start="45.0,9.0", end="Midtown"), route)

    assert script["turn_lines"] == ["Plot twist: {turn}."]
    # Closest to the road win (Old Tower, Midtown); Old Tower's call fails, so only Midtown is left.
    assert [(s["place"], s["text"]) for s in script["stories"]] == [("Midtown", "A story.")]
    assert len(story_calls) == 2
    assert any("Facts about Midtown" in c for c in story_calls)
