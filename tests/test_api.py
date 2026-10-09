from fastapi.testclient import TestClient

from app import agent, limits, main, tracing, usage

client = TestClient(main.app)


def test_events_are_counted_and_unknown_ones_rejected():
    before = usage.COUNTS["ride_start"]
    assert client.post(
        "/api/event", json={"event": "ride_start", "area": "45.9,9.4", "mode": "sim", "km": 29.2}
    ).json() == {"ok": True}
    assert client.get("/api/stats").json()["counts"]["ride_start"] == before + 1
    assert client.post("/api/event", json={"event": "something_else"}).status_code == 422


def test_event_area_must_be_rounded_so_exact_positions_and_addresses_never_reach_the_log():
    for area in ["45.856049,9.394366", "Via Roma 12 Lecco"]:
        assert client.post("/api/event", json={"event": "page_view", "area": area}).status_code == 422


def test_area_is_rounded_to_about_10_km():
    assert usage.area(45.856049, 9.394366) == "45.9,9.4"


def test_chat_reply_without_type_or_text_becomes_a_question(monkeypatch):
    messages = {"messages": [{"role": "user", "content": "hi"}]}
    monkeypatch.setattr(agent, "chat_step", lambda messages, location: {"text": "Road or gravel?"})
    assert client.post("/api/chat", json=messages).json() == {"type": "question", "text": "Road or gravel?"}
    monkeypatch.setattr(agent, "chat_step", lambda messages, location: {})
    assert client.post("/api/chat", json=messages).json()["type"] == "question"


def test_simple_chat_plans_from_here_with_the_position_kept_server_side(monkeypatch):
    plan = {"type": "plan", "request": {"start": "here", "distance_km": 30, "bike": "gravel"}}
    monkeypatch.setattr(agent, "chat_step", lambda messages, location: plan)
    planned = []
    monkeypatch.setattr(main, "_plan", lambda req, source: planned.append(req) or {"routes": []})
    body = {"messages": [{"role": "user", "content": "30 km gravel loop"}], "location": "45.85601,9.39437"}
    assert client.post("/api/chat", json=body).json()["type"] == "plan"
    assert planned[0].start == "45.85601,9.39437"


def test_sentry_events_lose_query_strings_and_bodies():
    event = {"request": {"url": "/api/places", "query_string": "q=Via+Roma+12&near=45.85,9.39", "data": {"start": "x"}}}
    assert tracing._scrub(event, {}) == {"request": {"url": "/api/places"}}


def test_rider_memory_endpoints_are_gone():
    """Everything personal lives on the rider's phone now; the server keeps no per-rider memory."""
    rider = {"rider": "6a1f4c3e-2b7d-4e8f-9a0b-1c2d3e4f5a6b"}
    for path in ("/api/rider/ride", "/api/rider/memories", "/api/rider/forget"):
        assert client.post(path, json=rider).status_code in (404, 405)
        assert path not in limits.LIMITED
    assert not hasattr(main, "memory")
