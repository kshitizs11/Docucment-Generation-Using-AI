"""
flat_tool_calls.py -- standalone, self-contained document-generation tool calls.

Deliberately flat, not folder/module-based: one function per format (docx/pptx/xlsx/
pdf), each fully self-sufficient. No imports from this repo's app/ package or any
skills/ folder -- the orchestration instructions that would normally be read from
mnt/skills/private/ai-document-platform/{SKILL.md,ENGINEERING_NOTES.md} are hardcoded
below as string constants instead, so this one file can be lifted out and dropped into
another system without carrying the rest of this repo along.

Only external dependency: `pip install anthropic`. Requires ANTHROPIC_API_KEY in the
environment (or pass api_key= explicitly to any function).

Each generation function:
    call_<format>_tool(content_plan, layout_notes=None, assumptions=None,
                        api_key=None, output_dir=".") -> dict

    content_plan: {"title": str, "sections": [{"type": "heading"|"paragraph"|"bullet_list",
                                              "text": str, "items": [str]}, ...]}
    Returns: {"ok": True, "file_path": str, "file_id": str}
          or {"ok": False, "error": str}

Don't have a content_plan yet, just a plain-English prompt? Use plan_content() first:
    plan = plan_content("a pitch deck for my coffee ordering app")
    result = call_pptx_tool(plan)
plan_content() is a separate, cheap, fast call (plain tool-use, no code execution) --
seconds, not minutes -- and does not decide the format for you; you still choose which
call_<format>_tool to pass its output into.

Verified behavior this duplicates (see the original for the live-testing history that
produced these exact parameter choices -- max_tokens=16000, the specific betas, the
nested file_id response shape): backend/app/nodes/office_skill.py in this repo, and
mnt/skills/private/ai-document-platform/ENGINEERING_NOTES.md's "Production invocation
path" section.
"""
import os
import uuid

import anthropic

BETAS = ["code-execution-2025-08-25", "skills-2025-10-02"]
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 16000  # 4096 was observed to truncate mid-generation -- see ENGINEERING_NOTES.md notes below


def _extract_file_id(response):
    """
    Verified shape: file_id is nested inside a bash_code_execution_tool_result block,
    not at the top level of the response.
    """
    for block in response.content:
        if getattr(block, "type", None) != "bash_code_execution_tool_result":
            continue
        result = getattr(block, "content", None)
        for item in getattr(result, "content", None) or []:
            file_id = getattr(item, "file_id", None)
            if file_id:
                return file_id
    return None


def _build_request_message(fmt, content_plan, layout_notes, assumptions):
    sections = content_plan.get("sections", [])
    sections_text = "\n".join(
        f"- [{s['type']}] " + (s.get("text") or "; ".join(s.get("items", [])))
        for s in sections
    )
    message = (
        f"Generate a .{fmt} file for this content plan.\n\n"
        f"Title: {content_plan.get('title', '')}\n\nSections:\n{sections_text}\n"
    )
    if layout_notes:
        message += (
            f"\nStyling guidance -- tone: {layout_notes.get('tone')}; "
            f"palette: {', '.join(layout_notes.get('palette', []))}; "
            f"notes: {layout_notes.get('notes')}\n"
        )
    if assumptions:
        message += f"\nNote: some values are placeholders ({'; '.join(assumptions)}) -- keep them visibly labeled as such.\n"
    return message


# ============================================================================
# Optional planning helper -- turns a plain-English prompt into the content_plan
# dict every call_<format>_tool() function expects. Self-contained like everything
# else in this file: its own tool schema, no shared code with the generation
# functions below beyond the module-level `anthropic`/`os` imports and MODEL.
# ============================================================================

_PLAN_TOOL = {
    "name": "submit_content_plan",
    "description": "Structured content plan for a document to be generated.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["heading", "paragraph", "bullet_list"]},
                        "text": {"type": "string"},
                        "items": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["type"],
                },
            },
        },
        "required": ["title", "sections"],
    },
}

_PLAN_SYSTEM_PROMPT = (
    "You are a content planner for document generation. Given a request, call "
    "submit_content_plan with the document's actual content: a title, and an ordered "
    "list of sections (heading / paragraph / bullet_list). Write real content, not "
    "placeholders. Do not decide fonts, colors, or layout -- that is handled downstream."
)


def plan_content(prompt, api_key=None):
    """
    Turns a plain-English prompt into {"title": str, "sections": [...]}, ready to pass
    into any call_<format>_tool() function. Plain client.messages.create() -- no code
    execution, no skills container, seconds not minutes.
    """
    client = anthropic.Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=_PLAN_SYSTEM_PROMPT,
        tools=[_PLAN_TOOL],
        tool_choice={"type": "tool", "name": "submit_content_plan"},
        messages=[{"role": "user", "content": prompt}],
    )
    tool_use = next(b for b in response.content if b.type == "tool_use")
    return {"title": tool_use.input["title"], "sections": tool_use.input["sections"]}



# ============================================================================
# DOCX tool call -- fully self-contained, duplicated rather than shared
# with the other three functions below (see module docstring for why).
# ============================================================================

