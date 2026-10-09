import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import agent, area, main, news, story, usage

# Its own visitor for the rate limiter: the other test files already use most of the shared 30 calls a minute.
client = TestClient(main.app, headers={"CF-Connecting-IP": "203.0.113.99"})

ARTICLES = [
    {"title": "Mandello del Lario railway station", "type": "railwaystation", "dist": 40.0},
    {"title": "Mandello del Lario", "type": None, "dist": 45.2},
    {"title": "Moto Guzzi", "type": None, "dist": 320.3},
    {"title": "Grigna", "type": "mountain", "dist": 1900.0},
    {"title": "Abbadia Lariana", "type": "city", "dist": 3500.0},
]
BERGAMO = {"town": "Bergamo", "region": "Lombardy", "country": "Italy", "country_code": "IT"}
CASONCELLI = (
    "Casoncelli are a type of stuffed pasta typical of Bergamo and Brescia, filled with breadcrumbs, cheese and "
    "sausage, folded like a sweet wrapper and served with melted butter, sage and pancetta. Each valley swears its "
    "own recipe is the old one."
)


def _page(title: str, extract: str, **more) -> dict:
    return {"title": title, "extract": extract, **more}


def _wiki(geosearch=(), searches=None):
    """A fake story._get: geosearch answers the given articles, full-text search answers by (host, query)."""
    asked = []

    def get(url, params=None):
        if params and params.get("list") == "geosearch":
            return {"query": {"geosearch": list(geosearch)}}
        if params and params.get("generator") == "search":
            asked.append((url.split("/")[2], params["gsrsearch"]))
            pages = (searches or {}).get(asked[-1], [])
            return {"query": {"pages": [{**p, "index": i + 1} for i, p in enumerate(pages)]}}
        return {}

    return get, asked


def _gemma(topics: list[dict], text: str = " A line. "):
    """A fake model: topic ideas for the topics prompt, a line for the live one. Keeps each prompt by task."""
    sent = {}

    def complete(instructions, content, temperature):
        sent.setdefault(agent.TASKS[instructions], []).append(content)
        return {"topics": topics} if instructions == agent.TOPICS_PROMPT else {"text": text}

    return complete, sent


@pytest.fixture
def place_first(monkeypatch):
    monkeypatch.setattr(area, "TOPIC_SHARE", 0)


@pytest.fixture
def topic_first(monkeypatch):
    monkeypatch.setattr(area, "TOPIC_SHARE", 1)
    monkeypatch.setattr(news, "town_at", lambda lat, lon: BERGAMO)


def test_the_nearest_untold_place_within_a_km_skipping_stations(place_first, monkeypatch):
    monkeypatch.setattr(story, "_get", _wiki(ARTICLES)[0])
    monkeypatch.setattr(news, "town_at", lambda lat, lon: BERGAMO)
    monkeypatch.setattr(story, "_summary", lambda title: f"Facts about {title}.")
    complete, sent = _gemma([], text=" Vroom. ")
    monkeypatch.setattr(agent, "_complete", complete)
    before = usage.COUNTS["live"]
    body = {
        "point": [45.917, 9.317],
        "bike": "gravel",
        "thread": "The quest for the lost bell.",
        "told": ["mandello del lario"],
        "context": {"speed_kmh": 21.5, "said": ["The ferry joke."]},
    }

    assert client.post("/api/area", json=body).json() == {"place": "Moto Guzzi", "topic": None, "text": "Vroom."}
    [prompt] = sent["live line"]
    assert agent.LIVE_TASKS["area"] in prompt and "Bike: gravel" in prompt
    assert '"place": "Moto Guzzi"' in prompt and "Facts about Moto Guzzi." in prompt and '"topic"' not in prompt
    assert "The quest for the lost bell." in prompt and "The ferry joke." in prompt and '"speed_kmh": 21.5' in prompt
    assert usage.COUNTS["live"] == before + 1


def test_a_topic_is_told_from_its_wikipedia_article_and_told_topics_are_skipped(topic_first, monkeypatch):
    ideas = [
        {"topic": "Bergamo funicular", "local": "Funicolare Bergamo", "place": "Bergamo"},
        {"topic": "Bergamo legend", "local": "Leggenda Bergamo", "place": "Bergamo"},
        {"topic": "Casoncelli pasta Bergamo", "local": "Casoncelli", "place": "Bergamo"},
    ]
    searches = {
        ("en.wikipedia.org", "Bergamo legend"): [
            _page("Bergamo–Albino light rail", "A light rail line from Bergamo to Albino. " * 10),
            _page("Legends of Bergamo", "Bergamo legend: see the list. " * 10, pageprops={"disambiguation": ""}),
            _page("Alexander of Bergamo", "The legend of the patron saint of Bergamo. " * 10),
            _page("Bergamo", "Bergamo is a city with a legend or two. " * 10),
        ],
        ("en.wikipedia.org", "Casoncelli pasta Bergamo"): [_page("Casoncelli", CASONCELLI)],
    }
    fake_get, asked = _wiki(ARTICLES, searches)
    monkeypatch.setattr(story, "_get", fake_get)
    complete, sent = _gemma(ideas)
    monkeypatch.setattr(agent, "_complete", complete)
    told = ["Bergamo Funicular", "Alexander of Bergamo"]

    answer = client.post("/api/area", json={"point": [45.703, 9.663], "thread": "The pasta quest.", "told": told})

    assert answer.json() == {"place": "Casoncelli", "topic": "Casoncelli pasta Bergamo", "text": "A line."}
    # The told topic is never searched; the rest are searched at once, so the first good one wins.
    assert sorted(asked) == [("en.wikipedia.org", "Bergamo legend"), ("en.wikipedia.org", "Casoncelli pasta Bergamo")]
    [ask] = sent["area topics"]
    assert '"town": "Bergamo", "region": "Lombardy", "country": "Italy"' in ask
    assert '"nearby_towns": ["Abbadia Lariana"]' in ask
    assert "- Bergamo Funicular\n- Alexander of Bergamo" in ask
    [line] = sent["live line"]
    assert '"place": "Casoncelli"' in line and '"topic": "Casoncelli pasta Bergamo"' in line
    assert CASONCELLI in line and "The pasta quest." in line


