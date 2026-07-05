"""
OfficeSkill node: the one node that actually generates the file. Reuses the exact
pattern verified in tools/skills-api-test/test_skills_api.py -- inlines the
ai-document-platform orchestration skill's instructions as the system prompt (custom
skill upload returns 404 on this account/tier, see that skill's ENGINEERING_NOTES.md), references
the Anthropic-hosted format skill via the code-execution container, and lets Claude
itself read the skill, write the generation script, run it, and self-verify.

Unlike the standalone test script, this node is seeded with the content plan and
layout notes from upstream nodes instead of re-planning content from scratch -- the
Content/Layout nodes already did that work.
"""
import uuid
from pathlib import Path

from app.client import OFFICE_SKILL_MODEL, get_client
from app.state import DocGenState

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_DIR = REPO_ROOT / "mnt" / "skills" / "private" / "ai-document-platform"
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "output"

BETAS = ["code-execution-2025-08-25", "skills-2025-10-02"]
MAX_TOKENS = 16000  # 4096 was observed to truncate mid-generation; see ENGINEERING_NOTES.md notes


def _system_prompt() -> list:
    text = "\n\n---\n\n".join(
        (SKILL_DIR / name).read_text()
        for name in ("SKILL.md", "ENGINEERING_NOTES.md")
        if (SKILL_DIR / name).exists()
    )
    # Identical on every call (~11KB, well above the 1024-token cache minimum) --
    # cache_control means only the first call in a 5-min window pays full price for
    # this; every call after that reads it from cache at a fraction of the cost.
    # This was found to matter in practice: a single office_skill call was observed
    # at 371K input tokens, and this system prompt is resent in full on every one.
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _build_request_message(state: DocGenState) -> str:
    plan = state["content_plan"]
    layout = state.get("layout_notes", {})
    sections_text = "\n".join(
        f"- [{s['type']}] " + (s.get("text") or "; ".join(s.get("items", [])))
        for s in plan["sections"]
    )
    message = (
        f"Generate a .{state['format']} file for this content plan.\n\n"
        f"Title: {plan['title']}\n\nSections:\n{sections_text}\n"
    )
    if layout:
        message += (
            f"\nStyling guidance -- tone: {layout.get('tone')}; "
            f"palette: {', '.join(layout.get('palette', []))}; "
            f"notes: {layout.get('notes')}\n"
        )
    if state.get("assumptions"):
        message += f"\nNote: some values are placeholders ({'; '.join(state['assumptions'])}) -- keep them visibly labeled as such.\n"
    return message


def office_skill_node(state: DocGenState) -> dict:
    client = get_client()
    response = client.beta.messages.create(
        model=OFFICE_SKILL_MODEL,
        max_tokens=MAX_TOKENS,
        betas=BETAS,
        system=_system_prompt(),
        container={
            "skills": [{"type": "anthropic", "skill_id": state["format"], "version": "latest"}]
        },
        tools=[{"type": "code_execution_20250825", "name": "code_execution"}],
        messages=[{"role": "user", "content": _build_request_message(state)}],
    )

    OUTPUT_DIR.mkdir(exist_ok=True)
    file_id = _extract_file_id(response)
    if not file_id:
        return {"error": f"OfficeSkill: no file_id found in response (stop_reason={response.stop_reason})"}

    dest = OUTPUT_DIR / f"{uuid.uuid4().hex}.{state['format']}"
    client.beta.files.download(file_id, betas=["code-execution-2025-08-25"]).write_to_file(dest)
    return {"file_id": file_id, "file_path": str(dest)}


def _extract_file_id(response):
    # Verified shape (see tools/skills-api-test/ENGINEERING_NOTES.md notes): file_id is nested
    # inside a bash_code_execution_tool_result block, not at the top level.
    for block in response.content:
        if getattr(block, "type", None) != "bash_code_execution_tool_result":
            continue
        result = getattr(block, "content", None)
        for item in getattr(result, "content", None) or []:
            file_id = getattr(item, "file_id", None)
            if file_id:
                return file_id
    return None