_DOCX_SYSTEM_PROMPT = """---
name: ai-document-platform
description: "Orchestrates end-to-end document generation requests (reports, decks, spreadsheets, filled PDFs) that require planning content across multiple sections before producing a .docx, .pptx, .xlsx, or .pdf. Use when a request asks for a document to be generated from a topic/brief rather than a small direct edit. This skill never generates Office file bytes itself — it plans content, then delegates to the canonical docx/pptx/xlsx/pdf skills in mnt/skills/public/ for actual generation."
---

# AI Document Platform — Orchestration Skill

## Overview

This skill is a **planner and dispatcher**, not a document generator. It exists to turn a loosely
specified request ("a 30-page healthcare report", "a pitch deck from these notes") into a concrete
call into one of the existing, canonical document skills at `mnt/skills/public/{docx,pptx,xlsx,pdf}/`.

**Hard rule: never write OOXML, PDF byte manipulation, or spreadsheet-formula logic directly in this
skill or in generated code that duplicates what a public skill already does.** If you find yourself
about to hand-roll python-docx/pptx XML manipulation instead of following `mnt/skills/public/<type>/SKILL.md`,
stop — you have skipped a step below.

## When this skill applies

- The request implies **multiple sections/slides/sheets** that need planning before content exists
  (a report, a deck, a workbook with several tabs, a multi-field form).
- The request does **not** yet specify which file type — you must infer it (see Step 1).

If the request is a small, single-shot edit to an existing file with a clear target format, skip
planning and go straight to the relevant `mnt/skills/public/<type>/SKILL.md` — don't force it through
this orchestration layer.

## Workflow

### 1. Classify the target format

Infer exactly one of `docx`, `pptx`, `xlsx`, `pdf` from the request. If ambiguous (e.g. "a report" could
be docx or pptx), ask the user rather than guessing.

### 2. Plan the content — format-agnostic

Before touching any document tooling, produce a structured content plan as markdown or JSON:

- A list of sections/slides/sheets, each with a title and a one-line purpose.
- For each section: the actual content (prose, bullet points, table data, chart data) — not
  placeholders. Planning content is the one part of this pipeline that is genuinely LLM work;
  everything after this step is mechanical.

Do not decide layout/styling details that belong to the target skill's workflow (e.g. don't pick
OOXML color codes here — that's `pptx/SKILL.md`'s job).

### 3. Discover the canonical skill

Read `mnt/skills/public/<format>/SKILL.md` in full before writing any code. Follow its documented
workflow exactly — including any files it tells you to read next (e.g. pptx's `SKILL.md` tells you to
read `html2pptx.md` in full before creating slides from scratch; docx's tells you when to use
`docx-js.md` vs `ooxml.md`).

### 4. Generate — using the target skill's own tools

Use the scripts and libraries the target `SKILL.md` documents (`ooxml/scripts/{unpack,pack,validate}.py`,
`recalc.py`, `html2pptx`, python-docx/pptx/pdf, etc.) with the content plan from Step 2 as input. Do not
substitute your own document-generation approach for the one documented.

### 5. Validate

Follow the target skill's own validation step, which differs by how the file was produced:

- **Editing an existing file** (unpack → edit XML → pack): run `ooxml/scripts/validate.py` with
  `--original` set to the source file, as documented. This is the only case that script covers.
- **From-scratch generation** (`docx-js`, `html2pptx`, `python-docx`/`python-pptx`, `openpyxl`): there is
  no dedicated "validate a fresh file" script — `ooxml/scripts/validate.py` does not apply since there is
  no original to diff against. Instead: unpack the produced file with `ooxml/scripts/unpack.py` and
  confirm it is a well-formed OOXML zip, then spot-check that the planned content (Step 2) actually landed
  in the relevant XML part (e.g. `word/document.xml`, `ppt/slides/slideN.xml`). For xlsx, run `recalc.py`
  regardless of how the file was produced — formulas always need recalculation.
- **PDF forms**: run the fillable-field check scripts as documented in `pdf/SKILL.md`.

A document is not done until it passes the applicable check above.

### 6. Return the artifact

Report the output file path and a one-line summary of what was generated. Do not re-describe the full
content plan back to the user unless asked — the file is the deliverable.

## Explicit non-goals

- This skill does not implement authentication, storage, job queues, or a UI. Those are separate
  concerns for whatever application embeds this skill; they must not leak into document-generation
  logic.
- This skill does not maintain its own copy of OOXML/PDF reference material. If reference material
  seems missing, it belongs in the relevant `mnt/skills/public/<type>/` skill, not here.


---

# Engineering rules — AI Document Platform

These rules apply to any code, agent, or prompt that sits on top of this repository's document skills
(`mnt/skills/public/docx`, `pptx`, `xlsx`, `pdf`).

## Non-negotiable

1. **Never reimplement document generation.** Do not write OOXML XML manipulation, PDF byte-level
   editing, or spreadsheet formula engines outside of `mnt/skills/public/*`. Those skills are the
   canonical implementation. If a capability seems missing, extend the relevant public skill — don't
   fork the logic into a new layer.
2. **Always read the target `SKILL.md` before generating.** Every code path that produces a
   docx/pptx/xlsx/pdf must have first read that format's `SKILL.md` (and whatever it tells you to read
   next) in the same session/turn that generates the file. Cached knowledge from a previous session is
   not a substitute — skill docs can change.
3. **Keep planning and generation separate.** Content planning (what sections exist, what they say) is
   allowed to be free-form LLM output. Generation (turning that plan into file bytes) must go through
   the documented scripts/libraries only.
4. **No silent format guessing.** If the target file type is ambiguous, ask — don't default to one
   format and hope.

## Scope boundary

This directory (`mnt/skills/private/ai-document-platform/`) defines *orchestration* behavior only. It
must not contain:
- Application infrastructure (auth, DB schemas, API routes, queues) — those live in the application
  repo that embeds this skill, not here.
- Duplicated reference material from the public skills.

## Production invocation path (decision) — Messages API skills + code-execution betas

**Verified live** against a real Anthropic API key: `client.beta.messages.create()` with
`betas=["code-execution-2025-08-25", "skills-2025-10-02"]`, `container.skills` referencing the hosted
`docx` skill, and this skill's own `SKILL.md`/`CLAUDE.md` inlined as the `system` prompt. Full pipeline
observed end to end — the model read the hosted skill's `SKILL.md`, wrote a docx-js script, ran it,
rendered the output to PDF and visually inspected a page image to verify formatting, then exported the
file. Downloaded via `client.beta.files.download(file_id)` and confirmed as valid, correctly-populated
OOXML. Test harness: `tools/skills-api-test/test_skills_api.py`.

Two things learned that aren't obvious from the docs:
- **Custom skill upload (`POST /v1/skills?beta=true`, i.e. `client.beta.skills.create()`) returned 404**
  on this account/tier — not enabled for this key, independent of the SDK or request shape (confirmed via
  raw `curl` too). Workaround used instead: inline this skill's `SKILL.md`/`CLAUDE.md` text directly as
  the `system` prompt rather than uploading it as a formal skill object. For a skill that's just
  instructions (no bundled scripts of its own), this achieves the same effect. Retry the upload path later
  if that endpoint becomes available — it's cleaner for versioning the orchestration skill independently
  of any one call site.
- **Generated files are referenced as `file_id`s nested inside the tool-result content**, not at the
  top level of the response: `content[i].type == "bash_code_execution_tool_result"` →
  `content[i].content.content == [{"file_id": "...", "type": "bash_code_execution_output"}]`. Download
  with `client.beta.files.download(file_id, betas=[...])`, then `.write_to_file(path)`.
- Use `client.beta.messages.create()`, not `client.messages.create()` — the plain (non-beta) resource in
  the current SDK doesn't accept `betas`.
- Budget real time for this: a from-scratch docx generation call (read skill → write script → run →
  LibreOffice PDF render → visual verification → export) took roughly 10 minutes end to end with
  `max_tokens=16000`. `max_tokens=4096` was not enough and truncated mid-turn on an earlier attempt.

Given a multi-tenant backend hands arbitrary end-user requests to the model, this sandboxed-container path
remains the right call over the CLI/Agent SDK (real filesystem + Bash access, appropriate for a trusted
single operator, not for arbitrary end-user requests).

## Full pipeline: `backend/` (FastAPI + LangGraph)

This invocation path is now wrapped in a 7-node LangGraph pipeline (Planner → Research → Content → Layout
→ OfficeSkill → Validation → Export), exposed via FastAPI at `POST /generate`. See `backend/README.md` for
the full writeup. The OfficeSkill node there is exactly the pattern verified above, just seeded with a
content plan and layout notes from upstream nodes instead of re-planning from scratch. Only Planner
through Layout are plain (non-beta) Claude calls; OfficeSkill is the one node using this section's
verified betas/container pattern. Validation is deterministic (reuses `mnt/skills/public/*/ooxml/scripts`,
no LLM call). Research and Export have explicitly-marked missing integrations (no real retrieval, no
cloud storage) — see `backend/README.md`'s "What's real vs. explicitly out of scope" section rather than
assuming those are complete.

## Superseded: LLM-plans/fixed-code-generates (TrueFoundry-based)

Built and locally verified (docx and pptx generators both produced valid files from fixed content plans)
during a period when only an OpenAI-compatible gateway key (TrueFoundry) was available, no direct
Anthropic key. Removed once a real Anthropic key made the primary path above testable — kept here only as
a historical note in case direct-LLM-credential-only constraints recur:
- LLM did content planning only (forced tool-use), fixed backend modules did generation per format.
- Blocked from full verification: the available TrueFoundry service account (`qna-agent-um`) returned
  `401: Service account does not exist` — a dead credential, unrelated to the architecture itself.

## Why this exists

`mnt/skills/public/*` already encodes battle-tested, licensed document-generation workflows. Any
platform built on top of this repo gets that for free — but only if every layer above it treats those
skills as an opaque, canonical engine rather than something to be reverse-engineered or partially
reimplemented for convenience.
"""


