"""LangGraph definitions: wires the nodes into the JobSherpa pipeline."""

from langgraph.graph import END, START, StateGraph

from agent.nodes import stubs
from agent.state import AgentState

def route_after_filter(state: AgentState) -> str:
    """No new jobs today: skip score, apply and email; go straight to the drip.
    
    Checks the actuallist, not the counter, so the two can never disagree."""
    return "score" if state.get("new_jobs") else "hr_drip"

def build_graph():
    """Build and compile the pipline. Returns a runnable compiled graph."""
    graph = StateGraph(AgentState)

    graph.add_node("load_memory", stubs.load_memory)
    graph.add_node("scrape", stubs.scrape_jobs)
    graph.add_node("filter", stubs.filter_jobs)
    graph.add_node("score", stubs.score_jobs)
    graph.add_node("apply", stubs.apply_jobs)
    graph.add_node("email", stubs.send_emails)
    graph.add_node("hr_drip", stubs.hr_drip_campaign)
    graph.add_node("save_memory", stubs.save_memory)
    graph.add_node("report", stubs.build_report)

    graph.add_edge(START, "load_memory")
    graph.add_edge("load_memory", "scrape")
    graph.add_edge("scrape", "filter")
    graph.add_conditional_edges(
        "filter",
        route_after_filter,
        {'score': "score", 'hr_drip': "hr_drip"},
    )
    graph.add_edge("score", 'apply')
    graph.add_edge('apply', 'email')
    graph.add_edge('email', 'hr_drip')
    graph.add_edge('hr_drip', 'save_memory')
    graph.add_edge('save_memory', "report")
    graph.add_edge("report", END)

    return graph.compile()