def test_the_country_s_own_wikipedia_is_asked_when_english_has_nothing(topic_first, monkeypatch):
    ideas = [{"topic": "Bergamo funicular history", "local": "Funicolare di Bergamo Alta", "place": "Bergamo"}]
    funicolare = "La funicolare di Bergamo Alta collega la città alta al centro, costruita nel 1887. " * 4
    searches = {("it.wikipedia.org", "Funicolare di Bergamo Alta"): [_page("Funicolare di Bergamo Alta", funicolare)]}
    fake_get, asked = _wiki([], searches)
    monkeypatch.setattr(story, "_get", fake_get)
    monkeypatch.setattr(agent, "_complete", _gemma(ideas)[0])

    answer = client.post("/api/area", json={"point": [45.703, 9.663]}).json()

    assert answer["place"] == "Funicolare di Bergamo Alta" and answer["topic"] == "Bergamo funicular history"
    assert asked == [
        ("en.wikipedia.org", "Bergamo funicular history"),
        ("it.wikipedia.org", "Funicolare di Bergamo Alta"),
    ]


def test_without_a_good_topic_or_place_the_town_is_told_once(topic_first, monkeypatch):
    monkeypatch.setattr(story, "_get", _wiki(ARTICLES[:2])[0])
    monkeypatch.setattr(agent, "_complete", _gemma([{"topic": "Bergamo ghosts", "place": "Bergamo"}])[0])
    summaries = {"Bergamo": "Bergamo is a city in Lombardy."}
    monkeypatch.setattr(story, "_summary", lambda title: summaries.get(title, ""))
    told = ["Mandello del Lario", "Mandello del Lario railway station"]

    assert area._subject(45.703, 9.663, told) == {"place": "Bergamo", "topic": None, "facts": summaries["Bergamo"]}
    summaries.clear()
    assert area._subject(45.703, 9.663, told)["facts"] == "Bergamo, Italy"
    assert area._subject(45.703, 9.663, [*told, "bergamo"]) is None


def test_the_point_is_rounded_before_any_outside_call(monkeypatch):
    calls = []

    class _Response:
        status_code = 200

        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    def fake_get(url, params=None, **kwargs):
        calls.append(f"{url} {params}")
        if "photon" in url:
            props = {"city": "Bergamo", "state": "Lombardy", "country": "Italy", "countrycode": "IT"}
            return _Response({"features": [{"properties": props}]})
        if params.get("list") == "geosearch":
            return _Response({"query": {"geosearch": []}})
        return _Response({"query": {"pages": [{"title": "Casoncelli", "extract": CASONCELLI, "index": 1}]}})

    monkeypatch.setattr(area, "TOPIC_SHARE", 1)
    monkeypatch.setattr(httpx, "get", fake_get)
    complete, sent = _gemma([{"topic": "Casoncelli pasta Bergamo", "local": "Casoncelli", "place": "Bergamo"}])
    monkeypatch.setattr(agent, "_complete", complete)
    news.town_at.cache_clear()

    answer = client.post("/api/area", json={"point": [45.70312, 9.66288]}).json()

    assert answer == {"place": "Casoncelli", "topic": "Casoncelli pasta Bergamo", "text": "A line."}
    assert len(calls) == 3
    assert any("'gscoord': '45.703|9.663'" in c for c in calls)
    assert any("'lat': 45.703, 'lon': 9.663" in c for c in calls)
    assert not any("45.7031" in c or "9.6628" in c for c in calls)
    # Wikipedia search and the model only ever see names.
    prompts = [p for task in sent.values() for p in task]
    assert not any("45.70" in c or "9.66" in c for c in calls[2:] + prompts)


def test_area_story_is_empty_when_lookups_or_the_model_fail(place_first, monkeypatch):
    def offline(url, params=None, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "get", offline)
    news.town_at.cache_clear()
    empty = {"place": None, "topic": None, "text": ""}
    assert client.post("/api/area", json={"point": [45.62, 9.75]}).json() == empty

    monkeypatch.setattr(story, "_get", _wiki(ARTICLES)[0])
    monkeypatch.setattr(news, "town_at", lambda lat, lon: BERGAMO)
    monkeypatch.setattr(story, "_summary", lambda title: "Facts.")

    def garbled(instructions, content, temperature):
        raise ValueError("The model did not answer with JSON")

    monkeypatch.setattr(agent, "_complete", garbled)
    assert client.post("/api/area", json={"point": [45.917, 9.317]}).json() == empty
    monkeypatch.setattr(area, "TOPIC_SHARE", 1)
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
        {**ok, "told": ["Lecco"] * 61},
        {**ok, "thread": "x" * 1501},
        {**ok, "context": {"said": ["a", "b", "c", "d"]}},
    ]:
        assert client.post("/api/area", json=bad).status_code == 422
