"""
Research node: flags claims/data points in the outline that would benefit from
external lookup.

SCOPE NOTE: no external retrieval (web search, internal RAG, databases) is wired up
here -- that integration doesn't exist yet. This node is honest about that: it does
NOT fabricate facts or pretend to have looked anything up. It identifies what a real
research step would need to fetch, and returns those as explicit "assumptions" the
Content node states plainly (e.g. "assumed 68% completion for illustration") rather
than presenting invented numbers as if verified. Wire a real retrieval call in here
when a data source is available; the Content node already expects this shape.
"""
from app.client import PLANNING_MODEL, get_client
from app.state import DocGenState

RESEARCH_TOOL = {
    "name": "submit_research_notes",
    "description": "Identify facts/data the outline references that would need external verification.",
    "input_schema": {
        "type": "object",
        "properties": {
            "research_notes": {
                "type": "string",
                "description": "Plain-text summary of what would need to be looked up, or empty if none.",
            },
            "assumptions": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Specific placeholder values/claims the Content node should use, "
                    "clearly stated as assumptions rather than verified facts."
                ),
            },
        },
        "required": ["research_notes", "assumptions"],
    },
}

SYSTEM_PROMPT = (
    "You are the research-scoping stage of a document-generation pipeline. You have "
    "NO access to external data sources, search, or databases -- you cannot look "
    "anything up. Given an outline, identify any specific facts/figures/data it would "
    "need for accuracy, and propose clearly-labeled placeholder assumptions for them "
    "so the document can still be drafted. Do not present a guess as a verified fact."
)


def research_node(state: DocGenState) -> dict:
    if not state.get("needs_research"):
        return {"research_notes": "", "assumptions": []}

    client = get_client()
    response = client.messages.create(
        model=PLANNING_MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[RESEARCH_TOOL],
        tool_choice={"type": "tool", "name": "submit_research_notes"},
        messages=[{"role": "user", "content": f"Outline:\n{state['outline']}"}],
    )
    tool_use = next(b for b in response.content if b.type == "tool_use")
    return {
        "research_notes": tool_use.input["research_notes"],
        "assumptions": tool_use.input["assumptions"],
    }
