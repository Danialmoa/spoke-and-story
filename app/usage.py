import json
import logging
from collections import Counter
from datetime import UTC, datetime

log = logging.getLogger("spokestory.usage")
# In memory only: resets when the server restarts. The log lines are the lasting record (Render keeps them).
COUNTS: Counter = Counter()
SINCE = datetime.now(UTC).isoformat(timespec="seconds")


def area(lat: float, lon: float) -> str:
    """Rounded to ~10 km: enough to see where people ride, too coarse to point at anyone's home."""
    return f"{lat:.1f},{lon:.1f}"


def track(event: str, **fields) -> None:
    COUNTS[event] += 1
    log.info(json.dumps({"event": event, **fields}))


def stats() -> dict:
    return {"since": SINCE, "counts": dict(COUNTS)}
