# flat_tool_calls.py — quick start

One file, no folders: `call_docx_tool`, `call_pptx_tool`, `call_xlsx_tool`,
`call_pdf_tool` (each takes a content plan, returns a generated file),
`plan_content` (turns a plain sentence into a content plan), and `generate` (does
both steps in one call — any prompt, any format). No dependency on the rest of this
repo — copy this one file anywhere and it works standalone.

## The simplest way to use it

```python
import flat_tool_calls as f

result = f.generate("a pitch deck for my coffee ordering app", format="pptx", output_dir="./output")
print(result)
# {"ok": True, "file_path": "./output/<uuid>.pptx", "file_id": "..."}
```

Any prompt, any format — `format` is `"docx"`, `"pptx"`, `"xlsx"`, or `"pdf"`, your
choice, passed as a plain argument. Verified: tested all 4 format strings route to
the correct skill internally (by intercepting the actual request sent to Claude, not
just trusting the code). Nothing here guesses the format from your prompt text —
that decision is always yours, made by whatever you pass as `format=`.

The rest of this guide covers the two steps `generate()` does internally, for when
you want more control (e.g. reusing one content plan across multiple formats, or
editing the plan before generating).

## The flow, in plain terms

What actually happens when you call `f.generate("your prompt", format="pptx")`:

```
Your prompt (plain English)
        │
        ▼
plan_content(prompt)          ← Claude reads your prompt, writes a
        │                        structured outline (title + sections)
        │                        Takes a few seconds.
        ▼
content_plan (a dict)
        │
        ▼
call_pptx_tool(content_plan)  ← Claude reads the pptx skill, writes real
        │                        generation code, runs it, checks its own
        │                        output, exports the file.
        │                        Takes ~10 minutes.
        ▼
{"ok": True, "file_path": "...", "file_id": "..."}
```

The only two things you control: **what you type** (any sentence describing the
document) and **`format=`** (`"docx"` / `"pptx"` / `"xlsx"` / `"pdf"`, your choice —
nothing guesses it for you). Everything in between — planning the content, picking
which skill to use, writing the generation code, checking the result — happens
inside that one `generate()` call.

## Step 1 — Install the one dependency

```bash
pip install anthropic
```

That's it. No FastAPI, no LangGraph, nothing else from this repo required.

## Step 2 — Set your API key

```bash
export ANTHROPIC_API_KEY=sk-ant-...your real key...
```

(Or skip this and pass `api_key="sk-ant-..."` directly to any function call instead.)

## Step 3 — Get a content plan

Two ways to get the `content_plan` these functions need:

**Option A — just type a sentence** (uses `plan_content`, a separate cheap/fast call —
seconds, not minutes):

```python
import flat_tool_calls as f

content_plan = f.plan_content("a pitch deck for my coffee ordering app called BrewFlow")
```

Verified live — one sentence in, a fully worked-out multi-section outline out (title,
problem, solution, market, ask, etc., written by Claude, not placeholders).

**Option B — write it yourself** if you want exact control over what's in it:

```python
content_plan = {
    "title": "Q3 Sales Summary",
    "sections": [
        {"type": "paragraph", "text": "Sales grew 12% quarter over quarter."},
        {"type": "heading", "text": "Next Steps"},
        {"type": "bullet_list", "items": ["Expand into EMEA", "Hire 2 more AEs"]},
    ],
}
```

## Step 4 — Call a function

```python
result = f.call_pptx_tool(content_plan, output_dir="./output")
print(result)
# {"ok": True, "file_path": "./output/<uuid>.pptx", "file_id": "..."}
```

Swap `call_pptx_tool` for `call_docx_tool`, `call_xlsx_tool`, or `call_pdf_tool` for
the other formats — same input shape, same return shape, every time. **You** choose
which one to call; nothing in this file guesses the format for you.

**Budget real time**: each call takes roughly 10 minutes (it's a real Claude call
that reads a skill, writes and runs generation code, and self-verifies before
returning) — this is not instant.

## What a `content_plan` looks like

```python
{
    "title": "Document Title",
    "sections": [
        {"type": "heading", "text": "Section Heading"},
        {"type": "paragraph", "text": "A paragraph of text."},
        {"type": "bullet_list", "items": ["First point", "Second point"]},
    ],
}
```

`layout_notes` and `assumptions` are optional extra arguments — see the function
signature (`content_plan, layout_notes=None, assumptions=None, api_key=None,
output_dir="."`).

## Using this in another application

Just copy `flat_tool_calls.py` into that codebase and `import` it — nothing else
needs to come with it. It has exactly one external dependency (`anthropic`) and
reads nothing off disk except what it's handed as arguments.

If the other application already has its own way of turning a user request into a
`content_plan` (its own planning/LLM step), point that output at these functions
directly. Otherwise, use `plan_content()` (Step 3, Option A above) — it's part of
this same file, so nothing extra to bring along either way.

Full end-to-end, one prompt to a downloaded file:
```python
import flat_tool_calls as f
result = f.generate("a one-page project status report with 3 next steps", format="docx", output_dir="./output")
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| `KeyError: 'ANTHROPIC_API_KEY'` | You didn't set the env var or pass `api_key=` |
| `credit balance is too low` | The API key's account needs credits — not a bug, check console.anthropic.com billing |
| `{"ok": False, "error": "no file_id found..."}` | The model didn't produce a file this run — check `response.stop_reason` in the error message; try again |
| Takes way longer than 10 minutes | Rare, but possible for a complex request — this isn't hung, just slow; not typically worth killing before ~20 min |
