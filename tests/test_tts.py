import httpx
import pytest
from fastapi.testclient import TestClient

from app import limits, main, tts

client = TestClient(main.app)
MP3 = b"ID3-fake-mp3"


@pytest.fixture(autouse=True)
def fresh_tts():
    tts.cache.clear()
    tts.clients.clear()
    tts.today.update(date=None, chars=0)


@pytest.fixture
def upstream(monkeypatch):
    """A fake ElevenLabs: records each call, answers with MP3 or with the status set in .status."""
    calls = []

    def fake_post(url, params, headers, json):
        calls.append({"url": url, "params": params, "headers": headers, "json": json})
        if upstream.status != 200:
            body = {"detail": {"code": "quota_exceeded", "message": f"no credits for: {json['text']}"}}
            return httpx.Response(upstream.status, json=body)
        return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})

    upstream = type("Upstream", (), {"calls": calls, "status": 200})
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(tts._client, "post", fake_post)
    return upstream


def say(text, ip="203.0.113.7"):
    return client.post("/api/tts", json={"text": text}, headers={"CF-Connecting-IP": ip})


def test_line_comes_back_as_mp3_from_flash_with_the_default_voice(upstream):
    r = say("Ho ho ho, left at the bakery!")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.content == MP3
    [call] = upstream.calls
    assert call["url"].endswith(f"/v1/text-to-speech/{tts.VOICE}")
    assert call["json"] == {"text": "Ho ho ho, left at the bakery!", "model_id": "eleven_flash_v2_5"}
    assert call["headers"] == {"xi-api-key": "test-key"}


def test_same_line_twice_is_paid_once(upstream):
    assert say("The lake is showing off again.").content == MP3
    assert say("The lake is showing off again.", ip="198.51.100.2").content == MP3
    assert len(upstream.calls) == 1
    assert tts.today["chars"] == len("The lake is showing off again.")


def test_no_key_means_use_kokoro_and_the_page_is_told_up_front(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    assert client.get("/api/voice").json() == {"elevenlabs": False}
    r = say("Hello")
    assert r.status_code == 503
    assert "detail" in r.json()


def test_daily_budget_is_shared_by_everyone(upstream, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_DAILY_CHARS", "25")
    assert client.get("/api/voice").json() == {"elevenlabs": True}
    assert say("Twenty characters!!!").status_code == 200
    blocked = say("Another twenty chars", ip="198.51.100.2")
    assert blocked.status_code == 429
    assert len(upstream.calls) == 1
    assert say("Ten chars!", ip="198.51.100.3").status_code == 429
    tts.today["chars"] = 25
    assert client.get("/api/voice").json() == {"elevenlabs": False}


def test_one_visitor_gets_a_share_per_hour(upstream, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(tts, "monotonic", lambda: now[0])
    monkeypatch.setattr(tts, "CLIENT_CHARS_PER_HOUR", 30)
    assert say("x" * 20).status_code == 200
    assert say("y" * 20).status_code == 429
    assert say("y" * 20, ip="198.51.100.2").status_code == 200
    now[0] += tts.HOUR_S
    assert say("y" * 20).status_code == 200


def test_line_length_is_capped():
    assert say("x" * (tts.MAX_CHARS + 1)).status_code == 422
    assert say("").status_code == 422


def test_upstream_error_is_a_clean_answer_with_only_the_error_code(upstream):
    upstream.status = 401
    r = say("Secret story text")
    assert r.status_code == 502
    assert r.json() == {"detail": "ElevenLabs answered 401 quota_exceeded."}
    assert say("Secret story text").status_code == 502
    assert tts.today["chars"] == 0


def test_upstream_timeout_is_a_clean_answer(upstream, monkeypatch):
    def hang(*args, **kwargs):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(tts._client, "post", hang)
    assert say("Hello").status_code == 504


def test_a_ride_preparing_its_lines_is_not_stopped_by_the_general_limiter(upstream):
    assert "/api/tts" not in limits.LIMITED
    lines = [f"Line number {i}" for i in range(limits.MAX_CALLS + 10)]
    assert all(say(line).status_code == 200 for line in lines)


def test_characters_are_charged_before_the_call_so_parallel_requests_cant_slip_past_the_caps(upstream, monkeypatch):
    seen = []

    def fake_post(url, params, headers, json):
        seen.append((tts.today["chars"], tts.clients["203.0.113.7"][1]))
        return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})

    monkeypatch.setattr(tts._client, "post", fake_post)
    assert say("x" * 40).status_code == 200
    assert seen == [(40, 40)]
