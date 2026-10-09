import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import agent, buddy, main, memory, planner

HERE = "45.85601,9.39437"
RIDER = "6a1f4c3e-2b7d-4e8f-9a0b-1c2d3e4f5a6b"
client = TestClient(main.app)


class _Response:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _call(name: str, **args) -> dict:
    return {"id": f"call-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _needs(*calls) -> dict:
    return {"status": "REQUIRES_ACTION", "thread_id": "t1", "tool_calls": list(calls)}


def _done(content: str) -> dict:
    return {"status": "COMPLETED", "thread_id": "t1", "content": content}


@pytest.fixture
def backboard(monkeypatch):
    """Answers each Backboard call with the next scripted reply and keeps what was sent."""
    sent, replies = [], []

    def post(url, headers, json, timeout):
        sent.append(json)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return _Response(reply)

    monkeypatch.setattr(buddy.httpx, "post", post)
    monkeypatch.setattr(agent, "_assistant", lambda: "shared")
    monkeypatch.setattr(memory, "rider_assistant", lambda rider: "rider-assistant")
    return sent, replies


def _result() -> dict:
    routes = [
        {"id": "A", "distance_km": 31.0, "climb_m": 900, "coords": [[45.85, 9.39, 200]], "score": 0.0},
        {"id": "B", "distance_km": 29.0, "climb_m": 300, "coords": [[45.85, 9.39, 200]], "score": 0.0},
    ]
    return {"start": {"name": HERE, "lat": 45.85601, "lon": 9.39437}, "routes": routes, "best": "A", "scenery": False}


def test_buddy_plans_from_here_and_the_position_never_reaches_backboard(backboard, monkeypatch):
    sent, replies = backboard
    replies += [
        _needs(_call("plan_routes", start="here", distance_km=30, bike="gravel", priority="flat")),
        _done('{"best": "B", "text": "B is the flat one."}'),
    ]
    planned = []
    monkeypatch.setattr(planner, "plan", lambda req: planned.append(req) or _result())

    reply = buddy.reply([{"role": "user", "content": "30 km loop, tired legs"}], HERE)

    assert (planned[0].start, planned[0].priority, planned[0].bike) == (HERE, "flat", "gravel")
    assert reply["type"] == "plan" and reply["request"]["start"] == HERE
    assert (reply["result"]["best"], reply["result"]["explanation"]) == ("B", "B is the flat one.")
    assert "45.85" not in json.dumps(sent)
    assert sent[0]["memory"] == "off" and sent[0]["assistant_id"] == "shared"
    assert "remember" not in {t["function"]["name"] for t in sent[0]["tools"]}


def test_buddy_can_plan_for_bike_lanes(backboard, monkeypatch):
    sent, replies = backboard
    replies += [
        _needs(_call("plan_routes", start="Lecco", end="Varenna", bike="road", priority="bike_lanes")),
        _done('{"best": "A", "text": "A has the most bike paths."}'),
    ]
    planned = []
    monkeypatch.setattr(planner, "plan", lambda req: planned.append(req) or _result())

    buddy.reply([{"role": "user", "content": "To Varenna on bike lanes please"}])

    plan_routes = next(t for t in sent[0]["tools"] if t["function"]["name"] == "plan_routes")
    assert "bike_lanes" in plan_routes["function"]["parameters"]["properties"]["priority"]["enum"]
    assert planned[0].priority == "bike_lanes"


def test_buddy_remembers_only_new_facts_for_a_rider(backboard, monkeypatch):
    sent, replies = backboard
    replies += [
        _needs(_call("remember", fact="hates climbs"), _call("remember", fact="Rides a gravel bike")),
        _done('{"text": "Where do we start?"}'),
    ]
    saved = []
    monkeypatch.setattr(memory, "preferences", lambda rider: ["Hates climbs"])
    monkeypatch.setattr(memory, "remember", lambda rider, fact: saved.append(fact))
    monkeypatch.setattr(buddy._saver, "submit", lambda fn, *args: fn(*args))

    reply = buddy.reply([{"role": "user", "content": "I hate climbs, I ride a gravel bike"}], None, RIDER)

    assert reply == {"type": "question", "text": "Where do we start?"}
    assert saved == ["Rides a gravel bike"]
    assert "- Hates climbs" in sent[0]["system_prompt"] and sent[0]["assistant_id"] == "rider-assistant"
    assert sent[0]["content"] == "I hate climbs, I ride a gravel bike"


def test_tool_errors_go_back_to_the_model(backboard, monkeypatch):
    sent, replies = backboard
    replies += [_needs(_call("plan_routes", start="Atlantis", bike="road", priority="scenic")), _done("Where is that?")]

    def no_place(req):
        raise ValueError("Place not found: Atlantis")

    monkeypatch.setattr(planner, "plan", no_place)

    assert buddy.reply([{"role": "user", "content": "loop from Atlantis"}]) == {
        "type": "question",
        "text": "Where is that?",
    }
    assert "Place not found: Atlantis" in sent[1]["tool_outputs"][0]["output"]


def test_buddy_plans_once_and_keeps_the_plan_when_backboard_fails_after(backboard, monkeypatch):
    sent, replies = backboard
    plan = _call("plan_routes", start="Lecco", distance_km=30, bike="road", priority="scenic")
    replies += [_needs(plan), _needs(plan), httpx.ReadTimeout("slow")]
    planned = []
    monkeypatch.setattr(planner, "plan", lambda req: planned.append(req) or _result())

    reply = buddy.reply([{"role": "user", "content": "30 km loop from Lecco"}])

    assert len(planned) == 1
    assert "Already planned" in sent[2]["tool_outputs"][0]["output"]
    assert reply["type"] == "plan" and reply["result"]["best"] == "A"


def test_tool_loop_stops_after_max_steps(backboard, monkeypatch):
    sent, replies = backboard
    replies += [_needs(_call("search_place", text="Lecco"))] * (buddy.MAX_STEPS + 1)
    monkeypatch.setattr(buddy.photon, "search", lambda text, near: [{"name": "Lecco, Italy"}])

    with pytest.raises(ValueError):
        buddy.reply([{"role": "user", "content": "hi"}])
    assert len(sent) == buddy.MAX_STEPS + 1


def test_chat_falls_back_to_the_simple_chat_when_the_agent_fails(monkeypatch):
    monkeypatch.setenv("BACKBOARD_API_KEY", "test")

    def down(*args):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(buddy, "reply", down)
    monkeypatch.setattr(agent, "chat_step", lambda messages, location: {"type": "question", "text": "Road or gravel?"})
    answer = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]}).json()
    assert answer == {"type": "question", "text": "Road or gravel?"}
