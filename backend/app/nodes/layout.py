"""
Layout node: proposes tone/color styling hints from the content plan. These are
instructions handed to the OfficeSkill node's prompt -- they are NOT applied here;
this node never touches OOXML/file bytes.
"""
from app.client import PLANNING_MODEL, get_client
from app.state import DocGenState

LAYOUT_TOOL = {
    "name": "submit_layout_notes",
    "description": "Styling guidance for the document to be generated.",
    "input_schema": {
        "type": "object",
        "properties": {
            "tone": {
                "type": "string",
                "description": "One or two words describing the visual/writing tone, e.g. 'formal corporate'.",
            },
            "palette": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-4 hex color codes (e.g. '#1C2833') that fit the content and tone.",
            },
            "notes": {
                "type": "string",
                "description": "Any other brief styling guidance (fonts, emphasis, layout preferences).",
            },
        },
        "required": ["tone", "palette", "notes"],
    },
}

SYSTEM_PROMPT = (
    "You are the styling stage of a document-generation pipeline. Given a content "
    "plan, propose a tone and a small color palette that fit the subject matter -- "
    "call submit_layout_notes. Do not write or revise content; that stage is already "
    "done."
)


def layout_node(state: DocGenState) -> dict:
    client = get_client()
    plan = state["content_plan"]
    summary = plan["title"] + "\n" + "\n".join(
        s.get("text", "") or ", ".join(s.get("items", [])) for s in plan["sections"]
    )

    response = client.messages.create(
        model=PLANNING_MODEL,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        tools=[LAYOUT_TOOL],
        tool_choice={"type": "tool", "name": "submit_layout_notes"},
        messages=[{"role": "user", "content": f"Content plan:\n{summary}"}],
    )
    tool_use = next(b for b in response.content if b.type == "tool_use")
    return {
        "layout_notes": {
            "tone": tool_use.input["tone"],
            "palette": tool_use.input["palette"],
            "notes": tool_use.input["notes"],
        }
    }
