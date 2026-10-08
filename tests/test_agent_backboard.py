from app import agent


class _Response:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _no_google(*args):
    raise AssertionError("Google must not be called while Backboard works")


def test_backboard_is_used_when_configured(monkeypatch):
    monkeypatch.setenv("BACKBOARD_API_KEY", "test")
    monkeypatch.setattr(agent, "_assistant", lambda: "a1")
    sent = []
    reply = {"status": "COMPLETED", "content": '{"text": "hi"}', "input_tokens": 5, "output_tokens": 3}
    monkeypatch.setattr(agent.httpx, "post", lambda url, headers, json, timeout: sent.append(json) or _Response(reply))
    monkeypatch.setattr(agent, "_google", _no_google)

    assert agent._complete("instructions", "content") == {"text": "hi"}
    assert (sent[0]["assistant_id"], sent[0]["system_prompt"], sent[0]["memory"]) == ("a1", "instructions", "off")


def test_google_takes_over_when_backboard_fails(monkeypatch):
    monkeypatch.setenv("BACKBOARD_API_KEY", "test")
    monkeypatch.setattr(agent, "_assistant", lambda: "a1")
    # Backboard reports a failed model call with HTTP 200 and status FAILED.
    monkeypatch.setattr(agent.httpx, "post", lambda *a, **k: _Response({"status": "FAILED", "content": "LLM Error"}))
    monkeypatch.setattr(agent, "_google", lambda instructions, content, temperature: '{"text": "from google"}')

    assert agent._complete("instructions", "content") == {"text": "from google"}
