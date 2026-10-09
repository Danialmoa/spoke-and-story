import httpx
from fastapi.testclient import TestClient

from app import agent, main, ors, scenery, spots, story, usage
from app.planner import PlanRequest

client = TestClient(main.app)


def _route_north(km: float) -> list:
    """Straight route going north from (45.0, 9.0), one point every ~100 m."""
    return [[45.0 + i * 0.0009, 9.0, 200] for i in range(int(km * 10) + 1)]


def test_live_line_gets_the_moment_and_what_was_already_said(monkeypatch):
    sent = []
    reply = {"text": " Those legs are writing poetry. "}
    monkeypatch.setattr(agent, "_complete", lambda instructions, content, temperature: sent.append(content) or reply)
    before = usage.COUNTS["live"]
    context = {"reason": "grind", "speed_kmh": 6.4321, "grade_pct": 9.0, "said": ["The ferry joke."]}

    body = {"event": "speed", "bike": "road", "context": context}
    assert client.post("/api/live", json=body).json() == {"text": "Those legs are writing poetry."}
    assert agent.LIVE_TASKS["grind"] in sent[0]
    assert '"speed_kmh": 6.4' in sent[0] and "The ferry joke." in sent[0]
    assert "km_left" not in sent[0]  # unknown numbers stay out of the prompt
    assert usage.COUNTS["live"] == before + 1


def test_a_live_chapter_is_written_from_the_place_facts_beat_and_thread(monkeypatch):
    sent = []
    monkeypatch.setattr(agent, "_complete", lambda instructions, content, temperature: sent.append(content) or {})
    context = {"place": "Old Tower", "facts": "Built by monks.", "beat": "The monks hid the map.", "thread": "A quest."}

    client.post("/api/live", json={"event": "chapter", "context": context})
    assert agent.LIVE_TASKS["chapter"] in sent[0]
    assert all(f'"{k}": "{v}"' in sent[0] for k, v in context.items())
    assert "Already told on earlier rides" not in sent[0].replace(agent.LIVE_TASKS["chapter"], "")

    # A place told on an earlier ride: what was told goes in, so the chapter finds a new angle.
    told = {**context, "told_before": "the bell that fell on the mayor"}
    client.post("/api/live", json={"event": "chapter", "context": told})
    assert (
        "Already told on earlier rides (place: what was told):\n- Old Tower: the bell that fell on the mayor" in sent[1]
    )
    assert '"told_before"' not in sent[1]


def test_live_answers_an_empty_line_when_the_model_fails(monkeypatch):
    def broken(instructions, content, temperature):
        raise ValueError("The model did not answer with JSON")

    monkeypatch.setattr(agent, "_complete", broken)
    assert client.post("/api/live", json={"event": "reroute", "bike": "mtb", "context": {}}).json() == {"text": ""}


def test_live_rejects_unknown_events_and_oversized_context():
    ok = {"event": "chapter", "bike": "road", "context": {"place": "Varenna"}}
    for bad in [
        {**ok, "event": "dance"},
        {**ok, "context": {"said": ["a", "b", "c", "d"]}},
        {**ok, "context": {"base": "x" * 1501}},
        {**ok, "context": {"facts": "x" * 1501}},
        {**ok, "context": {"local_time": "tea time"}},
    ]:
        assert client.post("/api/live", json=bad).status_code == 422


def _place(name: str, lat: float, lon: float = 9.0003, **tags) -> dict:
    return {"type": "node", "lat": lat, "lon": lon, "tags": {"name": name, "amenity": "cafe", **tags}}


def test_spots_prefer_old_and_fun_places_right_by_the_route_spread_out():
    elements = [
        _place("Start Bar", 45.0018),  # 200 m in: the ride has barely started
        _place("Bar Uno", 45.0180),  # km 2, loses to the old osteria next door
        _place("Antica Osteria", 45.0189, amenity="restaurant", start_date="1898", historic="yes"),
        _place("Far Pub", 45.0360, lon=9.002, amenity="pub"),  # 160 m off the road
        _place("Gelateria", 45.0450, amenity="ice_cream"),  # km 5
        _place("McCafe", 45.0630, brand="McDonald's"),
        {
            "type": "way",
            "center": {"lat": 45.0720, "lon": 9.0002},
            "tags": {"name": "Bierhaus", "amenity": "biergarten"},
        },
    ]
    found = spots.choose(_route_north(10), elements)
    assert [(s["name"], round(s["index"] / 10)) for s in found] == [
        ("Antica Osteria", 2),
        ("Gelateria", 5),
        ("Bierhaus", 8),
    ]
    assert found[0] == {
        "name": "Antica Osteria",
        "kind": "restaurant",
        "note": "restaurant, since 1898, historic",
        "lat": 45.0189,
        "lon": 9.0003,
        "index": found[0]["index"],
    }
    assert (found[2]["lat"], found[2]["lon"]) == (45.072, 9.0002)


