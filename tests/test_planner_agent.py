from app import agent, planner
from app.planner import PlanRequest, misfit, score


def _route(**overrides) -> dict:
    return {
        "distance_km": 50,
        "climb_m": 500,
        "water_km": 0,
        "forest_km": 0,
        "viewpoints": 0,
        "quiet_pct": 0,
    } | overrides


def test_score_follows_preferences():
    req = PlanRequest(start="Lecco", prefer=["water"])
    assert score(_route(water_km=20), req) == 0.4
    assert score(_route(forest_km=30), req) == 0.0


def test_misfit_only_for_loops_far_from_target_distance():
    req = PlanRequest(start="Lecco", distance_km=40)
    assert misfit(_route(distance_km=48), req) is None
    assert misfit(_route(distance_km=63), req) == "too long"
    assert misfit(_route(distance_km=25), req) == "too short"
    assert misfit(_route(distance_km=145), PlanRequest(start="Lecco", end="Varenna")) is None


def _feature(km: float, climb: float, minutes: float) -> dict:
    return {
        "geometry": {"coordinates": [[9.0, 45.0, 200.0], [9.0, 45.001, 200.0 + climb]]},
        "properties": {"summary": {"distance": km * 1000, "duration": minutes * 60}, "ascent": climb},
    }


def test_plan_keeps_loops_closest_to_distance_and_ranks_by_priority(monkeypatch):
    features = [_feature(145, 8000, 700), _feature(62, 2900, 220), _feature(44, 900, 140),
                _feature(38, 300, 150), _feature(41, 600, 120), _feature(28, 100, 90)]  # fmt: skip
    monkeypatch.setattr(planner.photon, "geocode", lambda place: {"name": place, "lat": 45.0, "lon": 9.0})
    monkeypatch.setattr(planner.ors, "loops", lambda *args: features)
    monkeypatch.setattr(planner.scenery, "scenery", lambda routes: None)

    flat = planner.plan(PlanRequest(start="Lecco", distance_km=40, priority="flat"))
    assert [(r["id"], r["distance_km"], r["fits"]) for r in flat["routes"]] == [
        ("A", 38.0, True), ("B", 41.0, True), ("C", 44.0, True), ("D", 28.0, False)
    ]  # fmt: skip
    assert (flat["best"], flat["scenery"]) == ("A", False)

    fast = planner.plan(PlanRequest(start="Lecco", distance_km=40, priority="fast"))
    assert [r["duration_min"] for r in fast["routes"]] == [120, 140, 150, 90]


def test_pick_without_scenery_data_hides_the_empty_scenery_numbers(monkeypatch):
    sent = []
    monkeypatch.setattr(agent, "_complete", lambda instructions, content: sent.append(content) or {"best": "A"})
    planner.pick(PlanRequest(start="Lecco"), [_route(id="A", score=0.0)], has_scenery=False)
    candidates = sent[0].split("Candidates")[1]
    assert '"score"' not in candidates and '"water_km"' not in candidates


def test_pick_falls_back_to_a_real_route(monkeypatch):
    monkeypatch.setattr(agent, "_complete", lambda instructions, content: {"best": "Z", "text": "Go with Z!"})
    result = planner.pick(PlanRequest(start="Lecco"), [_route(id="A"), _route(id="B")], has_scenery=False)
    assert result["best"] == "A"
    assert result["explanation"] == "Go with Z!"


def test_parse_json_handles_code_fences():
    text = 'Sure!\n```json\n{"type": "question", "text": "Road, gravel or MTB?"}\n```'
    assert agent.parse_json(text) == {"type": "question", "text": "Road, gravel or MTB?"}


def test_chat_step_passes_location_and_stops_asking_after_two_questions(monkeypatch):
    sent = []
    monkeypatch.setattr(agent, "_complete", lambda instructions, content: sent.append(content) or {})
    messages = [
        {"role": "user", "content": "take me to Varenna"},
        {"role": "assistant", "content": "Road, gravel or MTB?"},
        {"role": "user", "content": "road"},
    ]
    agent.chat_step(messages, "45.85,9.39")
    assert "current location is known" in sent[-1] and "45.85" not in sent[-1]
    assert "Reply with a plan now" not in sent[-1]

    agent.chat_step(
        messages + [{"role": "assistant", "content": "Any climb limit?"}, {"role": "user", "content": "no"}]
    )
    assert "Reply with a plan now" in sent[-1]


def test_pick_reply_without_text_still_answers(monkeypatch):
    monkeypatch.setattr(agent, "_complete", lambda instructions, content: {"best": "B"})
    assert planner.pick(PlanRequest(start="Lecco"), [_route(id="A"), _route(id="B")], has_scenery=True) == {
        "best": "B",
        "explanation": "",
    }


def test_pick_never_sends_the_riders_position(monkeypatch):
    sent = []
    monkeypatch.setattr(agent, "_complete", lambda instructions, content: sent.append(content) or {"best": "A"})
    planner.pick(PlanRequest(start="45.85601,9.39437", end="Varenna"), [_route(id="A")], has_scenery=True)
    assert "45.85" not in sent[0] and '"start": "your current location"' in sent[0]


def test_pick_hides_the_loop_distance_for_a_to_b_rides(monkeypatch):
    sent = []
    monkeypatch.setattr(agent, "_complete", lambda instructions, content: sent.append(content) or {"best": "A"})
    planner.pick(PlanRequest(start="Lecco", end="Curno"), [_route(id="A")], has_scenery=True)
    planner.pick(PlanRequest(start="Lecco", distance_km=40), [_route(id="A")], has_scenery=True)
    asked = [c.split("Candidates")[0] for c in sent]
    assert ["distance_km" in a for a in asked] == [False, True]
