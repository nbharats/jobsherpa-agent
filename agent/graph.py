"""LangGraph definition: wires the nodes into the JobSherpa pipeline."""
from langgraph.graph import END, START, StateGraph

from agent.nodes import apply_node, email_node, filter_node, memory_node, scrape_node, score_node, stubs
from agent.state import AgentState


def route_after_filter(state: AgentState) -> str:
    """No new jobs today: skip score, apply and email; go straight to the drip.

    Checks the actual list, not the counter, so the two can never disagree.
    """
    return "score" if state.get("new_jobs") else "hr_drip"


def build_graph():
    """Build and compile the pipeline. Returns a runnable compiled graph."""
    graph = StateGraph(AgentState)

    graph.add_node("load_memory", memory_node.load_memory)
    graph.add_node("scrape", scrape_node.scrape_jobs)
    graph.add_node("filter", filter_node.filter_jobs)
    graph.add_node("score", score_node.score_jobs)
    graph.add_node("apply", apply_node.apply_jobs)
    graph.add_node("email", email_node.send_emails)
    graph.add_node("hr_drip", email_node.hr_drip_campaign)
    graph.add_node("save_memory", memory_node.save_memory)
    graph.add_node("report", stubs.build_report)

    graph.add_edge(START, "load_memory")
    graph.add_edge("load_memory", "scrape")
    graph.add_edge("scrape", "filter")
    graph.add_conditional_edges(
        "filter",
        route_after_filter,
        {"score": "score", "hr_drip": "hr_drip"},
    )
    graph.add_edge("score", "apply")
    graph.add_edge("apply", "email")
    graph.add_edge("email", "hr_drip")
    graph.add_edge("hr_drip", "save_memory")
    graph.add_edge("save_memory", "report")
    graph.add_edge("report", END)

    return graph.compile()