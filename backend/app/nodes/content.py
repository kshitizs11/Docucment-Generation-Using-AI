"""
Content node: produces the structured, format-agnostic content plan
({title, sections: [heading|paragraph|bullet_list]}) that the OfficeSkill node will
hand to the actual document-generation skill. This is the same schema validated in
earlier testing -- it stays format-agnostic; layout/styling is a separate node.
"""
from app.client import PLANNING_MODEL, get_client
from app.state import DocGenState

CONTENT_TOOL = {
    "name": "submit_content_plan",
    "description": "Structured content plan for the document to be generated.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["heading", "paragraph", "bullet_list"],
                        },
                        "text": {"type": "string"},
                        "items": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["type"],
                },
            },
        },
        "required": ["title", "sections"],
    },
}

SYSTEM_PROMPT = (
    "You are the content-drafting stage of a document-generation pipeline. Given an "
    "outline, research notes, and explicitly-labeled assumptions, write the document's "
    "actual content by calling submit_content_plan: a title and an ordered list of "
    "sections (heading / paragraph / bullet_list). Write real content, not "
    "placeholders. If assumptions were provided, state them plainly in the text (e.g. "
    "'assuming X') rather than presenting them as verified. Do not decide fonts, "
    "colors, or layout -- that is handled downstream."
)


def content_node(state: DocGenState) -> dict:
    client = get_client()
    user_message = f"Outline:\n{state['outline']}"
    if state.get("research_notes"):
        user_message += f"\n\nResearch notes:\n{state['research_notes']}"
    if state.get("assumptions"):
        assumptions = "\n".join(f"- {a}" for a in state["assumptions"])
        user_message += f"\n\nAssumptions to use (state these plainly, don't present as fact):\n{assumptions}"

    response = client.messages.create(
        model=PLANNING_MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        tools=[CONTENT_TOOL],
        tool_choice={"type": "tool", "name": "submit_content_plan"},
        messages=[{"role": "user", "content": user_message}],
    )
    tool_use = next(b for b in response.content if b.type == "tool_use")
    return {
        "content_plan": {
            "title": tool_use.input["title"],
            "sections": tool_use.input["sections"],
        }
    }
