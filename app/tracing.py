import os

import sentry_sdk
from sentry_sdk.integrations.httpx import HttpxIntegration


def init() -> None:
    """Sentry tracing, only when SENTRY_DSN is set. Gemma calls are traced by sentry-sdk's google-genai
    integration (model, input/output/thinking tokens); prompts stay out because send_default_pii is off."""
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return
    sentry_sdk.init(
        dsn=dsn,
        traces_sample_rate=1.0,
        send_default_pii=False,
        # Stack frames would carry prompts, what riders told the buddy, and positions.
        include_local_variables=False,
        max_request_body_size="never",
        # Its spans and breadcrumbs keep query strings: the ORS key, typed addresses, GPS points.
        disabled_integrations=[HttpxIntegration()],
        before_send=_scrub,
        before_send_transaction=_scrub,
    )


def _scrub(event: dict, hint: dict) -> dict:
    """/api/places?q=...&near=lat,lon would otherwise put addresses and positions into events."""
    request = event.get("request") or {}
    request.pop("query_string", None)
    request.pop("data", None)
    return event


def span(name: str):
    """Timing span for an outside service. Carries only this name: no URL, coordinates or text.
    A no-op when Sentry isn't initialised."""
    return sentry_sdk.start_span(op="http.client", name=name)
