import uuid

import pytest
from fastapi.testclient import TestClient

from app import main, memory, story

client = TestClient(main.app)


class _Response:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class FakeBackboard:
    """Just the assistant and memory endpoints memory.py uses."""

    def __init__(self):
        self.assistants: dict[str, str] = {}
        self.memories: dict[str, list[dict]] = {}
        self.added = 0

    def _path(self, url: str) -> list[str]:
        return url.removeprefix(memory.BACKBOARD_URL).strip("/").split("/")

    def get(self, url, headers, params, timeout):
        path = self._path(url)
        if path == ["assistants"]:
            return _Response(
                [{"assistant_id": a, "name": n} for a, n in self.assistants.items() if n == params["name"]]
            )
        return _Response({"memories": self.memories.get(path[1], [])})

    def post(self, url, headers, json, timeout):
        path = self._path(url)
        if path == ["assistants"]:
            assistant = f"a{len(self.assistants) + 1}"
            self.assistants[assistant] = json["name"]
            return _Response({"assistant_id": assistant})
        self.added += 1
        stored = {"id": f"m{self.added}", **json, "created_at": f"2026-10-09T10:00:{self.added:02d}"}
        self.memories.setdefault(path[1], []).append(stored)
        return _Response({"success": True})

    def delete(self, url, headers, timeout):
        path = self._path(url)
        if path[-1] == "memories":
            self.memories.pop(path[1], None)
        else:
            self.assistants.pop(path[1])
        return _Response({})


@pytest.fixture
def backboard(monkeypatch):
    fake = FakeBackboard()
    monkeypatch.setattr(memory, "_assistants", {})
    for method in ("get", "post", "delete"):
        monkeypatch.setattr(memory.httpx, method, getattr(fake, method))
    return fake


def test_each_rider_has_their_own_assistant_so_memories_never_mix(backboard):
    anna, ben = str(uuid.uuid4()), str(uuid.uuid4())
    memory.remember(anna, "Hates climbs")
    memory.remember(anna, "Rides a gravel bike")
    memory.remember(ben, "Loves lake views")

    assert memory.memories(anna) == ["Rides a gravel bike", "Hates climbs"]
    assert memory.memories(ben) == ["Loves lake views"]
    assert sorted(backboard.assistants.values()) == sorted([f"rider-{anna}", f"rider-{ben}"])


def test_looking_up_a_new_rider_creates_nothing(backboard):
    rider = str(uuid.uuid4())
    assert memory.memories(rider) == [] and memory.past_rides(rider) == []
    assert backboard.assistants == {}


def test_rider_must_be_a_uuid(backboard):
    with pytest.raises(ValueError):
        memory.memories("../assistants")
    assert client.post("/api/rider/memories", json={"rider": "not-a-uuid"}).status_code == 422
    assert (
        client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "rider": "x"}).status_code
        == 422
    )


def test_rides_are_stored_as_place_names_and_come_back_newest_first(backboard):
    rider = str(uuid.uuid4())
    told = [{"place": "Abbadia Lariana", "gist": "The rider races a ghost miller."}]
    client.post("/api/rider/ride", json={"rider": rider, "start": "45.85601,9.39437", "end": None, "told": told})
    client.post("/api/rider/ride", json={"rider": rider, "start": "Lecco", "end": "Varenna", "told": []})
    memory.remember(rider, "Hates climbs")

    assert memory.past_rides(rider) == [
        {"start": "Lecco", "end": "Varenna", "told": []},
        {"start": None, "end": None, "told": told},
    ]
    shown = client.post("/api/rider/memories", json={"rider": rider}).json()["memories"]
    assert shown[0] == "Hates climbs" and "Abbadia Lariana (The rider races a ghost miller.)" in shown[2]
    assert not any("45.8" in m for m in shown)
    assert memory.preferences(rider) == ["Hates climbs"]


def test_a_broken_ride_row_or_a_missing_key_just_means_less_history(backboard, monkeypatch):
    rider = str(uuid.uuid4())
    client.post("/api/rider/ride", json={"rider": rider, "start": "Lecco", "end": "Varenna", "told": []})
    stored = backboard.memories["a1"]
    stored.append({"content": "x", "metadata": {"kind": "ride", "told": '[{"place": "Mal'}, "created_at": "2026-10-10"})
    stored.append({"content": "y", "metadata": {"kind": "ride", "told": "[]"}, "created_at": "2026-10-11"})

    assert memory.past_rides(rider) == [
        {"start": None, "end": None, "told": []},
        {"start": "Lecco", "end": "Varenna", "told": []},
    ]
    monkeypatch.delenv("BACKBOARD_API_KEY")
    assert memory.past_rides(rider) == []


def test_forget_me_deletes_everything_for_that_rider_only(backboard):
    anna, ben = str(uuid.uuid4()), str(uuid.uuid4())
    memory.remember(anna, "Hates climbs")
    memory.remember(ben, "Loves lake views")

    assert client.post("/api/rider/forget", json={"rider": anna}).json() == {"ok": True}
    assert memory.memories(anna) == []
    assert memory.memories(ben) == ["Loves lake views"]
    assert list(backboard.assistants.values()) == [f"rider-{ben}"]


def test_story_hears_about_earlier_rides(monkeypatch):
    rides = [
        {"start": "Lecco", "end": "Varenna", "told": [{"place": "Fiumelatte", "gist": "A short river."}]},
        {"start": "Lecco", "end": None, "told": [{"place": "Malgrate", "gist": "A bridge duel."}]},
        {"start": "Lecco", "end": "Varenna", "told": []},
    ]
    monkeypatch.setattr(memory, "past_rides", lambda rider: rides)
    built = []
    monkeypatch.setattr(story, "build", lambda req, route, history, rides_before: built.append((history, rides_before)))
    body = {"request": {"start": "Lecco", "end": "Varenna"}, "route": {"coords": []}}

    client.post("/api/story", json={**body, "rider": str(uuid.uuid4())})
    client.post("/api/story", json=body)
    loop = {"request": {"start": "Lecco"}, "route": {"coords": []}}
    client.post("/api/story", json={**loop, "rider": str(uuid.uuid4())})

    told = [{"place": "Fiumelatte", "gist": "A short river."}, {"place": "Malgrate", "gist": "A bridge duel."}]
    assert built == [(told, 2), ([], 0), (told, 0)]
