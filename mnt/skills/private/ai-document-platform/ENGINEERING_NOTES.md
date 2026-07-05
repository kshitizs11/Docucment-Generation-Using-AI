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
