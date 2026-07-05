---
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
