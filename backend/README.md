# Backend — AI document platform (FastAPI + LangGraph + UI)

Full 7-node pipeline, verified end to end through a real browser against a real
Anthropic API key — prompt in, generated file out, no manual steps in between.

```
request
   │
   ▼
Planner ──▶ Research ──▶ Content ──▶ Layout ──▶ OfficeSkill ──▶ Validation ──▶ Export
(format +    (flags gaps,  (structured  (tone/     (generates the   (deterministic  (returns
 outline)     no real       content     color       actual file —    OOXML/xlsx/pdf   file
              retrieval     plan)       hints)      the verified     check, no       reference)
              wired up)                             Messages API     LLM call)
                                                     call)
```

`app/graph.py` wires these as a linear LangGraph `StateGraph` (`app/state.py` for the
shared state shape). `app/main.py` exposes it as an async job API (`POST /generate` →
`GET /jobs/{id}` → `GET /jobs/{id}/download`), and serves a premium single-page UI at
`/` (`static/index.html`, Tailwind CDN + Lucide icons, no build step) that drives that
API and polls for completion.

**Scope note on the UI**: it's deliberately one screen — hero, chat-style composer,
animated live pipeline, result card, dark mode, and a "Recent generations" panel
backed by the real `/jobs` data. It does *not* have Projects/Templates/History/
Knowledge-base pages, a command palette, drag-and-drop, or charts/tables — none of
those have backend support yet, and building UI shells for them would mean either
fake data or dead buttons. Every number shown (file size, generation time, slide/
validation details, queue count) is real, sourced from the job API — nothing is
fabricated for visual effect. Verified with a real browser (Playwright) against both
a fast fake-graph fixture (for rapid iteration on animations/interactions) and the
real pipeline. One real bug worth knowing about if you touch the CSS: a custom
Tailwind theme color (`bg-canvas`) combined with a `dark:` variant of a *different*,
built-in color intermittently lost the cascade to Tailwind's own generated rules —
fixed by handling `<body>`'s base colors with plain CSS instead of fighting Tailwind's
JIT ordering for that one case (see the comment in `static/index.html`).

See `../RUNBOOK.md` at the repo root for the full step-by-step guide to running this
end to end via the UI. This README covers the backend's internals; the runbook covers
"how do I actually use this."

## Setup

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
# edit .env, paste in a real ANTHROPIC_API_KEY (direct Anthropic key, not a gateway/proxy)
```

## Run

From the repo root: `python app.py` (see `../README.md` / `../RUNBOOK.md`). Or directly
from here:

```bash
uvicorn app.main:app --reload
```

Then open `http://127.0.0.1:8000/` in a browser, type a request, click Generate.

Or drive it via API directly:

```bash
curl -X POST http://127.0.0.1:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"request": "a one-page project status report with a summary and 3 next steps"}'
# -> {"job_id": "..."}

curl http://127.0.0.1:8000/jobs/<job_id>          # poll status
curl http://127.0.0.1:8000/jobs/<job_id>/download -o out.docx   # once status is "completed"
```

Or invoke the graph directly without the HTTP layer at all:

```python
from app.graph import doc_gen_graph
result = doc_gen_graph.invoke({"request": "..."})
```

**Budget real time**: the OfficeSkill node alone takes ~10 minutes (it reads the
hosted skill, writes and runs a generation script, renders to PDF, visually verifies,
exports). `/generate` returns immediately with a `job_id` rather than blocking on
this — see "async job handling" below.

**Budget real cost too**: a single OfficeSkill call was observed at 371K input tokens.
This isn't one LLM request — it's a multi-step agentic tool-use session (read skill →
write script → run → convert to PDF → render to image → visually inspect → export),
and each step resends the growing conversation as input context, which is inherent to
how multi-turn tool use works. `app/nodes/office_skill.py`'s system prompt (the
~11KB `SKILL.md`+`ENGINEERING_NOTES.md` text, identical on every call) uses `cache_control:
{"type": "ephemeral"}` so repeated calls within a 5-minute window read it from cache
instead of paying full price again — verified live (first call: 2979 tokens cached;
second call: 2979 tokens read from cache, 0 new). This doesn't touch the larger,
per-call-unique tool-use transcript cost, only the static system-prompt portion.

