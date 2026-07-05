"""
Planner node: classifies the target document format and produces a short outline.
Plain Messages API call (no code execution) -- this is real LLM reasoning, not the
document-generation step.
"""
from app.client import PLANNING_MODEL, get_client
from app.state import DocGenState

PLAN_TOOL = {
    "name": "submit_plan",
    "description": "Classify the target document format and produce a short outline.",
    "input_schema": {
        "type": "object",
        "properties": {
            "format": {
                "type": "string",
                "enum": ["docx", "pptx", "pdf", "xlsx"],
                "description": "The Office format that best fits the request.",
            },
            "outline": {
                "type": "string",
                "description": "A short bullet outline (plain text) of what the document should cover.",
            },
            "needs_research": {
                "type": "boolean",
                "description": (
                    "True if the request references specific facts, figures, or data "
                    "that would need external lookup/verification to state accurately."
                ),
            },
        },
        "required": ["format", "outline", "needs_research"],
    },
}

SYSTEM_PROMPT = (
    "You are the planning stage of a document-generation pipeline. Given a user's "
    "request, decide which Office format it should become and sketch a short outline. "
    "Do not write the document's actual content yet -- that happens in a later step. "
    "If the request is genuinely ambiguous about format, pick the most likely one and "
    "note the ambiguity in the outline rather than refusing to answer."
)


def planner_node(state: DocGenState) -> dict:
    client = get_client()
    response = client.messages.create(
        model=PLANNING_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[PLAN_TOOL],
        tool_choice={"type": "tool", "name": "submit_plan"},
        messages=[{"role": "user", "content": state["request"]}],
    )
    tool_use = next(b for b in response.content if b.type == "tool_use")
    return {
        "format": tool_use.input["format"],
        "outline": tool_use.input["outline"],
        "needs_research": tool_use.input["needs_research"],
    }
