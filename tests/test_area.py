import time

import httpx
from fastapi.testclient import TestClient

from app import agent, area, main, news, story, usage

# Its own visitor for the rate limiter: the other test files already use most of the shared 30 calls a minute.
client = TestClient(main.app, headers={"CF-Connecting-IP": "203.0.113.99"})

ARTICLES = [
    {"title": "Mandello del Lario railway station", "type": "railwaystation", "dist": 40.0},
    {"title": "Mandello del Lario", "type": None, "dist": 45.2},
    {"title": "Moto Guzzi", "type": None, "dist": 320.3},
    {"title": "Grigna", "type": "mountain", "dist": 1900.0},
]


class _Response:
    status_code = 200

    def __init__(self, data: dict):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _geosearch(articles: list[dict]):
    return lambda url, params=None: {"query": {"geosearch": articles}} if params else {}


def test_area_story_skips_told_places_and_stations(monkeypatch):
    monkeypatch.setattr(story, "_get", _geosearch(ARTICLES))
    monkeypatch.setattr(story, "_summary", lambda title: f"Facts about {title}.")
    sent = []
    monkeypatch.setattr(
        agent, "_complete", lambda instructions, content, temperature: sent.append(content) or {"text": " Vroom. "}
    )
    before = usage.COUNTS["live"]
    body = {
        "point": [45.917, 9.317],
        "bike": "gravel",
        "thread": "The quest for the lost bell.",
        "told": ["Mandello del Lario"],
        "context": {"speed_kmh": 21.5, "said": ["The ferry joke."]},
    }

    assert client.post("/api/area", json=body).json() == {"place": "Moto Guzzi", "text": "Vroom."}
    [prompt] = sent
    assert agent.LIVE_TASKS["area"] in prompt and "Bike: gravel" in prompt
    assert '"place": "Moto Guzzi"' in prompt and "Facts about Moto Guzzi." in prompt
    assert "The quest for the lost bell." in prompt and "The ferry joke." in prompt and '"speed_kmh": 21.5' in prompt
    assert usage.COUNTS["live"] == before + 1


def test_area_story_falls_back_to_the_town_when_wikipedia_has_nothing_fresh(monkeypatch):
    monkeypatch.setattr(story, "_get", _geosearch(ARTICLES[:2]))
    monkeypatch.setattr(news, "town_at", lambda lat, lon: ("Cavernago", "Italy"))
    summaries = {"Cavernago": "Cavernago is a comune in the Province of Bergamo."}
    monkeypatch.setattr(story, "_summary", lambda title: summaries.get(title, ""))

    told = {"Mandello del Lario"}
    assert area._subject(45.62, 9.75, told) == ("Cavernago", "Cavernago is a comune in the Province of Bergamo.")
    summaries.clear()
    assert area._subject(45.62, 9.75, told) == ("Cavernago", "Cavernago, Italy")
    assert area._subject(45.62, 9.75, told | {"Cavernago"}) is None


def test_the_point_is_rounded_before_any_outside_call(monkeypatch):
    calls = []

    def fake_get(url, params=None, **kwargs):
        calls.append(f"{url} {params}")
        if "geosearch" in str(params):
            return _Response({"query": {"geosearch": []}})
        if "photon" in url:
            return _Response({"features": [{"properties": {"city": "Mandello del Lario", "country": "Italy"}}]})
        return _Response({"extract": "A town on Lake Como."})

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(agent, "_complete", lambda instructions, content, temperature: {"text": "Hello lake."})
    news.town_at.cache_clear()

    answer = client.post("/api/area", json={"point": [45.91734, 9.31661]}).json()
    assert answer == {"place": "Mandello del Lario", "text": "Hello lake."}
    assert len(calls) == 3
    assert "'gscoord': '45.917|9.317'" in calls[0] and "'lat': 45.917, 'lon': 9.317" in calls[1]
    assert not any("45.9173" in c or "9.3166" in c for c in calls)


def test_area_story_is_empty_when_lookups_or_the_model_fail(monkeypatch):
    def offline(url, params=None, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "get", offline)
    news.town_at.cache_clear()
    empty = {"place": None, "text": ""}
    assert client.post("/api/area", json={"point": [45.62, 9.75]}).json() == empty

    monkeypatch.setattr(story, "_get", _geosearch(ARTICLES))
    monkeypatch.setattr(story, "_summary", lambda title: "Facts.")

    def garbled(instructions, content, temperature):
        raise ValueError("The model did not answer with JSON")

    monkeypatch.setattr(agent, "_complete", garbled)
    assert client.post("/api/area", json={"point": [45.917, 9.317]}).json() == empty

    def slow(instructions, content, temperature):
        time.sleep(0.3)
        return {"text": "Too late."}

    monkeypatch.setattr(agent, "_complete", slow)
    monkeypatch.setattr(area, "BUDGET_S", 0.05)
    assert client.post("/api/area", json={"point": [45.917, 9.317]}).json() == empty


def test_area_rejects_bad_points_and_oversized_lists():
    ok = {"point": [45.917, 9.317]}
    for bad in [
        {"point": [45.917]},
        {"point": [45.917, 9.317, 200]},
        {"point": [91, 9.317]},
        {"point": [45.917, -181]},
        {**ok, "bike": "unicycle"},
        {**ok, "told": ["Lecco"] * 41},
        {**ok, "thread": "x" * 1501},
        {**ok, "context": {"said": ["a", "b", "c", "d"]}},
    ]:
        assert client.post("/api/area", json=bad).status_code == 422
