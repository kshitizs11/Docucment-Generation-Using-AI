# Integration guide: calling this as a service from another backend

This service is a plain REST API — any other backend, in any language, can call it
over HTTP without importing any Python from this repo. This guide covers exactly that
case (server-to-server). For local/human use through the browser, see `RUNBOOK.md`
instead.

## 1. Run the service

**Locally** (see `RUNBOOK.md` for full setup):
```bash
python app.py --no-reload
```

**As a container**, from the repo root:
```bash
docker build -t ai-doc-platform .
docker run -p 8000:8000 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e SERVICE_API_KEY=your-shared-secret \
  ai-doc-platform
```

> **Not verified in this environment** — the Dockerfile was written and reviewed
> against the actual path-resolution code in `app/nodes/office_skill.py` and
> `app/nodes/validation.py` (both expect `mnt/skills/*` as a sibling of `backend/`,
> which is what the Dockerfile copies), but no Docker daemon was available here to
> actually build and run it. Build it yourself and confirm `GET /health` responds
> before relying on it.

## 2. Set an API key before exposing this on any shared network

By default, `SERVICE_API_KEY` is unset and **no auth is enforced** — fine for
localhost-only use, not fine the moment another service can reach this one over a
network, since anyone who can reach `/generate` triggers real generations on your
Anthropic quota.

Set `SERVICE_API_KEY` (in `backend/.env` locally, or as a container env var) and send
it as `X-API-Key` on every call. See `backend/app/auth.py` for exactly what this does
and doesn't protect — notably, **setting this breaks the bundled browser UI**, which
doesn't send the header. That's expected: this key is for backend-to-backend calls,
not for the human-facing UI.

## 3. The API

### `POST /generate` — start a generation, returns immediately

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-shared-secret" \
  -d '{"request": "a one-page project status report with 3 next steps"}'
# -> 202 {"job_id": "..."}
```

You do not choose the output format — it's inferred from `request` text by the
Planner node. If you need to hardcode format per-caller, phrase the request text to
make it unambiguous (e.g. "...as a PowerPoint deck").

### `GET /jobs/{job_id}` — poll for status

```bash
curl http://localhost:8000/jobs/<job_id> -H "X-API-Key: your-shared-secret"
```

```json
{
  "status": "running",
  "current_step": "office_skill",
  "request": "...",
  "format": null,
  "title": null,
  "validation": null,
  "size_bytes": null,
  "created_at": 1735900000.0,
  "finished_at": null,
  "download_ready": false,
  "error": null
}
```

`status` is one of `pending | running | completed | failed`. Poll every few seconds —
**a real generation takes roughly 10 minutes** (see `ARCHITECTURE.md` for why); don't
assume anything is wrong just because it's slow. Once `status == "completed"`,
`format`/`title`/`validation`/`size_bytes` are populated and `download_ready` is `true`.
If `status == "failed"`, `error` has the reason.

### `GET /jobs/{job_id}/download` — fetch the file once completed

```bash
curl http://localhost:8000/jobs/<job_id>/download \
  -H "X-API-Key: your-shared-secret" \
  -o output.docx
```

Returns 404 if the job isn't in `completed` status yet — check `/jobs/{job_id}` first.

## 4. Minimal Python example (the calling backend's side)

```python
import time
import requests

BASE_URL = "http://localhost:8000"
HEADERS = {"X-API-Key": "your-shared-secret"}

def generate_document(request_text: str, poll_interval: int = 5, timeout: int = 900) -> bytes:
    resp = requests.post(f"{BASE_URL}/generate", json={"request": request_text}, headers=HEADERS)
    resp.raise_for_status()
    job_id = resp.json()["job_id"]

    deadline = time.time() + timeout
    while time.time() < deadline:
        status = requests.get(f"{BASE_URL}/jobs/{job_id}", headers=HEADERS).json()
        if status["status"] == "completed":
            return requests.get(f"{BASE_URL}/jobs/{job_id}/download", headers=HEADERS).content
        if status["status"] == "failed":
            raise RuntimeError(f"Generation failed: {status['error']}")
        time.sleep(poll_interval)
    raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")
```

This is illustrative, not a shipped SDK — no retry/backoff, no connection pooling.
Adapt it to whatever HTTP client and error-handling conventions your backend already
uses; there's nothing this-repo-specific about it beyond the three endpoints above.

## 5. What this integration does NOT give you

- **No multi-tenancy.** One shared `SERVICE_API_KEY` for all callers — no per-caller
  quotas, no isolation between requests from different callers.
- **No webhook/callback.** The calling backend must poll; this service never calls back.
- **No persistence across restarts.** If this service restarts mid-generation, that
  job is gone (`app/jobs.py` is in-memory) — the calling backend should treat a
  vanished `job_id` as a failure and retry, not assume it'll reappear.
- **No horizontal scaling.** Single process, in-memory state — running multiple
  replicas behind a load balancer would split job state across instances incorrectly.
  Fine for one instance serving one integration; not fine for scaled production
  traffic without replacing `app/jobs.py` with a real shared store first.

See `backend/README.md`'s "What's real vs. explicitly out of scope" for the full,
honest list of what else isn't built (research retrieval, cloud storage, etc.) —
those apply regardless of how this service is consumed.
