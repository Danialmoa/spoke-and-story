from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import limits


def make_client() -> TestClient:
    app = FastAPI()
    limits.install(app)
    app.post("/api/plan")(lambda: {"ok": True})
    app.get("/api/places")(lambda: {"ok": True})
    return TestClient(app)


def test_expensive_endpoint_is_limited_per_client_and_says_so_kindly():
    client = make_client()
    rider = {"X-Forwarded-For": "203.0.113.7, 10.0.0.1"}
    assert all(client.post("/api/plan", headers=rider).status_code == 200 for _ in range(limits.MAX_CALLS))
    blocked = client.post("/api/plan", headers=rider)
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": limits.SLOW_DOWN}
    assert client.post("/api/plan", headers={"X-Forwarded-For": "198.51.100.2"}).status_code == 200


def test_cheap_endpoints_are_never_limited():
    client = make_client()
    assert all(client.get("/api/places").status_code == 200 for _ in range(limits.MAX_CALLS + 5))


def test_window_slides_so_a_rider_can_go_again_after_a_minute(monkeypatch):
    client = make_client()
    now = [1000.0]
    monkeypatch.setattr(limits, "monotonic", lambda: now[0])
    for _ in range(limits.MAX_CALLS):
        client.post("/api/plan")
    assert client.post("/api/plan").status_code == 429
    now[0] += limits.WINDOW_S + 1
    assert client.post("/api/plan").status_code == 200
