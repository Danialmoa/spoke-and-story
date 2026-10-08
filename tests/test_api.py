from fastapi.testclient import TestClient

from app import main, usage

client = TestClient(main.app)


def test_events_are_counted_and_unknown_ones_rejected():
    before = usage.COUNTS["ride_start"]
    assert client.post(
        "/api/event", json={"event": "ride_start", "area": "45.9,9.4", "mode": "sim", "km": 29.2}
    ).json() == {"ok": True}
    assert client.get("/api/stats").json()["counts"]["ride_start"] == before + 1
    assert client.post("/api/event", json={"event": "something_else"}).status_code == 422


def test_area_is_rounded_to_about_10_km():
    assert usage.area(45.856049, 9.394366) == "45.9,9.4"
