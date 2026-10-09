from datetime import datetime, timezone

import httpx
import pytest

from app import agent, brief, destination, news, openmeteo, scenery


def _route_north(km: float) -> list:
    """Straight route going north from (45.0, 9.0), one point every ~100 m."""
    return [[45.0 + i * 0.0009, 9.0, 200] for i in range(int(km * 10) + 1)]


class _Response:
    def __init__(self, status_code=200, data=None, text=""):
        self.status_code = status_code
        self._data = data
        self.text = text

    def raise_for_status(self):
        if self.status_code != 200:
            raise httpx.HTTPStatusError("bad", request=None, response=None)

    def json(self):
        if self._data is None:
            raise ValueError("not JSON")
        return self._data


@pytest.fixture
def fresh_news(monkeypatch):
    monkeypatch.setattr(news, "GAP_S", 0)
    news._cache.clear()
    news.town_at.cache_clear()
    yield
    news._cache.clear()
    news.town_at.cache_clear()


def test_news_is_cached_per_town_and_keeps_it_local(fresh_news, monkeypatch):
    calls = []

    def fake_get(url, params):
        calls.append(params)
        titles = ["Sagra delle castagne  a Lecco", "Sagra delle castagne a Lecco", "Mucca in fuga sul lungolago"]
        return _Response(data={"articles": [{"title": t} for t in titles] + [{"url": "no title"}]})

    monkeypatch.setattr(news._client, "get", fake_get)
    assert news.headlines("Lecco", "Italy", wait_s=5) == ["Sagra delle castagne a Lecco", "Mucca in fuga sul lungolago"]
    assert news.headlines("lecco", "Italy", wait_s=5) == ["Sagra delle castagne a Lecco", "Mucca in fuga sul lungolago"]
    assert len(calls) == 1
    assert calls[0]["query"] == '"Lecco" sourcecountry:italy'
    news.headlines("Bath", "United Kingdom", wait_s=5)
    assert calls[1]["query"] == '"Bath" sourcecountry:unitedkingdom'


def test_news_failures_give_no_headlines_and_are_retried_sooner(fresh_news, monkeypatch):
    answers = [
        _Response(429, text="Please limit requests to one every 5 seconds"),
        httpx.ConnectError("down"),
        _Response(data={"articles": [{"title": "Palio in piazza"}]}),
    ]

    def fake_get(url, params):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(news._client, "get", fake_get)
    monkeypatch.setattr(news, "RETRY_S", 0)
    assert news.headlines("Siena", "Italy", wait_s=5) == []
    assert news.headlines("Siena", "Italy", wait_s=5) == []
    assert news.headlines("Siena", "Italy", wait_s=5) == ["Palio in piazza"]


def test_news_near_names_the_town_and_gives_up_quietly(fresh_news, monkeypatch):
    place = {"features": [{"properties": {"name": "Via Roma", "city": "Lecco", "country": "Italy"}}]}
    monkeypatch.setattr(news.httpx, "get", lambda *args, **kwargs: _Response(data=place))
    monkeypatch.setattr(news, "headlines", lambda town, country, wait_s: [f"News from {town}"])
    assert news.near(45.85, 9.39, 1) == {"town": "Lecco", "country": "Italy", "headlines": ["News from Lecco"]}
    news.town_at.cache_clear()
    monkeypatch.setattr(news.httpx, "get", lambda *args, **kwargs: _Response(503))
    assert news.near(45.85, 9.39, 1) == {}


def test_water_and_repair_stations_next_to_the_route_in_order():
    elements = [
        {"type": "node", "lat": 45.0450, "lon": 9.0005, "tags": {"amenity": "bicycle_repair_station"}},
        {"type": "node", "lat": 45.0180, "lon": 9.0003, "tags": {"amenity": "drinking_water", "name": "Fontanella"}},
        {"type": "node", "lat": 45.0270, "lon": 9.0030, "tags": {"amenity": "drinking_water"}},  # ~235 m off
        {"type": "node", "lat": 45.0360, "lon": 9.0001, "tags": {"amenity": "drinking_water", "access": "private"}},
        {"type": "way", "center": {"lat": 45.0630, "lon": 9.0002}, "tags": {"amenity": "drinking_water"}},
        {"type": "node", "lat": 45.0700, "lon": 9.0001, "tags": {"amenity": "bench"}},
    ]
    found = destination.stops(_route_north(10), elements)
    assert [(s["kind"], s["name"], round(s["km"])) for s in found] == [
        ("water", "Fontanella", 2),
        ("repair", None, 5),
        ("water", None, 7),
    ]
    assert found[0]["index"] == 20


