"""Single place that constructs the Anthropic client and knows which model each
node tier uses. Cheap planning-stage nodes use the plain (non-beta) Messages API;
the OfficeSkill node needs the beta client for container/code-execution/skills."""
import os
from pathlib import Path

from dotenv import load_dotenv
import anthropic

# override=True: some environments have an ambient placeholder ANTHROPIC_API_KEY set
# globally, which would otherwise silently shadow the real value in .env.
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)

PLANNING_MODEL = os.environ.get("ANTHROPIC_PLANNING_MODEL", "claude-sonnet-4-6")
OFFICE_SKILL_MODEL = os.environ.get("ANTHROPIC_OFFICE_SKILL_MODEL", "claude-sonnet-4-6")


def get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or "REPLACE_ME" in api_key:
        raise RuntimeError("Set a real ANTHROPIC_API_KEY in backend/.env before running the pipeline.")
    return anthropic.Anthropic(api_key=api_key)