def test_a_recent_opening_is_not_an_old_favourite():
    assert spots._rank({"amenity": "cafe", "start_date": "1952"}) == 0
    assert spots._rank({"amenity": "cafe", "start_date": "2024-01-30"}) == 2
    assert spots._note({"amenity": "ice_cream", "cuisine": "ice_cream;coffee_shop"}) == (
        "ice cream shop, food: ice cream, coffee shop"
    )


def test_spots_query_filters_tags_before_distance_on_a_simplified_route():
    query = spots._query(_route_north(3))
    # "around" first loads everything near the route; tags first keeps it within the declared memory.
    assert '["name"]->.c;nwr.c(around:90,45.00000,9.00000,45.02700,9.00000);' in query


def test_spots_endpoint_answers_an_empty_list_when_overpass_fails(monkeypatch):
    monkeypatch.setattr(scenery, "_overpass_within_budget", lambda query: None)
    assert client.post("/api/spots", json={"coords": _route_north(3)}).json() == {"spots": []}


def _feature() -> dict:
    return {
        "geometry": {"coordinates": [[9.0, 45.0, 200.0], [9.0, 45.001, 210.0]]},
        "properties": {"summary": {"distance": 111, "duration": 30}, "ascent": 10},
    }


def test_reroute_goes_through_the_points_in_order_and_answers_route_r(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ors, "_directions", lambda bike, priority, body: calls.append((bike, priority, body)) or [_feature()]
    )
    body = {"points": [[45.0, 9.0], [45.0005, 9.0001], [45.001, 9.0]], "bike": "gravel", "priority": "flat"}

    route = client.post("/api/reroute", json=body).json()
    assert calls == [("gravel", "flat", {"coordinates": [[9.0, 45.0], [9.0001, 45.0005], [9.0, 45.001]]})]
    assert (route["id"], route["distance_km"], route["climb_m"], route["coords"][0]) == ("R", 0.1, 10, [45.0, 9.0, 200])


def test_reroute_checks_points_and_passes_ors_errors_on(monkeypatch):
    for points in [[[45.0, 9.0]], [[45.0, 9.0]] * 26, [[45.0], [45.1, 9.0]]]:
        assert client.post("/api/reroute", json={"points": points, "bike": "road"}).status_code == 422

    def no_route(bike, priority, body):
        request = httpx.Request("POST", "https://api.openrouteservice.org/v2/directions")
        raise httpx.HTTPStatusError("no route", request=request, response=httpx.Response(404, request=request))

    monkeypatch.setattr(ors, "_directions", no_route)
    assert client.post("/api/reroute", json={"points": [[45.0, 9.0], [45.1, 9.0]]}).status_code == 502


def test_story_gives_the_live_guide_the_thread_beats_facts_and_place_coordinates(monkeypatch):
    articles = [{"title": "Lakeside", "lat": 45.027, "lon": 9.005}, {"title": "Midtown", "lat": 45.0631, "lon": 9.003}]
    monkeypatch.setattr(story, "_articles_near", lambda point: articles)
    monkeypatch.setattr(story, "_summary", lambda title: f"About {title}.")
    outline = {"thread": "Chasing the lake legend.", "beats": ["beat one"]}
    monkeypatch.setattr(agent, "_complete", lambda instructions, content, temperature: outline)
    route = {"coords": _route_north(10), "distance_km": 10.0, "climb_m": 120}
    script = story.build(PlanRequest(start="Lecco", end="Midtown"), route)
    assert script["thread"] == "Chasing the lake legend."
    assert [(s["place"], s["beat"], s["facts"], s["lat"], s["lon"]) for s in script["stories"]] == [
        ("Lakeside", "beat one", "About Lakeside.", 45.027, 9.005),
        ("Midtown", "", "About Midtown.", 45.0631, 9.003),
    ]