def call_docx_tool(content_plan, layout_notes=None, assumptions=None, api_key=None, output_dir="."):
    """Generate a .docx file via Claude's code-execution + skills API. See module docstring for the return shape."""
    client = anthropic.Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])

    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        betas=BETAS,
        system=[{"type": "text", "text": _DOCX_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        container={"skills": [{"type": "anthropic", "skill_id": "docx", "version": "latest"}]},
        tools=[{"type": "code_execution_20250825", "name": "code_execution"}],
        messages=[{"role": "user", "content": _build_request_message("docx", content_plan, layout_notes, assumptions)}],
    )

    file_id = _extract_file_id(response)
    if not file_id:
        return {"ok": False, "error": f"no file_id found in response (stop_reason={response.stop_reason})"}

    os.makedirs(output_dir, exist_ok=True)
    dest = os.path.join(output_dir, f"{uuid.uuid4().hex}.docx")
    client.beta.files.download(file_id, betas=["code-execution-2025-08-25"]).write_to_file(dest)
    return {"ok": True, "file_path": dest, "file_id": file_id}


# ============================================================================
# PPTX tool call -- fully self-contained, duplicated rather than shared
# with the other three functions below (see module docstring for why).
# ============================================================================

_PPTX_SYSTEM_PROMPT = """---
name: ai-document-platform
description: "Orchestrates end-to-end document generation requests (reports, decks, spreadsheets, filled PDFs) that require planning content across multiple sections before producing a .docx, .pptx, .xlsx, or .pdf. Use when a request asks for a document to be generated from a topic/brief rather than a small direct edit. This skill never generates Office file bytes itself — it plans content, then delegates to the canonical docx/pptx/xlsx/pdf skills in mnt/skills/public/ for actual generation."
---

# AI Document Platform — Orchestration Skill

## Overview

This skill is a **planner and dispatcher**, not a document generator. It exists to turn a loosely
specified request ("a 30-page healthcare report", "a pitch deck from these notes") into a concrete
call into one of the existing, canonical document skills at `mnt/skills/public/{docx,pptx,xlsx,pdf}/`.

**Hard rule: never write OOXML, PDF byte manipulation, or spreadsheet-formula logic directly in this
skill or in generated code that duplicates what a public skill already does.** If you find yourself
about to hand-roll python-docx/pptx XML manipulation instead of following `mnt/skills/public/<type>/SKILL.md`,
stop — you have skipped a step below.

## When this skill applies

- The request implies **multiple sections/slides/sheets** that need planning before content exists
  (a report, a deck, a workbook with several tabs, a multi-field form).
- The request does **not** yet specify which file type — you must infer it (see Step 1).

If the request is a small, single-shot edit to an existing file with a clear target format, skip
planning and go straight to the relevant `mnt/skills/public/<type>/SKILL.md` — don't force it through
this orchestration layer.

## Workflow

### 1. Classify the target format

Infer exactly one of `docx`, `pptx`, `xlsx`, `pdf` from the request. If ambiguous (e.g. "a report" could
be docx or pptx), ask the user rather than guessing.

### 2. Plan the content — format-agnostic

Before touching any document tooling, produce a structured content plan as markdown or JSON:

- A list of sections/slides/sheets, each with a title and a one-line purpose.
- For each section: the actual content (prose, bullet points, table data, chart data) — not
  placeholders. Planning content is the one part of this pipeline that is genuinely LLM work;
  everything after this step is mechanical.

Do not decide layout/styling details that belong to the target skill's workflow (e.g. don't pick
OOXML color codes here — that's `pptx/SKILL.md`'s job).

### 3. Discover the canonical skill

Read `mnt/skills/public/<format>/SKILL.md` in full before writing any code. Follow its documented
workflow exactly — including any files it tells you to read next (e.g. pptx's `SKILL.md` tells you to
read `html2pptx.md` in full before creating slides from scratch; docx's tells you when to use
`docx-js.md` vs `ooxml.md`).

### 4. Generate — using the target skill's own tools

Use the scripts and libraries the target `SKILL.md` documents (`ooxml/scripts/{unpack,pack,validate}.py`,
`recalc.py`, `html2pptx`, python-docx/pptx/pdf, etc.) with the content plan from Step 2 as input. Do not
substitute your own document-generation approach for the one documented.

### 5. Validate

Follow the target skill's own validation step, which differs by how the file was produced:

- **Editing an existing file** (unpack → edit XML → pack): run `ooxml/scripts/validate.py` with
  `--original` set to the source file, as documented. This is the only case that script covers.
- **From-scratch generation** (`docx-js`, `html2pptx`, `python-docx`/`python-pptx`, `openpyxl`): there is
  no dedicated "validate a fresh file" script — `ooxml/scripts/validate.py` does not apply since there is
  no original to diff against. Instead: unpack the produced file with `ooxml/scripts/unpack.py` and
  confirm it is a well-formed OOXML zip, then spot-check that the planned content (Step 2) actually landed
  in the relevant XML part (e.g. `word/document.xml`, `ppt/slides/slideN.xml`). For xlsx, run `recalc.py`
  regardless of how the file was produced — formulas always need recalculation.
- **PDF forms**: run the fillable-field check scripts as documented in `pdf/SKILL.md`.

A document is not done until it passes the applicable check above.

### 6. Return the artifact

Report the output file path and a one-line summary of what was generated. Do not re-describe the full
content plan back to the user unless asked — the file is the deliverable.

## Explicit non-goals

- This skill does not implement authentication, storage, job queues, or a UI. Those are separate
  concerns for whatever application embeds this skill; they must not leak into document-generation
  logic.
- This skill does not maintain its own copy of OOXML/PDF reference material. If reference material
  seems missing, it belongs in the relevant `mnt/skills/public/<type>/` skill, not here.


---

# Engineering rules — AI Document Platform

These rules apply to any code, agent, or prompt that sits on top of this repository's document skills
(`mnt/skills/public/docx`, `pptx`, `xlsx`, `pdf`).

## Non-negotiable

1. **Never reimplement document generation.** Do not write OOXML XML manipulation, PDF byte-level
   editing, or spreadsheet formula engines outside of `mnt/skills/public/*`. Those skills are the
   canonical implementation. If a capability seems missing, extend the relevant public skill — don't
   fork the logic into a new layer.
2. **Always read the target `SKILL.md` before generating.** Every code path that produces a
   docx/pptx/xlsx/pdf must have first read that format's `SKILL.md` (and whatever it tells you to read
   next) in the same session/turn that generates the file. Cached knowledge from a previous session is
   not a substitute — skill docs can change.
3. **Keep planning and generation separate.** Content planning (what sections exist, what they say) is
   allowed to be free-form LLM output. Generation (turning that plan into file bytes) must go through
   the documented scripts/libraries only.
4. **No silent format guessing.** If the target file type is ambiguous, ask — don't default to one
   format and hope.

## Scope boundary

This directory (`mnt/skills/private/ai-document-platform/`) defines *orchestration* behavior only. It
must not contain:
- Application infrastructure (auth, DB schemas, API routes, queues) — those live in the application
  repo that embeds this skill, not here.
- Duplicated reference material from the public skills.

## Production invocation path (decision) — Messages API skills + code-execution betas

**Verified live** against a real Anthropic API key: `client.beta.messages.create()` with
`betas=["code-execution-2025-08-25", "skills-2025-10-02"]`, `container.skills` referencing the hosted
`docx` skill, and this skill's own `SKILL.md`/`CLAUDE.md` inlined as the `system` prompt. Full pipeline
observed end to end — the model read the hosted skill's `SKILL.md`, wrote a docx-js script, ran it,
rendered the output to PDF and visually inspected a page image to verify formatting, then exported the
file. Downloaded via `client.beta.files.download(file_id)` and confirmed as valid, correctly-populated
OOXML. Test harness: `tools/skills-api-test/test_skills_api.py`.

Two things learned that aren't obvious from the docs:
- **Custom skill upload (`POST /v1/skills?beta=true`, i.e. `client.beta.skills.create()`) returned 404**
  on this account/tier — not enabled for this key, independent of the SDK or request shape (confirmed via
  raw `curl` too). Workaround used instead: inline this skill's `SKILL.md`/`CLAUDE.md` text directly as
  the `system` prompt rather than uploading it as a formal skill object. For a skill that's just
  instructions (no bundled scripts of its own), this achieves the same effect. Retry the upload path later
  if that endpoint becomes available — it's cleaner for versioning the orchestration skill independently
  of any one call site.
- **Generated files are referenced as `file_id`s nested inside the tool-result content**, not at the
  top level of the response: `content[i].type == "bash_code_execution_tool_result"` →
  `content[i].content.content == [{"file_id": "...", "type": "bash_code_execution_output"}]`. Download
  with `client.beta.files.download(file_id, betas=[...])`, then `.write_to_file(path)`.
- Use `client.beta.messages.create()`, not `client.messages.create()` — the plain (non-beta) resource in
  the current SDK doesn't accept `betas`.
- Budget real time for this: a from-scratch docx generation call (read skill → write script → run →
  LibreOffice PDF render → visual verification → export) took roughly 10 minutes end to end with
  `max_tokens=16000`. `max_tokens=4096` was not enough and truncated mid-turn on an earlier attempt.

Given a multi-tenant backend hands arbitrary end-user requests to the model, this sandboxed-container path
remains the right call over the CLI/Agent SDK (real filesystem + Bash access, appropriate for a trusted
single operator, not for arbitrary end-user requests).

## Full pipeline: `backend/` (FastAPI + LangGraph)

This invocation path is now wrapped in a 7-node LangGraph pipeline (Planner → Research → Content → Layout
→ OfficeSkill → Validation → Export), exposed via FastAPI at `POST /generate`. See `backend/README.md` for
the full writeup. The OfficeSkill node there is exactly the pattern verified above, just seeded with a
content plan and layout notes from upstream nodes instead of re-planning from scratch. Only Planner
through Layout are plain (non-beta) Claude calls; OfficeSkill is the one node using this section's
verified betas/container pattern. Validation is deterministic (reuses `mnt/skills/public/*/ooxml/scripts`,
no LLM call). Research and Export have explicitly-marked missing integrations (no real retrieval, no
cloud storage) — see `backend/README.md`'s "What's real vs. explicitly out of scope" section rather than
assuming those are complete.

## Superseded: LLM-plans/fixed-code-generates (TrueFoundry-based)

Built and locally verified (docx and pptx generators both produced valid files from fixed content plans)
during a period when only an OpenAI-compatible gateway key (TrueFoundry) was available, no direct
Anthropic key. Removed once a real Anthropic key made the primary path above testable — kept here only as
a historical note in case direct-LLM-credential-only constraints recur:
- LLM did content planning only (forced tool-use), fixed backend modules did generation per format.
- Blocked from full verification: the available TrueFoundry service account (`qna-agent-um`) returned
  `401: Service account does not exist` — a dead credential, unrelated to the architecture itself.

## Why this exists

`mnt/skills/public/*` already encodes battle-tested, licensed document-generation workflows. Any
platform built on top of this repo gets that for free — but only if every layer above it treats those
skills as an opaque, canonical engine rather than something to be reverse-engineered or partially
reimplemented for convenience.
"""


def call_pptx_tool(content_plan, layout_notes=None, assumptions=None, api_key=None, output_dir="."):
    """Generate a .pptx file via Claude's code-execution + skills API. See module docstring for the return shape."""
    client = anthropic.Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])

    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        betas=BETAS,
        system=[{"type": "text", "text": _PPTX_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        container={"skills": [{"type": "anthropic", "skill_id": "pptx", "version": "latest"}]},
        tools=[{"type": "code_execution_20250825", "name": "code_execution"}],
        messages=[{"role": "user", "content": _build_request_message("pptx", content_plan, layout_notes, assumptions)}],
    )

    file_id = _extract_file_id(response)
    if not file_id:
        return {"ok": False, "error": f"no file_id found in response (stop_reason={response.stop_reason})"}

    os.makedirs(output_dir, exist_ok=True)
    dest = os.path.join(output_dir, f"{uuid.uuid4().hex}.pptx")
    client.beta.files.download(file_id, betas=["code-execution-2025-08-25"]).write_to_file(dest)
    return {"ok": True, "file_path": dest, "file_id": file_id}


# ============================================================================
# XLSX tool call -- fully self-contained, duplicated rather than shared
# with the other three functions below (see module docstring for why).
# ============================================================================

_XLSX_SYSTEM_PROMPT = """---
name: ai-document-platform
description: "Orchestrates end-to-end document generation requests (reports, decks, spreadsheets, filled PDFs) that require planning content across multiple sections before producing a .docx, .pptx, .xlsx, or .pdf. Use when a request asks for a document to be generated from a topic/brief rather than a small direct edit. This skill never generates Office file bytes itself — it plans content, then delegates to the canonical docx/pptx/xlsx/pdf skills in mnt/skills/public/ for actual generation."
---

# AI Document Platform — Orchestration Skill

## Overview

This skill is a **planner and dispatcher**, not a document generator. It exists to turn a loosely
specified request ("a 30-page healthcare report", "a pitch deck from these notes") into a concrete
call into one of the existing, canonical document skills at `mnt/skills/public/{docx,pptx,xlsx,pdf}/`.

**Hard rule: never write OOXML, PDF byte manipulation, or spreadsheet-formula logic directly in this
skill or in generated code that duplicates what a public skill already does.** If you find yourself
about to hand-roll python-docx/pptx XML manipulation instead of following `mnt/skills/public/<type>/SKILL.md`,
stop — you have skipped a step below.

## When this skill applies

- The request implies **multiple sections/slides/sheets** that need planning before content exists
  (a report, a deck, a workbook with several tabs, a multi-field form).
- The request does **not** yet specify which file type — you must infer it (see Step 1).

If the request is a small, single-shot edit to an existing file with a clear target format, skip
planning and go straight to the relevant `mnt/skills/public/<type>/SKILL.md` — don't force it through
this orchestration layer.

## Workflow

### 1. Classify the target format

Infer exactly one of `docx`, `pptx`, `xlsx`, `pdf` from the request. If ambiguous (e.g. "a report" could
be docx or pptx), ask the user rather than guessing.

### 2. Plan the content — format-agnostic

Before touching any document tooling, produce a structured content plan as markdown or JSON:

- A list of sections/slides/sheets, each with a title and a one-line purpose.
- For each section: the actual content (prose, bullet points, table data, chart data) — not
  placeholders. Planning content is the one part of this pipeline that is genuinely LLM work;
  everything after this step is mechanical.

Do not decide layout/styling details that belong to the target skill's workflow (e.g. don't pick
OOXML color codes here — that's `pptx/SKILL.md`'s job).

### 3. Discover the canonical skill

Read `mnt/skills/public/<format>/SKILL.md` in full before writing any code. Follow its documented
workflow exactly — including any files it tells you to read next (e.g. pptx's `SKILL.md` tells you to
read `html2pptx.md` in full before creating slides from scratch; docx's tells you when to use
`docx-js.md` vs `ooxml.md`).

### 4. Generate — using the target skill's own tools

Use the scripts and libraries the target `SKILL.md` documents (`ooxml/scripts/{unpack,pack,validate}.py`,
`recalc.py`, `html2pptx`, python-docx/pptx/pdf, etc.) with the content plan from Step 2 as input. Do not
substitute your own document-generation approach for the one documented.

### 5. Validate

Follow the target skill's own validation step, which differs by how the file was produced:

- **Editing an existing file** (unpack → edit XML → pack): run `ooxml/scripts/validate.py` with
  `--original` set to the source file, as documented. This is the only case that script covers.
- **From-scratch generation** (`docx-js`, `html2pptx`, `python-docx`/`python-pptx`, `openpyxl`): there is
  no dedicated "validate a fresh file" script — `ooxml/scripts/validate.py` does not apply since there is
  no original to diff against. Instead: unpack the produced file with `ooxml/scripts/unpack.py` and
  confirm it is a well-formed OOXML zip, then spot-check that the planned content (Step 2) actually landed
  in the relevant XML part (e.g. `word/document.xml`, `ppt/slides/slideN.xml`). For xlsx, run `recalc.py`
  regardless of how the file was produced — formulas always need recalculation.
- **PDF forms**: run the fillable-field check scripts as documented in `pdf/SKILL.md`.

A document is not done until it passes the applicable check above.

### 6. Return the artifact

Report the output file path and a one-line summary of what was generated. Do not re-describe the full
content plan back to the user unless asked — the file is the deliverable.

## Explicit non-goals

- This skill does not implement authentication, storage, job queues, or a UI. Those are separate
  concerns for whatever application embeds this skill; they must not leak into document-generation
  logic.
- This skill does not maintain its own copy of OOXML/PDF reference material. If reference material
  seems missing, it belongs in the relevant `mnt/skills/public/<type>/` skill, not here.


---

# Engineering rules — AI Document Platform

These rules apply to any code, agent, or prompt that sits on top of this repository's document skills
(`mnt/skills/public/docx`, `pptx`, `xlsx`, `pdf`).

## Non-negotiable

1. **Never reimplement document generation.** Do not write OOXML XML manipulation, PDF byte-level
   editing, or spreadsheet formula engines outside of `mnt/skills/public/*`. Those skills are the
   canonical implementation. If a capability seems missing, extend the relevant public skill — don't
   fork the logic into a new layer.
2. **Always read the target `SKILL.md` before generating.** Every code path that produces a
   docx/pptx/xlsx/pdf must have first read that format's `SKILL.md` (and whatever it tells you to read
   next) in the same session/turn that generates the file. Cached knowledge from a previous session is
   not a substitute — skill docs can change.
3. **Keep planning and generation separate.** Content planning (what sections exist, what they say) is
   allowed to be free-form LLM output. Generation (turning that plan into file bytes) must go through
   the documented scripts/libraries only.
4. **No silent format guessing.** If the target file type is ambiguous, ask — don't default to one
   format and hope.

## Scope boundary

This directory (`mnt/skills/private/ai-document-platform/`) defines *orchestration* behavior only. It
must not contain:
- Application infrastructure (auth, DB schemas, API routes, queues) — those live in the application
  repo that embeds this skill, not here.
- Duplicated reference material from the public skills.

## Production invocation path (decision) — Messages API skills + code-execution betas

**Verified live** against a real Anthropic API key: `client.beta.messages.create()` with
`betas=["code-execution-2025-08-25", "skills-2025-10-02"]`, `container.skills` referencing the hosted
`docx` skill, and this skill's own `SKILL.md`/`CLAUDE.md` inlined as the `system` prompt. Full pipeline
observed end to end — the model read the hosted skill's `SKILL.md`, wrote a docx-js script, ran it,
rendered the output to PDF and visually inspected a page image to verify formatting, then exported the
file. Downloaded via `client.beta.files.download(file_id)` and confirmed as valid, correctly-populated
OOXML. Test harness: `tools/skills-api-test/test_skills_api.py`.

Two things learned that aren't obvious from the docs:
- **Custom skill upload (`POST /v1/skills?beta=true`, i.e. `client.beta.skills.create()`) returned 404**
  on this account/tier — not enabled for this key, independent of the SDK or request shape (confirmed via
  raw `curl` too). Workaround used instead: inline this skill's `SKILL.md`/`CLAUDE.md` text directly as
  the `system` prompt rather than uploading it as a formal skill object. For a skill that's just
  instructions (no bundled scripts of its own), this achieves the same effect. Retry the upload path later
  if that endpoint becomes available — it's cleaner for versioning the orchestration skill independently
  of any one call site.
- **Generated files are referenced as `file_id`s nested inside the tool-result content**, not at the
  top level of the response: `content[i].type == "bash_code_execution_tool_result"` →
  `content[i].content.content == [{"file_id": "...", "type": "bash_code_execution_output"}]`. Download
  with `client.beta.files.download(file_id, betas=[...])`, then `.write_to_file(path)`.
- Use `client.beta.messages.create()`, not `client.messages.create()` — the plain (non-beta) resource in
  the current SDK doesn't accept `betas`.
- Budget real time for this: a from-scratch docx generation call (read skill → write script → run →
  LibreOffice PDF render → visual verification → export) took roughly 10 minutes end to end with
  `max_tokens=16000`. `max_tokens=4096` was not enough and truncated mid-turn on an earlier attempt.

Given a multi-tenant backend hands arbitrary end-user requests to the model, this sandboxed-container path
remains the right call over the CLI/Agent SDK (real filesystem + Bash access, appropriate for a trusted
single operator, not for arbitrary end-user requests).

## Full pipeline: `backend/` (FastAPI + LangGraph)

This invocation path is now wrapped in a 7-node LangGraph pipeline (Planner → Research → Content → Layout
→ OfficeSkill → Validation → Export), exposed via FastAPI at `POST /generate`. See `backend/README.md` for
the full writeup. The OfficeSkill node there is exactly the pattern verified above, just seeded with a
content plan and layout notes from upstream nodes instead of re-planning from scratch. Only Planner
through Layout are plain (non-beta) Claude calls; OfficeSkill is the one node using this section's
verified betas/container pattern. Validation is deterministic (reuses `mnt/skills/public/*/ooxml/scripts`,
no LLM call). Research and Export have explicitly-marked missing integrations (no real retrieval, no
cloud storage) — see `backend/README.md`'s "What's real vs. explicitly out of scope" section rather than
assuming those are complete.

## Superseded: LLM-plans/fixed-code-generates (TrueFoundry-based)

Built and locally verified (docx and pptx generators both produced valid files from fixed content plans)
during a period when only an OpenAI-compatible gateway key (TrueFoundry) was available, no direct
Anthropic key. Removed once a real Anthropic key made the primary path above testable — kept here only as
a historical note in case direct-LLM-credential-only constraints recur:
- LLM did content planning only (forced tool-use), fixed backend modules did generation per format.
- Blocked from full verification: the available TrueFoundry service account (`qna-agent-um`) returned
  `401: Service account does not exist` — a dead credential, unrelated to the architecture itself.

## Why this exists

`mnt/skills/public/*` already encodes battle-tested, licensed document-generation workflows. Any
platform built on top of this repo gets that for free — but only if every layer above it treats those
skills as an opaque, canonical engine rather than something to be reverse-engineered or partially
reimplemented for convenience.
"""


def call_xlsx_tool(content_plan, layout_notes=None, assumptions=None, api_key=None, output_dir="."):
    """Generate a .xlsx file via Claude's code-execution + skills API. See module docstring for the return shape."""
    client = anthropic.Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])

    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        betas=BETAS,
        system=[{"type": "text", "text": _XLSX_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        container={"skills": [{"type": "anthropic", "skill_id": "xlsx", "version": "latest"}]},
        tools=[{"type": "code_execution_20250825", "name": "code_execution"}],
        messages=[{"role": "user", "content": _build_request_message("xlsx", content_plan, layout_notes, assumptions)}],
    )

    file_id = _extract_file_id(response)
    if not file_id:
        return {"ok": False, "error": f"no file_id found in response (stop_reason={response.stop_reason})"}

    os.makedirs(output_dir, exist_ok=True)
    dest = os.path.join(output_dir, f"{uuid.uuid4().hex}.xlsx")
    client.beta.files.download(file_id, betas=["code-execution-2025-08-25"]).write_to_file(dest)
    return {"ok": True, "file_path": dest, "file_id": file_id}


# ============================================================================
# PDF tool call -- fully self-contained, duplicated rather than shared
# with the other three functions below (see module docstring for why).
# ============================================================================

_PDF_SYSTEM_PROMPT = """---
name: ai-document-platform
description: "Orchestrates end-to-end document generation requests (reports, decks, spreadsheets, filled PDFs) that require planning content across multiple sections before producing a .docx, .pptx, .xlsx, or .pdf. Use when a request asks for a document to be generated from a topic/brief rather than a small direct edit. This skill never generates Office file bytes itself — it plans content, then delegates to the canonical docx/pptx/xlsx/pdf skills in mnt/skills/public/ for actual generation."
---

# AI Document Platform — Orchestration Skill

## Overview

This skill is a **planner and dispatcher**, not a document generator. It exists to turn a loosely
specified request ("a 30-page healthcare report", "a pitch deck from these notes") into a concrete
call into one of the existing, canonical document skills at `mnt/skills/public/{docx,pptx,xlsx,pdf}/`.

**Hard rule: never write OOXML, PDF byte manipulation, or spreadsheet-formula logic directly in this
skill or in generated code that duplicates what a public skill already does.** If you find yourself
about to hand-roll python-docx/pptx XML manipulation instead of following `mnt/skills/public/<type>/SKILL.md`,
stop — you have skipped a step below.

## When this skill applies

- The request implies **multiple sections/slides/sheets** that need planning before content exists
  (a report, a deck, a workbook with several tabs, a multi-field form).
- The request does **not** yet specify which file type — you must infer it (see Step 1).

If the request is a small, single-shot edit to an existing file with a clear target format, skip
planning and go straight to the relevant `mnt/skills/public/<type>/SKILL.md` — don't force it through
this orchestration layer.

## Workflow

### 1. Classify the target format

Infer exactly one of `docx`, `pptx`, `xlsx`, `pdf` from the request. If ambiguous (e.g. "a report" could
be docx or pptx), ask the user rather than guessing.

### 2. Plan the content — format-agnostic

Before touching any document tooling, produce a structured content plan as markdown or JSON:

- A list of sections/slides/sheets, each with a title and a one-line purpose.
- For each section: the actual content (prose, bullet points, table data, chart data) — not
  placeholders. Planning content is the one part of this pipeline that is genuinely LLM work;
  everything after this step is mechanical.

Do not decide layout/styling details that belong to the target skill's workflow (e.g. don't pick
OOXML color codes here — that's `pptx/SKILL.md`'s job).

### 3. Discover the canonical skill

Read `mnt/skills/public/<format>/SKILL.md` in full before writing any code. Follow its documented
workflow exactly — including any files it tells you to read next (e.g. pptx's `SKILL.md` tells you to
read `html2pptx.md` in full before creating slides from scratch; docx's tells you when to use
`docx-js.md` vs `ooxml.md`).

### 4. Generate — using the target skill's own tools

Use the scripts and libraries the target `SKILL.md` documents (`ooxml/scripts/{unpack,pack,validate}.py`,
`recalc.py`, `html2pptx`, python-docx/pptx/pdf, etc.) with the content plan from Step 2 as input. Do not
substitute your own document-generation approach for the one documented.

### 5. Validate

Follow the target skill's own validation step, which differs by how the file was produced:

- **Editing an existing file** (unpack → edit XML → pack): run `ooxml/scripts/validate.py` with
  `--original` set to the source file, as documented. This is the only case that script covers.
- **From-scratch generation** (`docx-js`, `html2pptx`, `python-docx`/`python-pptx`, `openpyxl`): there is
  no dedicated "validate a fresh file" script — `ooxml/scripts/validate.py` does not apply since there is
  no original to diff against. Instead: unpack the produced file with `ooxml/scripts/unpack.py` and
  confirm it is a well-formed OOXML zip, then spot-check that the planned content (Step 2) actually landed
  in the relevant XML part (e.g. `word/document.xml`, `ppt/slides/slideN.xml`). For xlsx, run `recalc.py`
  regardless of how the file was produced — formulas always need recalculation.
- **PDF forms**: run the fillable-field check scripts as documented in `pdf/SKILL.md`.

A document is not done until it passes the applicable check above.

### 6. Return the artifact

Report the output file path and a one-line summary of what was generated. Do not re-describe the full
content plan back to the user unless asked — the file is the deliverable.

## Explicit non-goals

- This skill does not implement authentication, storage, job queues, or a UI. Those are separate
  concerns for whatever application embeds this skill; they must not leak into document-generation
  logic.
- This skill does not maintain its own copy of OOXML/PDF reference material. If reference material
  seems missing, it belongs in the relevant `mnt/skills/public/<type>/` skill, not here.


---

# Engineering rules — AI Document Platform

These rules apply to any code, agent, or prompt that sits on top of this repository's document skills
(`mnt/skills/public/docx`, `pptx`, `xlsx`, `pdf`).

## Non-negotiable

1. **Never reimplement document generation.** Do not write OOXML XML manipulation, PDF byte-level
   editing, or spreadsheet formula engines outside of `mnt/skills/public/*`. Those skills are the
   canonical implementation. If a capability seems missing, extend the relevant public skill — don't
   fork the logic into a new layer.
2. **Always read the target `SKILL.md` before generating.** Every code path that produces a
   docx/pptx/xlsx/pdf must have first read that format's `SKILL.md` (and whatever it tells you to read
   next) in the same session/turn that generates the file. Cached knowledge from a previous session is
   not a substitute — skill docs can change.
3. **Keep planning and generation separate.** Content planning (what sections exist, what they say) is
   allowed to be free-form LLM output. Generation (turning that plan into file bytes) must go through
   the documented scripts/libraries only.
4. **No silent format guessing.** If the target file type is ambiguous, ask — don't default to one
   format and hope.

## Scope boundary

This directory (`mnt/skills/private/ai-document-platform/`) defines *orchestration* behavior only. It
must not contain:
- Application infrastructure (auth, DB schemas, API routes, queues) — those live in the application
  repo that embeds this skill, not here.
- Duplicated reference material from the public skills.

## Production invocation path (decision) — Messages API skills + code-execution betas

**Verified live** against a real Anthropic API key: `client.beta.messages.create()` with
`betas=["code-execution-2025-08-25", "skills-2025-10-02"]`, `container.skills` referencing the hosted
`docx` skill, and this skill's own `SKILL.md`/`CLAUDE.md` inlined as the `system` prompt. Full pipeline
observed end to end — the model read the hosted skill's `SKILL.md`, wrote a docx-js script, ran it,
rendered the output to PDF and visually inspected a page image to verify formatting, then exported the
file. Downloaded via `client.beta.files.download(file_id)` and confirmed as valid, correctly-populated
OOXML. Test harness: `tools/skills-api-test/test_skills_api.py`.

Two things learned that aren't obvious from the docs:
- **Custom skill upload (`POST /v1/skills?beta=true`, i.e. `client.beta.skills.create()`) returned 404**
  on this account/tier — not enabled for this key, independent of the SDK or request shape (confirmed via
  raw `curl` too). Workaround used instead: inline this skill's `SKILL.md`/`CLAUDE.md` text directly as
  the `system` prompt rather than uploading it as a formal skill object. For a skill that's just
  instructions (no bundled scripts of its own), this achieves the same effect. Retry the upload path later
  if that endpoint becomes available — it's cleaner for versioning the orchestration skill independently
  of any one call site.
- **Generated files are referenced as `file_id`s nested inside the tool-result content**, not at the
  top level of the response: `content[i].type == "bash_code_execution_tool_result"` →
  `content[i].content.content == [{"file_id": "...", "type": "bash_code_execution_output"}]`. Download
  with `client.beta.files.download(file_id, betas=[...])`, then `.write_to_file(path)`.
- Use `client.beta.messages.create()`, not `client.messages.create()` — the plain (non-beta) resource in
  the current SDK doesn't accept `betas`.
- Budget real time for this: a from-scratch docx generation call (read skill → write script → run →
  LibreOffice PDF render → visual verification → export) took roughly 10 minutes end to end with
  `max_tokens=16000`. `max_tokens=4096` was not enough and truncated mid-turn on an earlier attempt.

Given a multi-tenant backend hands arbitrary end-user requests to the model, this sandboxed-container path
remains the right call over the CLI/Agent SDK (real filesystem + Bash access, appropriate for a trusted
single operator, not for arbitrary end-user requests).

## Full pipeline: `backend/` (FastAPI + LangGraph)

This invocation path is now wrapped in a 7-node LangGraph pipeline (Planner → Research → Content → Layout
→ OfficeSkill → Validation → Export), exposed via FastAPI at `POST /generate`. See `backend/README.md` for
the full writeup. The OfficeSkill node there is exactly the pattern verified above, just seeded with a
content plan and layout notes from upstream nodes instead of re-planning from scratch. Only Planner
through Layout are plain (non-beta) Claude calls; OfficeSkill is the one node using this section's
verified betas/container pattern. Validation is deterministic (reuses `mnt/skills/public/*/ooxml/scripts`,
no LLM call). Research and Export have explicitly-marked missing integrations (no real retrieval, no
cloud storage) — see `backend/README.md`'s "What's real vs. explicitly out of scope" section rather than
assuming those are complete.

## Superseded: LLM-plans/fixed-code-generates (TrueFoundry-based)

Built and locally verified (docx and pptx generators both produced valid files from fixed content plans)
during a period when only an OpenAI-compatible gateway key (TrueFoundry) was available, no direct
Anthropic key. Removed once a real Anthropic key made the primary path above testable — kept here only as
a historical note in case direct-LLM-credential-only constraints recur:
- LLM did content planning only (forced tool-use), fixed backend modules did generation per format.
- Blocked from full verification: the available TrueFoundry service account (`qna-agent-um`) returned
  `401: Service account does not exist` — a dead credential, unrelated to the architecture itself.

## Why this exists

`mnt/skills/public/*` already encodes battle-tested, licensed document-generation workflows. Any
platform built on top of this repo gets that for free — but only if every layer above it treats those
skills as an opaque, canonical engine rather than something to be reverse-engineered or partially
reimplemented for convenience.
"""


def call_pdf_tool(content_plan, layout_notes=None, assumptions=None, api_key=None, output_dir="."):
    """Generate a .pdf file via Claude's code-execution + skills API. See module docstring for the return shape."""
    client = anthropic.Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])

    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        betas=BETAS,
        system=[{"type": "text", "text": _PDF_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        container={"skills": [{"type": "anthropic", "skill_id": "pdf", "version": "latest"}]},
        tools=[{"type": "code_execution_20250825", "name": "code_execution"}],
        messages=[{"role": "user", "content": _build_request_message("pdf", content_plan, layout_notes, assumptions)}],
    )

    file_id = _extract_file_id(response)
    if not file_id:
        return {"ok": False, "error": f"no file_id found in response (stop_reason={response.stop_reason})"}

    os.makedirs(output_dir, exist_ok=True)
    dest = os.path.join(output_dir, f"{uuid.uuid4().hex}.pdf")
    client.beta.files.download(file_id, betas=["code-execution-2025-08-25"]).write_to_file(dest)
    return {"ok": True, "file_path": dest, "file_id": file_id}



# ============================================================================
# Generic dispatcher -- pass any prompt to any format, one call.
# ============================================================================

_TOOL_BY_FORMAT = {
    "docx": call_docx_tool,
    "pptx": call_pptx_tool,
    "xlsx": call_xlsx_tool,
    "pdf": call_pdf_tool,
}


def generate(prompt, format, layout_notes=None, assumptions=None, api_key=None, output_dir="."):
    """
    One entry point for "any prompt, any format": plans the content from `prompt`
    (via plan_content) then generates via whichever call_<format>_tool matches
    `format` ("docx" | "pptx" | "xlsx" | "pdf"). You still choose the format --
    nothing here infers it from the prompt text.

    Equivalent to, and no different from, calling the two functions yourself:
        plan = plan_content(prompt, api_key=api_key)
        call_pptx_tool(plan, layout_notes, assumptions, api_key, output_dir)
    """
    if format not in _TOOL_BY_FORMAT:
        return {"ok": False, "error": f"Unknown format {format!r}, expected one of {sorted(_TOOL_BY_FORMAT)}"}

    content_plan = plan_content(prompt, api_key=api_key)
    tool = _TOOL_BY_FORMAT[format]
    return tool(content_plan, layout_notes=layout_notes, assumptions=assumptions, api_key=api_key, output_dir=output_dir)