def test_along_reads_the_venue_tags_from_the_same_overpass_answer(monkeypatch):
    queries = []
    venue = {"type": "node", "id": 7, "name": "Orobia climbing", "kind": "sports centre"}
    tags = {"name": "Orobia climbing", "opening_hours": "Mo-Fr 10:00-23:00", "contact:phone": "+39 035", "fee": "yes"}
    elements = [{"type": "node", "id": 7, "lat": 45.09, "lon": 9.0, "tags": tags}]
    monkeypatch.setattr(destination, "_venue", lambda end, coords: venue)
    monkeypatch.setattr(scenery, "_overpass", lambda query: queries.append(query) or elements)
    found = destination.along("Orobia climbing", _route_north(10))
    assert "node(7);" in queries[0]
    assert found == {
        "venue": {
            "name": "Orobia climbing",
            "kind": "sports centre",
            "opening_hours": "Mo-Fr 10:00-23:00",
            "phone": "+39 035",
        },
        "stops": [],
    }
    monkeypatch.setattr(scenery, "_overpass", lambda query: None)
    assert destination.along("Orobia climbing", _route_north(10)) == {"venue": None, "stops": None}


def test_website_line_comes_from_title_and_description_and_skips_private_hosts():
    page = '<html><title> Orobia &amp; friends </title><meta content="Climbing gym, open late" name="description">'
    assert destination._meta(page) == "Orobia & friends | Climbing gym, open late"
    assert destination.site("http://127.0.0.1/admin") == ""
    assert destination.site("file:///etc/passwd") == ""


class _Page:
    def __init__(self, redirect=None, body=b""):
        self.next_request = httpx.Request("GET", redirect) if redirect else None
        self.status_code = 200
        self.headers = {"content-type": "text/html"}
        self.encoding = "utf-8"
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_bytes(self):
        yield self._body


def test_website_redirects_are_checked_at_every_hop(monkeypatch):
    fetched = []
    pages = {
        "http://orobia.it/": _Page("https://www.orobia.it/"),
        "https://www.orobia.it/": _Page(body=b"<title>Orobia</title>"),
        "http://sneaky.it/": _Page("http://10.0.0.1/admin"),
    }
    monkeypatch.setattr(destination, "_public", lambda host: not host.startswith("10."))
    monkeypatch.setattr(destination.httpx, "stream", lambda method, url, **kwargs: fetched.append(url) or pages[url])

    assert destination.site("http://orobia.it/") == "Orobia"
    assert destination.site("http://sneaky.it/") == ""
    assert fetched == ["http://orobia.it/", "https://www.orobia.it/", "http://sneaky.it/"]


def test_weather_at_arrival_is_in_local_time_with_sunset(monkeypatch):
    data = {
        "utc_offset_seconds": 7200,
        "hourly": {
            "time": ["2026-10-09T18:00", "2026-10-09T19:00"],
            "temperature_2m": [17.4, 15.2],
            "precipitation_probability": [10, 40],
            "precipitation": [0.0, 0.3],
            "weather_code": [2, 61],
            "wind_speed_10m": [8.6, 12.1],
        },
        "daily": {"time": ["2026-10-09"], "sunset": ["2026-10-09T18:47"]},
    }
    monkeypatch.setattr(openmeteo.httpx, "get", lambda *args, **kwargs: _Response(data=data))
    arrive = datetime(2026, 10, 9, 17, 5, tzinfo=timezone.utc)
    assert openmeteo.at(45.9, 9.3, arrive) == {
        "day": "Friday",
        "time": "19:05",
        "temp_c": 15,
        "rain_chance_pct": 40,
        "rain_mm": 0.3,
        "wind_kmh": 12,
        "sky": "rain",
        "sunset": "18:47",
        "after_sunset": True,
    }


