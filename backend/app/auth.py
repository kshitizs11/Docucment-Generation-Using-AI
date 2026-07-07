"""
Opt-in shared-secret auth for the API surface, meant for another backend service
calling this one over HTTP -- not a full user-auth system.

SCOPE NOTE: off by default (SERVICE_API_KEY unset/empty) so local development and the
bundled UI keep working exactly as before with zero configuration. Set SERVICE_API_KEY
in backend/.env to require a matching `X-API-Key` header on the API endpoints -- do
this before exposing this service on any network another party can reach, since with
it unset, anyone who can reach /generate can trigger real generations on your
Anthropic quota.

TRADE-OFF: turning this on also breaks the bundled browser UI (static/index.html's
JS doesn't send the header) -- it stops being able to call /generate or poll /jobs.
That's an acceptable trade for the backend-to-backend integration this is meant for;
if you need the UI and auth at the same time, that's unbuilt follow-up work (the UI
prompting for/storing a key, or real session auth), not something this shared-secret
check does. The page routes themselves (/, /architecture) stay reachable either way --
only the JSON/file API is gated.
"""
import os

from fastapi import Header, HTTPException


def require_api_key(x_api_key: str = Header(default="")) -> None:
    expected = os.environ.get("SERVICE_API_KEY", "")
    if not expected:
        return
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header")