## Why each node calls Claude the way it does

- **Planner/Research/Content/Layout**: plain `client.messages.create()` with forced
  tool-use for structured output. No code execution, no skills container — these are
  fast, cheap reasoning steps, not document generation.
- **OfficeSkill**: `client.beta.messages.create()` with the `code-execution` +
  `skills` betas, referencing the Anthropic-hosted skill for `state["format"]`. This
  is the only node that touches document generation, and it does so by having Claude
  itself follow the hosted skill's documented workflow — never by this codebase
  reimplementing OOXML/PDF logic. See
  `mnt/skills/private/ai-document-platform/ENGINEERING_NOTES.md` for the full verified-behavior
  writeup (why custom skill upload isn't used, the exact file-extraction shape, etc).
- **Validation**: deterministic, no LLM call. Reuses this repo's own
  `mnt/skills/public/<format>/ooxml/scripts/unpack.py` rather than writing a second
  OOXML parser. `_check_docx` and `_check_pptx` (`app/nodes/validation.py`) deliberately
  check different things, learned from live failures, not designed upfront:
  - **docx**: word-level (not exact-substring) title matching against the whole
    document including headers/footers. Live testing found titles legitimately
    rendered in all-caps, placed in running headers instead of the body, and split
    across multiple text runs — none are defects, but exact-substring matching treated
    all three as failures.
  - **pptx**: does **not** check title wording at all. `html2pptx.md` (the skill's own
    design guidance) explicitly tells the model to invent creative branding — a
    content-plan title of "SaaS Product Pitch Deck" legitimately became "FlowSync —
    Where Teams Move as One" on the actual slide, zero words in common. Checking title
    fidelity there would mean fighting the skill's documented creative process, which
    is exactly what this architecture exists to avoid. Instead it checks that the file
    has at least one slide and every slide has non-empty text content — catches a
    genuinely broken/empty deck without policing wording or slide *count*, either.
    (An earlier version also required `slides >= headings + 1`, assuming a roughly 1:1
    section-to-slide mapping. Also wrong: a 4-heading plan for an explicitly-requested
    "4-slide deck" correctly produced a complete, well-formed 4-slide deck by
    consolidating sections per `html2pptx.md`'s own "keep slides brief" guidance — and
    got flagged as broken for having "too few" slides. Slide count isn't ours to
    dictate any more than title wording is.)

## Async job handling

`app/jobs.py` is an in-memory, single-process job store. `POST /generate` creates a
job and runs the pipeline via `doc_gen_graph.stream(..., stream_mode="updates")` in a
FastAPI `BackgroundTask`, updating `current_step` after each node completes so the UI
can show live progress. `GET /jobs/{id}` polls status; `GET /jobs/{id}/download`
streams the finished file; `GET /jobs` lists all jobs (debug visibility only).

This does not survive a restart and does not work across multiple uvicorn workers —
that's the right amount of infrastructure for proving the async flow works at all.
Swap it for a real queue (Redis/Celery/etc.) when there's an actual multi-worker
deployment to target.

## What's real vs. explicitly out of scope

Every node does genuine work — nothing here is faked. Two integrations are
deliberately not built, with the seam left clearly marked rather than stubbed
silently:

- **Research node has no external retrieval.** No web search, no internal RAG, no
  database lookups. It identifies what *would* need looking up and proposes
  clearly-labeled placeholder assumptions instead of fabricating facts. Wire a real
  retrieval call into `app/nodes/research.py` when there's an actual data source to
  target — the Content node already expects this exact shape.
- **Export node has no cloud storage.** Returns a local filesystem path, not a signed
  URL. Wire S3/blob storage + URL generation into `app/nodes/export.py` when there's a
  real bucket to target.

Also not built: auth, a database, multi-user support, template management, usage
analytics, and the fuller UI from the original architecture (projects/history/team
workspace/branding). The current UI is deliberately minimal — one prompt box — to
prove the pipeline is drivable end to end without assuming a design that hasn't been
built yet.
