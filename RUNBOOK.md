# Runbook: AI Document Platform, end to end

This is the step-by-step guide to running the full pipeline through the UI, from a
plain-English prompt to a downloaded Office document. It assumes nothing has been set
up yet.

## What you're running

```
Browser (prompt box)
   │  POST /generate
   ▼
FastAPI backend (backend/app/main.py)
   │  runs as a background job
   ▼
LangGraph pipeline (7 nodes — see backend/README.md for the full breakdown)
   │  Planner → Research → Content → Layout → OfficeSkill → Validation → Export
   ▼
OfficeSkill node: real Claude call, code-execution container, hosted docx/pptx/xlsx/pdf skill
   │
   ▼
Generated file, downloaded from the browser
```

Only one node (OfficeSkill) talks to Claude with code execution; the rest are cheap,
fast planning calls or plain deterministic checks. See
`mnt/skills/private/ai-document-platform/ENGINEERING_NOTES.md` for the underlying verified
Messages API behavior, and `backend/README.md` for the pipeline's internals.

## Step 1 — Prerequisites

- Python 3.10+
- Node.js + npm (needed by the docx/pptx generation skills themselves, not the backend)
- A **direct Anthropic API key** (console.anthropic.com/settings/keys). A gateway/proxy
  key (TrueFoundry, Bedrock, Azure OpenAI, etc.) will not work — the code-execution and
  skills betas are Anthropic-native and aren't proxied by those gateways.

## Step 2 — Install the document-generation dependencies

These are required by the skills themselves (`mnt/skills/public/*`), not by the
backend directly — the OfficeSkill node's Claude call runs generation code inside
Anthropic's own sandboxed container, but if you ever run any of this repo's skill
scripts locally (e.g. for the Validation node's OOXML checks, which do run locally),
these need to be present:

```bash
# docx
npm install -g docx

# pptx (only needed if you'll generate pptx locally / test that skill directly)
npm install -g ./mnt/skills/public/pptx/html2pptx.tgz pptxgenjs playwright sharp
npx playwright install chromium
```

## Step 3 — Configure the backend

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
```

Edit `backend/.env` and paste in your real Anthropic key:

```
ANTHROPIC_API_KEY=sk-ant-...your real key...
ANTHROPIC_PLANNING_MODEL=claude-sonnet-4-6
ANTHROPIC_OFFICE_SKILL_MODEL=claude-sonnet-4-6
```

`backend/.env` is gitignored — this key never needs to leave your machine.

## Step 4 — Start the server

```bash
cd backend
uvicorn app.main:app --reload
```

Confirm it's up:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

## Step 5 — Use it through the UI

1. Open `http://127.0.0.1:8000/` in a browser.
2. Type a plain-English request into the prompt box, e.g.:
   > a one-page project status report with a summary and 3 next steps
3. Click **Generate**.
4. The page shows live progress through the 7 pipeline steps
   (`planner → research → content → layout → office_skill → validation → export`).
5. **Wait** — the `office_skill` step is the real generation call and takes roughly
   10 minutes. This is the single slowest, most expensive step in the whole pipeline;
   everything before it (planning) takes seconds.
6. When it finishes, a **Download** button appears. Click it to get the file.

You do not need to specify the file format — the Planner node infers docx/pptx/xlsx/pdf
from the request itself. If your request is genuinely ambiguous about format, the
Planner will pick the most likely one rather than blocking (see
`app/nodes/planner.py`).

## Step 6 — What to do if something goes wrong

| Symptom | Likely cause | Where to look |
|---|---|---|
| `/health` doesn't respond | Server didn't start | Check the `uvicorn` terminal output for import errors |
| Job fails immediately with an auth error | Bad/placeholder `ANTHROPIC_API_KEY` | `backend/.env` — must be a real, direct Anthropic key |
| Job stuck on `office_skill` for a very long time | Normal — this step alone took ~10 min in testing | Check `GET /jobs/{id}` for status; check server logs for the underlying API call |
| Job fails at `validation` | The generated file may genuinely be malformed, or the check itself may be too strict | Read `validation.details` in the job status; `app/nodes/validation.py` has notes on real false-positives already fixed for docx (all-caps titles, titles in headers/footers, titles split across text runs) and for pptx (title wording is deliberately NOT checked — the skill is meant to invent creative branding, e.g. a generic title becoming a made-up product name) — if you hit a new one, it's likely another overly strict check, not a broken document |
| Download button never appears | Job failed silently, or browser lost the poll loop | Check `GET /jobs/{id}` directly, or `GET /jobs` for all jobs (debug endpoint) |

## What this does and doesn't prove

**Proven, live, through the real UI**: a plain-English prompt becomes a real,
validated `.docx` (or pptx/xlsx/pdf, per format inference), downloadable through a
browser, with zero manual intervention between typing the prompt and clicking
Download.

**Not built** (see `backend/README.md`'s "What's real vs. explicitly out of scope"
for the full list): external research retrieval (the Research node flags gaps but
can't fill them), cloud storage (downloads are served from local disk), auth, a
database, multi-user support, and the fuller UI (projects, history, templates, team
workspace) from the original architecture. This runbook covers the verified core; those
are separate, later pieces of work.
