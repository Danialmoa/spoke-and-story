import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import agent, buddy, main, planner

HERE = "45.85601,9.39437"
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
    sent, replies, urls = [], [], []

    def post(url, headers, json, timeout):
        urls.append(url)
        sent.append(json)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return _Response(reply)

    monkeypatch.setattr(buddy.httpx, "post", post)
    monkeypatch.setattr(buddy.httpx, "get", lambda *a, **k: pytest.fail("the buddy reads nothing from Backboard"))
    monkeypatch.setattr(agent, "_assistant", lambda: "shared")
    yield sent, replies
    # Only the model's thread endpoints: no memories, no per-rider assistants.
    assert {url.removeprefix(agent.BACKBOARD_URL) for url in urls} <= {"/threads/messages", "/threads/tool-outputs"}


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


def test_buddy_hands_only_new_facts_back_to_the_phone(backboard):
    sent, replies = backboard
    answer = {
        "text": "Got it, flat it is! Where do we start?",
        "remember": ["hates climbs", "Rides a gravel bike", " "],
    }
    replies += [_done(json.dumps(answer))]

    reply = buddy.reply(
        [{"role": "user", "content": "I hate climbs, I ride a gravel bike"}], None, ["Hates climbs"], True
    )

    assert reply == {"type": "question", "text": answer["text"], "remember": ["Rides a gravel bike"]}
    assert "- Hates climbs" in sent[0]["system_prompt"] and '"remember"' in sent[0]["system_prompt"]
    assert sent[0]["assistant_id"] == "shared" and sent[0]["memory"] == "off"
    # Facts come back in the answer: no remember tool, so the model never needs a second tool step to plan.
    assert {t["function"]["name"] for t in sent[0]["tools"]} == {"search_place", "plan_routes"}
    assert sent[0]["content"] == "I hate climbs, I ride a gravel bike"


def test_new_facts_come_back_with_a_plan_too(backboard, monkeypatch):
    sent, replies = backboard
    replies += [
        _needs(_call("plan_routes", start="Lecco", end="Varenna", bike="road", priority="flat")),
        _done('{"best": "B", "text": "You hate climbs, so B.", "remember": ["Hates climbs"]}'),
    ]
    monkeypatch.setattr(planner, "plan", lambda req: _result())

    reply = buddy.reply([{"role": "user", "content": "Lecco to Varenna, I hate climbs"}], None, [], True)

    assert (reply["type"], reply["result"]["best"], reply["remember"]) == ("plan", "B", ["Hates climbs"])


def test_known_facts_are_used_without_remember_me_and_nothing_new_comes_back(backboard, monkeypatch):
    sent, replies = backboard
    replies += [
        _needs(_call("plan_routes", start="Lecco", distance_km=30, bike="gravel", priority="flat")),
        _done('{"best": "B", "text": "You hate climbs, so B.", "remember": ["Likes loops"]}'),
    ]
    monkeypatch.setattr(planner, "plan", lambda req: _result())

    reply = buddy.reply([{"role": "user", "content": "30 km loop from Lecco"}], None, ["Hates climbs", "Gravel bike"])

    assert "remember" not in reply and reply["result"]["explanation"] == "You hate climbs, so B."
    assert "- Hates climbs\n- Gravel bike" in sent[0]["system_prompt"]
    assert "Remember me" not in sent[0]["system_prompt"]


def test_a_tool_call_written_as_text_is_never_shown_to_the_rider(backboard, monkeypatch):
    sent, replies = backboard
    leaked = '<|tool_call>call:plan_routes{bike:<|"|>road<|"|>,end:<|"|>Varenna<|"|>}<tool_call|>'
    replies += [_needs(_call("search_place", text="Varenna")), _done(leaked), _done('{"text": "Road or gravel?"}')]
    monkeypatch.setattr(buddy.photon, "search", lambda text, near: [{"name": "Varenna, Italy"}])
    # Asked once more.
    assert buddy.reply([{"role": "user", "content": "To Varenna"}]) == {"type": "question", "text": "Road or gravel?"}
    assert len(sent) == 3 and sent[2]["content"] == "To Varenna"

    # Only once: then main.chat falls back to the simple chat.
    replies += [_done(leaked), _done(leaked)]
    with pytest.raises(ValueError):
        buddy.reply([{"role": "user", "content": "To Varenna"}], None, [], True)

    replies += [_needs(_call("plan_routes", start="Lecco", end="Varenna", bike="road", priority="flat")), _done(leaked)]
    monkeypatch.setattr(planner, "plan", lambda req: _result())
    reply = buddy.reply([{"role": "user", "content": "Lecco to Varenna"}])
    assert reply["result"]["explanation"] == "Route A looks like the best match for you."


def test_chat_sends_known_facts_to_the_buddy_and_new_ones_back_to_the_page(backboard, monkeypatch):
    sent, replies = backboard
    monkeypatch.setenv("BACKBOARD_API_KEY", "test")
    replies += [_done('{"text": "Road or gravel?", "remember": ["Loves lake views"]}')]
    body = {
        "messages": [{"role": "user", "content": "I love lake views"}],
        "known": ["Hates climbs"],
        "remember": True,
    }

    answer = client.post("/api/chat", json=body).json()

    assert answer == {"type": "question", "text": "Road or gravel?", "remember": ["Loves lake views"]}
    assert "- Hates climbs" in sent[0]["system_prompt"]


def test_chat_checks_known_facts():
    hi = [{"role": "user", "content": "hi"}]
    for bad in [{"known": ["x"] * 31}, {"known": ["x" * 301]}, {"remember": "maybe"}]:
        assert client.post("/api/chat", json={"messages": hi, **bad}).status_code == 422


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
