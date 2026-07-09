# flat_tool_calls.py — quick start

One file. No folders, no other dependency from this repo. You give it a **prompt**,
optionally a **CSV/Excel file to base it on**, and an **output file path** — it gives
you back a real, generated `.docx`/`.pptx`/`.xlsx`/`.pdf`.

## Setup (do this once)

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...your real key...
```

That's it. No FastAPI, no LangGraph, nothing else — copy `flat_tool_calls.py` anywhere
and it works standalone.

## How to run it

**Have a CSV/Excel file to base the document on?** Use `generate_from_reference`:

```python
import flat_tool_calls as f

result = f.generate_from_reference(
    query="Create a short, simple slide deck summarizing the top 5 highest-cost and top 5 highest-volume models, plus a takeaways slide. Keep it text-only, no tables or charts.",
    input_path="/Users/Kshitiz.Sharma/Desktop/doc_builder/claude-skills-base/metrics (1).csv",
    output_path="/Users/Kshitiz.Sharma/Desktop/doc_builder/claude-skills-base/metrics_summary.pptx",
)
print(result)
# {"ok": True, "file_path": "...", "file_id": "..."}
```

- **`query`** — plain English, describe what you want
- **`input_path`** — a `.csv` (or `.xlsx`/`.xls`) file that already exists; its actual
  data gets folded into the prompt so the document is grounded in real numbers
- **`output_path`** — exactly where to save the result; the file extension
  (`.docx`/`.pptx`/`.xlsx`/`.pdf`) is what decides the format — nothing else does

**No reference file, just a prompt?** Use `generate` instead — same idea, but you pass
`format=` directly instead of an `output_path` extension deciding it:

```python
result = f.generate("a pitch deck for my coffee ordering app", format="pptx", output_dir="./output")
```

That's the whole thing. Real generation takes **~10 minutes** — this isn't instant,
and it isn't hung, it's a real Claude call reading a skill, writing and running
generation code, and checking its own output before returning.

## What's actually happening

```
Your prompt (+ reference file, if you gave one)
        │
        ▼
plan_content()      ← Claude turns it into a structured outline (title + sections)
        │              A few seconds.
        ▼
content_plan (a dict)
        │
        ▼
call_<format>_tool() ← Claude reads that format's skill, writes real generation
        │               code, runs it, checks its own output, exports the file.
        │               ~10 minutes — this is the slow part.
        ▼
{"ok": True, "file_path": "...", "file_id": "..."}
```

The only things you control: **what you type**, **which file (if any) it's grounded
in**, and **where it saves** (which also decides the format, via the extension).
Nothing here guesses the format from your prompt text.

## Quick reference — every function in the file

| Function | Use when | Returns |
|---|---|---|
| `generate_from_reference(query, input_path, output_path)` | You have a CSV/Excel file to base the document on | `{"ok": bool, "file_path": str, ...}` |
| `generate(prompt, format, output_dir=".")` | Just a prompt, no reference file | same |
| `plan_content(prompt)` | You want the content plan only, not a generated file yet | `{"title": str, "sections": [...]}` |
| `call_docx_tool(content_plan, output_dir=".")` | You already have a content plan and want a docx specifically | `{"ok": bool, "file_path": str, ...}` |
| `call_pptx_tool(content_plan, output_dir=".")` | Same, for pptx | same |
| `call_xlsx_tool(content_plan, output_dir=".")` | Same, for xlsx | same |
| `call_pdf_tool(content_plan, output_dir=".")` | Same, for pdf | same |

`generate` and `generate_from_reference` are the two you'll normally use — the
`call_<format>_tool` functions are what they call internally, exposed in case you
want to reuse one content plan across multiple formats or write your own plan by hand.

## Writing your own content plan (optional, advanced)

If you want exact control over the content instead of letting `plan_content` write
it, skip straight to a `call_<format>_tool`:

```python
content_plan = {
    "title": "Q3 Sales Summary",
    "sections": [
        {"type": "paragraph", "text": "Sales grew 12% quarter over quarter."},
        {"type": "heading", "text": "Next Steps"},
        {"type": "bullet_list", "items": ["Expand into EMEA", "Hire 2 more AEs"]},
    ],
}
result = f.call_docx_tool(content_plan, output_dir="./output")
```

Section `type` is `"heading"`, `"paragraph"`, or `"bullet_list"`. `layout_notes` and
`assumptions` are optional extra arguments on every `call_<format>_tool` — see each
function's docstring in the file for details.

## Using this in another application

Copy `flat_tool_calls.py` into that codebase and `import` it — nothing else needs to
come with it. One external dependency (`anthropic`), reads nothing off disk except
whatever file paths you hand it.

## Keep data-heavy reference files simple

A first real attempt at `generate_from_reference` asking for a richer breakdown
(tables/charts across 105 CSV rows) failed with `prompt is too long: 1,016,493 tokens
> 1,000,000 maximum` — not a bug, the underlying generation session itself ran too
long (more rows + tables/charts likely means more write-code → error → retry rounds
inside Claude's sandbox, and each round resends the whole growing conversation).
Asking for a **plain-text, smaller-scope summary** (top 5, no tables/charts) on the
same file succeeded cleanly. If you hit this: simplify the query, or pre-filter your
CSV to fewer rows before passing it in.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `KeyError: 'ANTHROPIC_API_KEY'` | You didn't set the env var or pass `api_key=` |
| `credit balance is too low` | The API key's account needs credits — not a bug, check console.anthropic.com billing |
| `{"ok": False, "error": "no file_id found..."}` | The model didn't produce a file this run — check `response.stop_reason` in the error message; try again |
| `RuntimeError: plan_content: incomplete tool_use input...` | Got cut off by `max_tokens` (8192) — unusual unless you're passing a huge reference file; shorten it |
| `prompt is too long: N tokens > 1,000,000 maximum` | The generation session ran too long — see "Keep data-heavy reference files simple" above |
| Takes way longer than 10 minutes | Rare, but possible for a complex request — not hung, just slow; not usually worth killing before ~20 min |
