import os

import pytest

# app.main loads .env; without this a real SENTRY_DSN there would send test traffic to Sentry.
os.environ["SENTRY_DSN"] = ""

# Tests must never call the real LLMs; Backboard is used whenever this key is set.
os.environ["BACKBOARD_API_KEY"] = ""


@pytest.fixture(autouse=True)
def fresh_rate_limit():
    from app import limits

    limits.calls.clear()
