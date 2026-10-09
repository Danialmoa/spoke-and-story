"""ElevenLabs gives the Gemma-written guide its voice. On any error here the page falls back to Kokoro in the browser."""

import logging
import os
import threading
from collections import OrderedDict
from datetime import date
from time import monotonic

import httpx

from . import tracing

log = logging.getLogger("spokestory.tts")

URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}"
# George, a warm British storyteller. ElevenLabs' own quickstart uses him, so new accounts have him too.
VOICE = "JBFqnCBsd6RMkjVDRZzb"
MODEL = "eleven_flash_v2_5"
FORMAT = "mp3_44100_64"
MAX_CHARS = 800
DAILY_CHARS = 10_000
CLIENT_CHARS_PER_HOUR = 6_000
HOUR_S = 3600
CACHE_SIZE = 200
MAX_CLIENTS = 10_000

# Kept open: a new TLS connection per line added ~0.5-1 s.
_client = httpx.Client(timeout=15)
# The account allows 6 requests at once; a few riders starting together (3 lines each) would go over.
_slots = threading.Semaphore(4)
_lock = threading.Lock()
cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
today = {"date": None, "chars": 0}
clients: dict[str, tuple[float, int]] = {}


class Unavailable(Exception):
    """The page takes any of these as "use Kokoro"."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status


def _key() -> str:
    return os.environ.get("ELEVENLABS_API_KEY", "")


def _voice() -> str:
    return os.environ.get("ELEVENLABS_VOICE_ID") or VOICE


def _daily_cap() -> int:
    return int(os.environ.get("ELEVENLABS_DAILY_CHARS") or DAILY_CHARS)


def _used_today() -> int:
    if today["date"] != date.today():
        today.update(date=date.today(), chars=0)
    return today["chars"]


def _window(client: str, now: float) -> tuple[float, int]:
    start, chars = clients.get(client, (now, 0))
    return (now, 0) if now - start >= HOUR_S else (start, chars)


def available() -> bool:
    return bool(_key()) and _used_today() < _daily_cap()


def speak(text: str, client: str) -> bytes:
    """mp3 bytes for text. Repeats come from the cache and cost nothing, so they don't count against the caps."""
    if not _key():
        raise Unavailable(503, "No ElevenLabs key on this server.")
    voice = _voice()
    with _lock:
        if (voice, text) in cache:
            cache.move_to_end((voice, text))
            return cache[(voice, text)]
        if _used_today() + len(text) > _daily_cap():
            raise Unavailable(429, "The fast voice has used up today's budget.")
        start, chars = _window(client, monotonic())
        if chars + len(text) > CLIENT_CHARS_PER_HOUR:
            raise Unavailable(429, "That's a lot of talking for one hour.")
        # Charged before the call: checked-then-charged-after let 40 parallel requests all pass the caps.
        today["chars"] += len(text)
        if len(clients) > MAX_CLIENTS:
            clients.clear()
        clients[client] = (start, chars + len(text))
    try:
        audio = _generate(voice, text)
    except Unavailable:
        with _lock:
            today["chars"] -= len(text)
            start, chars = clients.get(client, (monotonic(), len(text)))
            clients[client] = (start, max(0, chars - len(text)))
        raise
    with _lock:
        cache[(voice, text)] = audio
        if len(cache) > CACHE_SIZE:
            cache.popitem(last=False)
    return audio


def _generate(voice: str, text: str) -> bytes:
    with _slots, tracing.span("ElevenLabs tts"):
        try:
            r = _client.post(
                URL.format(voice=voice),
                params={"output_format": FORMAT},
                headers={"xi-api-key": _key()},
                json={"text": text, "model_id": MODEL},
            )
        except httpx.HTTPError as exc:
            log.warning("ElevenLabs didn't answer: %s", type(exc).__name__)
            raise Unavailable(504, "ElevenLabs didn't answer in time.") from None
    if r.status_code != 200:
        code = _error_code(r)
        log.warning("ElevenLabs answered %s %s", r.status_code, code)
        raise Unavailable(502, f"ElevenLabs answered {r.status_code} {code}".strip() + ".")
    return r.content


def _error_code(r: httpx.Response) -> str:
    """Only the code (e.g. voice_not_found, quota_exceeded): the rest of the body may quote the text."""
    try:
        detail = r.json()["detail"]
    except (ValueError, KeyError, TypeError):
        return ""
    return (detail.get("code") or detail.get("status") or "") if isinstance(detail, dict) else ""
