import time

import httpx
import pytest

from app import ors, scenery


def _http_error(status: int = 404) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.openrouteservice.org/v2/directions")
    return httpx.HTTPStatusError("no route", request=request, response=httpx.Response(status, request=request))


def test_loops_skip_failed_seeds_and_fail_only_when_all_fail(monkeypatch):
    def directions(bike, priority, body, options):
        if options["round_trip"]["seed"] % 2:
            raise _http_error()
        return [{"seed": options["round_trip"]["seed"]}]

    monkeypatch.setattr(ors, "_directions", directions)
    assert sorted(f["seed"] for f in ors.loops(45.0, 9.0, 40, "road", "scenic")) == [0, 2, 4]

    monkeypatch.setattr(ors, "_directions", lambda *args: (_ for _ in ()).throw(_http_error(429)))
    with pytest.raises(httpx.HTTPStatusError):
        ors.loops(45.0, 9.0, 40, "road", "scenic")


def test_alternative_routes_only_for_shorter_rides(monkeypatch):
    bodies = []
    monkeypatch.setattr(ors, "_directions", lambda bike, priority, body, **kw: bodies.append(body) or [])
    ors.a_to_b((45.85, 9.40), (45.70, 9.62), "road", "scenic")  # Lecco -> Curno, ~24 km
    ors.a_to_b((45.85, 9.40), (45.07, 7.69), "road", "scenic")  # Lecco -> Turin, ~160 km
    assert ["alternative_routes" in b for b in bodies] == [True, False]


def test_a_to_b_falls_back_to_one_route_when_alternatives_hang(monkeypatch):
    def directions(bike, priority, body, **kw):
        if "alternative_routes" in body:
            raise httpx.ReadTimeout("slow")
        return ["one route"]

    monkeypatch.setattr(ors, "_directions", directions)
    assert ors.a_to_b((45.70, 9.67), (45.85, 9.40), "gravel", "flat") == ["one route"]


def test_bike_lanes_on_a_road_bike_uses_the_profile_that_prefers_cycleways(monkeypatch):
    urls = []

    def post(url, **kwargs):
        urls.append(url)
        return httpx.Response(200, json={"features": []}, request=httpx.Request("POST", url))

    monkeypatch.setenv("ORS_API_KEY", "test")
    monkeypatch.setattr(ors.httpx, "post", post)
    for bike, priority in [("road", "scenic"), ("road", "bike_lanes"), ("mtb", "bike_lanes")]:
        ors._directions(bike, priority, {"coordinates": []})
    assert [url.split("/")[-2] for url in urls] == ["cycling-road", "cycling-regular", "cycling-mountain"]


def test_scenery_gives_up_after_its_budget(monkeypatch):
    monkeypatch.setattr(scenery, "BUDGET_S", 0.2)
    monkeypatch.setattr(scenery, "_overpass", lambda query: time.sleep(2))
    started = time.monotonic()
    assert scenery.scenery([[[45.0, 9.0, 200], [45.01, 9.0, 200]]]) is None
    assert time.monotonic() - started < 1


def test_overpass_html_error_page_counts_as_a_failed_server(monkeypatch):
    replies = iter(
        [
            httpx.Response(200, text="<html>Gateway Timeout</html>"),
            httpx.Response(200, json={"elements": [{"type": "node"}]}),
        ]
    )
    monkeypatch.setattr(scenery.httpx, "post", lambda *args, **kwargs: next(replies))
    assert scenery._overpass("query") == [{"type": "node"}]
