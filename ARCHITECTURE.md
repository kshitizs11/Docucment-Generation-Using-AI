# Architecture

How a prompt typed into the browser becomes a downloaded `.docx`/`.pptx`/`.xlsx`/`.pdf`.
This is the complete, accurate picture of what actually runs — every claim here is
backed by the code in `backend/app/` (file references throughout).

## System diagram

```
┌─────────────┐   POST /generate    ┌───────────────────────────────┐
│   Browser   │ ──────────────────▶ │   FastAPI app (app/main.py)   │
│ (static/    │                     │                               │
│  index.html)│ ◀────────────────── │  returns {job_id} immediately │
└──────┬──────┘   202 {job_id}      └───────────────┬───────────────┘
       │                                            │
       │  GET /jobs/{id}  (poll every 2.5s)         │ BackgroundTasks.add_task(_run_job)
       │  GET /jobs/{id}/download (once completed)  │  (fire-and-forget, doesn't block
       │  GET /jobs        (recent list, every 8s)  │   the HTTP response)
       ▼                                            ▼
┌─────────────────────────┐          ┌───────────────────────────────┐
│ In-memory job store      │ ◀──────  │  LangGraph pipeline            │
│ (app/jobs.py)            │  update  │  (app/graph.py — 7 nodes)      │
│ status/current_step/     │  _job()  │                                │
│ result per job_id        │          └───────────────┬───────────────┘
└─────────────────────────┘                           │
                                                       ▼
        ┌──────────────────────────────────────────────────────────────────┐
        │  Planner → Research → Content → Layout → OfficeSkill → Validation → Export │
        └──────────────────────────────────────────────────────────────────┘
          │            │           │          │         │             │        │
          ▼            ▼           ▼          ▼         ▼             ▼        ▼
       plain        plain       plain      plain    Messages API   deterministic  pass-
       Claude       Claude      Claude     Claude    + code-exec+   Python check   through
       call         call        call       call      skills betas  (no LLM call)  (no LLM
       (tool-use)  (tool-use,  (tool-use) (tool-use)      │                        call)
                    optional)                              ▼
                                                ┌───────────────────────────┐
                                                │ Anthropic sandboxed        │
                                                │ container:                 │
                                                │  1. reads hosted skill's    │
                                                │     SKILL.md live           │
                                                │  2. writes generation code  │
                                                │  3. runs it (bash tool)     │
                                                │  4. renders to PDF, views    │
                                                │     the image to self-verify│
                                                │  5. exports to $OUTPUT_DIR   │
                                                └──────────────┬─────────────┘
                                                               │ file_id
                                                               ▼
                                                 client.beta.files.download()
                                                               │
                                                               ▼
                                                  backend/output/<uuid>.<ext>
```

## Request lifecycle, step by step

1. **Browser → `POST /generate`** with `{"request": "<prompt text>"}`.
2. `app/main.py:generate()` calls `create_job(request_text)` (`app/jobs.py`) — generates
   a `job_id`, stores `{status: "pending", created_at: time.time(), ...}` in the
   in-memory `_JOBS` dict — then schedules `_run_job(job_id, request_text)` as a FastAPI
   `BackgroundTask` and returns `{"job_id": ...}` with HTTP 202 immediately. The HTTP
   request never waits for generation to finish.
