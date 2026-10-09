import json
import logging
import re
from uuid import UUID

import httpx

from .agent import BACKBOARD_URL, _headers

log = logging.getLogger("spokestory.memory")
PREFIX = "rider-"
MAX_MEMORIES = 100
COORDS = re.compile(r"^\s*-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?\s*$")
# rider -> Backboard assistant id. Only riders who have an assistant are cached.
_assistants: dict[str, str] = {}
# Rider IDs are made up by the page, so anyone can send new ones; this keeps the cache bounded.
MAX_CACHED = 10_000


def _rider(rider: str) -> str:
    return str(UUID(str(rider)))


def _url(assistant: str, tail: str = "") -> str:
    return f"{BACKBOARD_URL}/assistants/{assistant}{tail}"


def _find(rider: str) -> list[str]:
    params = {"name": PREFIX + rider, "limit": 10}
    r = httpx.get(f"{BACKBOARD_URL}/assistants", headers=_headers(), params=params, timeout=30)
    r.raise_for_status()
    return [a["assistant_id"] for a in r.json() if a["name"] == PREFIX + rider]


def _assistant(rider: str, create: bool) -> str | None:
    rider = _rider(rider)
    if rider not in _assistants:
        if len(_assistants) > MAX_CACHED:
            _assistants.clear()
        found = _find(rider)
        if found:
            _assistants[rider] = found[0]
        elif not create:
            return None
        else:
            body = {"name": PREFIX + rider, "system_prompt": "Spoke & Story's ride buddy for one anonymous rider."}
            r = httpx.post(f"{BACKBOARD_URL}/assistants", headers=_headers(), json=body, timeout=30)
            r.raise_for_status()
            _assistants[rider] = r.json()["assistant_id"]
    return _assistants[rider]


def rider_assistant(rider: str) -> str:
    """Backboard keeps memories per assistant, so each rider gets their own: riders never see each other's."""
    return _assistant(rider, create=True)


def _all(rider: str) -> list[dict]:
    """Newest first. A rider without an assistant has nothing stored, and asking must not create one."""
    assistant = _assistant(rider, create=False)
    if not assistant:
        return []
    params = {"page": 1, "page_size": MAX_MEMORIES}
    r = httpx.get(_url(assistant, "/memories"), headers=_headers(), params=params, timeout=30)
    r.raise_for_status()
    return sorted(r.json()["memories"], key=lambda m: m.get("created_at") or "", reverse=True)


def _add(rider: str, text: str, metadata: dict) -> None:
    # Added directly instead of with memory="Auto" on messages: Auto costs a $0.0039 search on every message plus
    # $0.0039 per write, while adding and listing memories is free.
    body = {"content": text, "metadata": metadata}
    r = httpx.post(_url(rider_assistant(rider), "/memories"), headers=_headers(), json=body, timeout=30)
    r.raise_for_status()


def remember(rider: str, text: str) -> None:
    _add(rider, text.strip()[:300], {"kind": "preference"})


def memories(rider: str) -> list[str]:
    return [m["content"] for m in _all(rider)]


def preferences(rider: str) -> list[str]:
    """What the rider told the buddy about themselves, newest first: no ride logs."""
    return [m["content"] for m in _all(rider) if (m.get("metadata") or {}).get("kind") == "preference"]


def _place(name: str | None) -> str | None:
    """Ride history keeps place names only, never a GPS position."""
    return None if not name or COORDS.match(name) else name.strip()[:200]


def remember_ride(rider: str, ride: dict) -> None:
    """ride: {"start", "end" (None for a loop), "told": [{"place", "gist"}]} as the page sends it."""
    start, end = _place(ride.get("start")), _place(ride.get("end"))
    told = [{"place": t["place"], "gist": t.get("gist") or ""} for t in ride.get("told") or []]
    heard = "; ".join(f"{t['place']} ({t['gist']})" if t["gist"] else t["place"] for t in told)
    text = f"Rode from {start or 'their location'} {f'to {end}' if end else 'on a loop'}."
    text += f" Heard stories about {heard}." if told else ""
    # Backboard metadata only takes flat values, so the told list goes in as a JSON string.
    meta = {"kind": "ride", "start": start or "", "end": end or "", "told": json.dumps(told, ensure_ascii=False)}
    _add(rider, text, meta)


def past_rides(rider: str) -> list[dict]:
    """Earlier rides, newest first: {"start", "end", "told": [{"place", "gist"}]}. The story is still told without
    them, so a Backboard failure just means no history."""
    try:
        stored = _all(rider)
    except (httpx.HTTPError, KeyError) as exc:  # KeyError: no BACKBOARD_API_KEY
        log.warning("Couldn't read ride history: %s", type(exc).__name__)
        return []
    rides = []
    for meta in (m.get("metadata") or {} for m in stored):
        if meta.get("kind") != "ride":
            continue
        try:
            told = json.loads(meta.get("told"))
        except (ValueError, TypeError):
            continue
        rides.append({"start": meta.get("start") or None, "end": meta.get("end") or None, "told": told})
    return rides


def forget(rider: str) -> None:
    """Deletes everything stored for the rider. Every assistant with their name, in case two requests raced to make one."""
    rider = _rider(rider)
    for assistant in _find(rider):
        httpx.delete(_url(assistant, "/memories"), headers=_headers(), timeout=30).raise_for_status()
        httpx.delete(_url(assistant), headers=_headers(), timeout=30).raise_for_status()
    _assistants.pop(rider, None)
