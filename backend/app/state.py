"""Shared state passed between LangGraph nodes. Every field is optional (total=False)
since each node only fills in what it's responsible for."""
from typing import TypedDict


class DocGenState(TypedDict, total=False):
    request: str
    format: str  # docx | pptx | pdf | xlsx
    outline: str
    needs_research: bool
    research_notes: str
    assumptions: list
    content_plan: dict  # {"title": str, "sections": [...]}
    layout_notes: dict  # {"tone": str, "palette": [str]}
    file_id: str
    file_path: str
    validation: dict  # {"ok": bool, "details": str}
    download_path: str
    error: str
