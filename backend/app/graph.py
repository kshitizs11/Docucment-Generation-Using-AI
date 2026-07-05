"""
The 7-node pipeline: Planner -> Research -> Content -> Layout -> OfficeSkill ->
Validation -> Export. Linear for now -- no retry/branching logic. Each node function
returns only the state keys it's responsible for; LangGraph merges them in.
"""
from langgraph.graph import END, StateGraph

from app.nodes.content import content_node
from app.nodes.export import export_node
from app.nodes.layout import layout_node
from app.nodes.office_skill import office_skill_node
from app.nodes.planner import planner_node
from app.nodes.research import research_node
from app.nodes.validation import validation_node
from app.state import DocGenState


def build_graph():
    graph = StateGraph(DocGenState)
    graph.add_node("planner", planner_node)
    graph.add_node("research", research_node)
    graph.add_node("content", content_node)
    graph.add_node("layout", layout_node)
    graph.add_node("office_skill", office_skill_node)
    graph.add_node("validation", validation_node)
    graph.add_node("export", export_node)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "research")
    graph.add_edge("research", "content")
    graph.add_edge("content", "layout")
    graph.add_edge("layout", "office_skill")
    graph.add_edge("office_skill", "validation")
    graph.add_edge("validation", "export")
    graph.add_edge("export", END)

    return graph.compile()


doc_gen_graph = build_graph()