3. **Browser polls `GET /jobs/{job_id}`** every 2.5s (`static/index.html`'s `poll()`),
   rendering the 7-step pipeline UI from `current_step` and `status`.
4. In the background, `_run_job` calls
   `doc_gen_graph.stream({"request": request_text}, stream_mode="updates")` — this
   yields one `{node_name: node_output}` dict per completed node, so `current_step` in
   the job store updates in near-real-time as each node finishes.
5. **The graph runs 7 nodes in a fixed linear order** (`app/graph.py`) — see the table
   below for what each one actually does.
6. When the stream ends, `_run_job` checks `state["validation"]["ok"]`:
   - **Failed** → job marked `status: "failed"`, `error` set from either `state["error"]`
     (an upstream node failure) or the validation failure detail.
   - **Succeeded** → job marked `status: "completed"` with `result = {format, title,
     validation, file_path, size_bytes}`, `finished_at` timestamp set.
7. **Browser sees `status: "completed"`**, renders the result card (title, format
   badge, file size, `finished_at - created_at` as generation time, validation
   details), fires a confetti animation, shows a Download button.
8. **Browser → `GET /jobs/{job_id}/download`** → `FileResponse` streams the actual file
   from `backend/output/`.

## The 7 nodes

| Node | File | Calls Claude? | What it does |
|---|---|---|---|
| **Planner** | `app/nodes/planner.py` | Yes — plain `client.messages.create()`, forced tool-use (`submit_plan`) | Classifies target format (docx/pptx/xlsx/pdf) from the raw request, drafts a short outline, flags whether external facts would be needed |
| **Research** | `app/nodes/research.py` | Yes, *if* `needs_research` — else skipped entirely, no call | Identifies what a real research step would need to fetch (no retrieval is wired up — see README); returns explicit, clearly-labeled placeholder assumptions instead of fabricating facts |
| **Content** | `app/nodes/content.py` | Yes — forced tool-use (`submit_content_plan`) | Writes the actual document content: `{title, sections: [heading\|paragraph\|bullet_list]}`. Format-agnostic — same shape feeds docx and pptx generation alike |
| **Layout** | `app/nodes/layout.py` | Yes — forced tool-use (`submit_layout_notes`) | Proposes tone + a small color palette from the content plan. Pure styling guidance — never touches file bytes |
| **OfficeSkill** | `app/nodes/office_skill.py` | Yes — `client.beta.messages.create()`, betas `code-execution-2025-08-25` + `skills-2025-10-02` | **The only node that generates the actual file.** System prompt = `mnt/skills/private/ai-document-platform/SKILL.md` + `ENGINEERING_NOTES.md` inlined (with `cache_control: ephemeral`); `container.skills` references the Anthropic-hosted skill matching `state["format"]`; `tools: [code_execution_20250825]`. Claude reads the hosted skill live, writes and runs real generation code, renders to PDF, visually inspects the result, exports. Response's `file_id` is downloaded via `client.beta.files.download()` |
| **Validation** | `app/nodes/validation.py` | **No** — pure deterministic Python | Reuses `mnt/skills/public/<format>/ooxml/scripts/unpack.py` (never reimplements OOXML parsing). docx: case-insensitive word-level title check across body + headers + footers. pptx: deliberately does **not** check title wording (the skill has creative license to invent branding) — checks slide count ≥ 1 and no empty slides instead. xlsx: `openpyxl` can open it, has sheets. pdf: `%PDF-` magic bytes |
| **Export** | `app/nodes/export.py` | No | Pass-through: surfaces `file_path` as `download_path` if validation passed, else empty |

**Only one node touches code execution.** Planner/Research/Content/Layout are cheap,
fast, plain tool-use calls — no sandbox, no betas, seconds not minutes. Validation and
Export never call an LLM at all.

## Why OfficeSkill is built the way it is

- **Custom skill upload doesn't work on this account/tier** (`POST /v1/skills?beta=true`
  → 404, verified live). Workaround: inline the orchestration skill's `SKILL.md` +
  `ENGINEERING_NOTES.md` as the `system` prompt instead of uploading it as a formal
  skill object — same effect for a skill that's just instructions.
- **Prompt caching** (`cache_control: {"type": "ephemeral"}` on the system prompt)
  because that ~11KB text is identical on every call — verified live (first call
  creates the cache, second call reads it instead of reprocessing).
- **`max_tokens=16000`**, not the API default — `4096` was observed to truncate a
  generation mid-flight in testing.
- **Budget real time and cost**: a from-scratch generation takes roughly 10 minutes
  and was observed at 371K input tokens for a single call — it's a genuine multi-step
  agentic session (read skill → write code → run → render → view image → export), not
  one lightweight request. See `backend/README.md` for the full cost breakdown.

## Async job architecture

- **In-memory, single-process** (`app/jobs.py`) — a plain dict behind a `threading.Lock`.
  Does not survive a restart, does not work across multiple uvicorn workers. That's a
  deliberate scope choice: proves the async flow works without guessing at real queue
  infrastructure (Redis/Celery/etc.) before there's an actual multi-worker deployment
  to target.
- **Polling, not WebSockets/SSE** — simpler, and the granularity that matters (which of
  the 7 nodes is currently running) doesn't need push-level latency.
- **State machine**: `pending → running → completed | failed`. `current_step` updates
  after each LangGraph node completes, independent of the coarser job `status`.

## What this diagram deliberately doesn't show

No auth, no database, no cloud storage, no multi-user isolation, no real external
research retrieval — see `backend/README.md`'s "What's real vs. explicitly out of
scope" for the full, honest list. Every node and edge drawn above is real and running;
nothing here is aspirational.