def test_prompt_lets_gemma_pick_fun_news_but_never_grim_or_political_or_named_people():
    assert agent.VOICE in brief.BRIEF_PROMPT
    prompt = " ".join(brief.BRIEF_PROMPT.split())
    for rule in [
        "pick at most 2 headlines",
        "odd or funny",
        "Skip politics",
        "Skip tragedies, deaths, serious violence, accidents with victims and disasters",
        "Never name private people",
        "never force a boring one",
        "English whatever the language",
        "Never invent facts",
        "40-70 words",
        "never a list read out",
    ]:
        assert rule in prompt, rule


def _found(monkeypatch, headlines=None, stops=None, weather=None):
    monkeypatch.setattr(
        news, "near", lambda lat, lon, wait_s: {"town": "Lecco", "country": "Italy", "headlines": headlines or []}
    )
    monkeypatch.setattr(destination, "along", lambda end, coords: {"venue": None, "stops": stops})
    monkeypatch.setattr(destination, "weather", lambda lat, lon, arrive: weather or {})


def test_brief_sends_town_names_not_coordinates_and_cleans_the_reply(monkeypatch):
    sent = []
    reply = {
        "items": [
            {"kind": "news", "text": " A cow escaped in Lecco. "},
            {"kind": "gossip", "text": "Not a kind we show."},
            {"kind": "water", "text": ""},
            "not an item",
        ],
        "spoken": " Before we roll: word from the streets of Lecco... ",
    }
    monkeypatch.setattr(agent, "_complete", lambda instructions, content, temperature: sent.append(content) or reply)
    _found(monkeypatch, headlines=["Mucca in fuga"], stops=[{"kind": "water", "name": None, "index": 3, "km": 0.3}])
    route = {"coords": _route_north(3), "distance_km": 3.0, "duration_min": 10}
    assert brief.build("45.85000,9.39000", "Varenna", route) == {
        "items": [{"kind": "news", "text": "A cow escaped in Lecco."}],
        "spoken": "Before we roll: word from the streets of Lecco...",
    }
    assert "45.85" not in sent[0] and '"from": "Lecco"' in sent[0]
    assert "- Mucca in fuga" in sent[0] and "Drinking water fountains along the route: 1, e.g. at km [0.3]" in sent[0]
    assert "Bike repair stations along the route: 0" in sent[0]
    assert "No headlines today" not in sent[0] and "unknown today" not in sent[0]


def test_brief_says_unknown_instead_of_none_when_a_lookup_is_missing(monkeypatch):
    sent = []
    monkeypatch.setattr(agent, "_complete", lambda instructions, content, temperature: sent.append(content) or {})
    _found(monkeypatch, weather={"time": "17:40", "temp_c": 21})
    brief.build("Lecco", "Varenna", {"coords": _route_north(3), "distance_km": 3.0, "duration_min": 10})
    assert "along the route" not in sent[0]
    assert "Water and repair stations are unknown today: don't mention them." in sent[0]
    assert "No headlines today: say nothing about local news" in sent[0]


def test_brief_never_raises(monkeypatch):
    route = {"coords": _route_north(3), "distance_km": 3.0, "duration_min": 10}

    def broken(*args, **kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(news, "near", broken)
    monkeypatch.setattr(destination, "along", broken)
    monkeypatch.setattr(destination, "weather", broken)
    monkeypatch.setattr(agent, "_complete", broken)
    assert brief.build("Lecco", "Varenna", route) == brief.EMPTY
    # Nothing found and nothing known: no Gemma call at all.
    _found(monkeypatch)
    assert brief.build("Lecco", None, route) == brief.EMPTY
    # Facts found, but Gemma fails or answers garbage.
    _found(monkeypatch, stops=[])
    assert brief.build("Lecco", None, route) == brief.EMPTY
    monkeypatch.setattr(agent, "_complete", lambda instructions, content, temperature: {"items": "x", "spoken": 3})
    assert brief.build("Lecco", None, route) == brief.EMPTY
