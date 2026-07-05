# Skills API test harness

Verifies that the `ai-document-platform` orchestration skill's instructions
(`mnt/skills/private/ai-document-platform/`) compose correctly with an Anthropic-hosted
document skill (`docx`) inside a real Messages API `code-execution` container call.

**Status: verified live.** See the "Production invocation path" section in
`mnt/skills/private/ai-document-platform/ENGINEERING_NOTES.md` for the full writeup of what was
learned. Short version: it works, custom skill upload (`client.beta.skills.create()`)
does not on this account (404 — not enabled for this tier), so the script inlines the
skill's `SKILL.md`/`ENGINEERING_NOTES.md` as the `system` prompt instead of uploading it.

## Setup

```bash
cd tools/skills-api-test
pip install -r requirements.txt
cp .env.example .env
# edit .env, paste in a real ANTHROPIC_API_KEY (must be a direct Anthropic key —
# a TrueFoundry/Bedrock/Azure-proxy key will not work; see .env.example for why)
```

## Run

```bash
python test_skills_api.py
```

Takes several minutes for a from-scratch docx generation (the model reads the skill,
writes and runs a docx-js script, renders to PDF via LibreOffice, visually inspects a
page image, then exports) — this isn't a quick call, budget for it.

## What it does

1. Reads `mnt/skills/private/ai-document-platform/SKILL.md` + `ENGINEERING_NOTES.md` and inlines
   them as the `system` prompt (not uploaded as a custom skill — see above).
2. Calls `client.beta.messages.create()` with `container.skills` set to the hosted
   `docx` skill, using the `code_execution` tool.
3. Requests the same one-page status-report `.docx` used throughout this project's
   testing, so results are directly comparable across runs.
4. Writes the full raw response to `output/raw_response.json`, then extracts the
   generated file's `file_id` from the `bash_code_execution_tool_result` content and
   downloads it via `client.beta.files.download()`.

## If it fails

- **Auth/401**: confirm the key in `.env` is a real key from
  console.anthropic.com/settings/keys, not a gateway/proxy key. Also check this
  sandbox/shell doesn't have an ambient `ANTHROPIC_API_KEY` set that would shadow
  `.env` — `load_dotenv(..., override=True)` is already set for this reason, but it's
  worth knowing why if auth errors look wrong despite a correct `.env`.
- **404 on `client.beta.skills.create()`**: expected on accounts without custom-skill
  upload enabled — this script no longer uses that call, but if you reintroduce it,
  this is why it fails.
- **"No file auto-extracted"**: `output/raw_response.json` has the full response —
  `extract_and_save_files()` in `test_skills_api.py` matches the shape observed in a
  real response, but the beta is new enough that Anthropic could change it.
