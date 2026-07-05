#!/usr/bin/env python3
"""
Verifies that the `ai-document-platform` orchestration skill's instructions compose
with an Anthropic-hosted document skill (docx) inside the Messages API's
code-execution container.

Pivoted from custom-skill-upload (client.beta.skills.create()) to inlining the skill's
SKILL.md/ENGINEERING_NOTES.md as the system prompt: a live test against this account found
POST /v1/skills?beta=true returns 404 (custom skill upload not enabled for this
account/tier), while container.skills with a hosted skill_id (e.g. "docx") works fine.
Inlining achieves the same effect for a skill that's really just instructions, without
needing that endpoint. Re-attempt custom upload later if that endpoint becomes available
-- it's cleaner for versioning a skill independently of any one prompt.

Usage:
    pip install -r requirements.txt
    cp .env.example .env   # then paste a real ANTHROPIC_API_KEY into .env
    python test_skills_api.py
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import anthropic

# override=True: this sandbox has an ambient placeholder ANTHROPIC_API_KEY set globally;
# without override, load_dotenv() would silently keep that instead of our real key.
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "mnt" / "skills" / "private" / "ai-document-platform"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

BETAS = ["code-execution-2025-08-25", "skills-2025-10-02"]
MODEL = os.environ.get("ANTHROPIC_TEST_MODEL", "claude-sonnet-4-6")

# Same request used in the earlier Claude Code subagent test, so results are
# directly comparable.
REQUEST = (
    "Generate a short one-page project status report as a .docx with: a title, "
    "a 2-3 sentence summary paragraph, and a bulleted list of 3 next steps."
)


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or "REPLACE_ME" in api_key:
        sys.exit("Set a real ANTHROPIC_API_KEY in tools/skills-api-test/.env before running this.")

    if not SKILL_DIR.exists():
        sys.exit(f"Custom skill directory not found: {SKILL_DIR}")

    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = "\n\n---\n\n".join(
        (SKILL_DIR / name).read_text()
        for name in ("SKILL.md", "ENGINEERING_NOTES.md")
        if (SKILL_DIR / name).exists()
    )

    print("Calling messages.create with hosted docx skill + inlined orchestration instructions ...")
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=BETAS,
        system=system_prompt,
        container={
            "skills": [
                {"type": "anthropic", "skill_id": "docx", "version": "latest"},
            ]
        },
        tools=[{"type": "code_execution_20250825", "name": "code_execution"}],
        messages=[{"role": "user", "content": REQUEST}],
    )

    OUTPUT_DIR.mkdir(exist_ok=True)
    dumped = response.model_dump_json(indent=2)
    raw_path = OUTPUT_DIR / "raw_response.json"
    raw_path.write_text(dumped)
    print(f"\nFull raw response written to {raw_path} ({len(dumped)} bytes)")
    print(f"stop_reason: {response.stop_reason}")
    print("--- first 4000 chars (inspect the full file above if file extraction below finds nothing) ---")
    print(dumped[:4000], "...(truncated)" if len(dumped) > 4000 else "")
    print("---\n")

    saved = extract_and_save_files(client, response, OUTPUT_DIR)

    if saved:
        for path in saved:
            print(f"SUCCESS: file saved to {path} ({path.stat().st_size} bytes)")
    else:
        print(
            "No file auto-extracted. This likely means the beta response shape differs "
            "from what extract_and_save_files() expects — check the raw response dump "
            "above for how the generated file is referenced (file_id, container path, "
            "etc.) and adjust that function."
        )


def extract_and_save_files(client, response, output_dir):
    """
    Files produced by a bash tool call inside the code-execution container show up as
    file_id references nested two levels deep:
      content[i].type == "bash_code_execution_tool_result"
      content[i].content.content == [{"file_id": "...", "type": "bash_code_execution_output"}, ...]
    (verified against a live response — see output/raw_response.json).
    """
    saved = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) != "bash_code_execution_tool_result":
            continue
        result = getattr(block, "content", None)
        items = getattr(result, "content", None) or []
        for item in items:
            file_id = getattr(item, "file_id", None)
            if file_id:
                path = _download_file(client, file_id, output_dir)
                if path:
                    saved.append(path)
    return saved


def _download_file(client, file_id, output_dir):
    response = client.beta.files.download(file_id)
    dest = output_dir / f"{file_id}.docx"
    response.write_to_file(dest)
    return dest


if __name__ == "__main__":
    main()
