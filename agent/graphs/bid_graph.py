"""Bid graph (one thread per job).

scout -> fan_out (Send x3) -> [analyst | risk | fit] -> decide -> skip/watch END
      -> pricing -> writer -> verifier -> critic -(send back, max 2)-> writer
      -> compliance -(blocked)-> END ; allowed -> approval (interrupt) -> submit -> END
approval: approve -> submit | edit -> compliance (re-check) -> approval | reject -> END
"""
from contextlib import contextmanager

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from agent.deps import Deps
from agent.nodes.analyst import analyst_node
from agent.nodes.approval import bid_approval_node, submit_node
from agent.nodes.common import guarded
from agent.nodes.compliance import compliance_node
from agent.nodes.critic import critic_node
from agent.nodes.decision import decision_node
from agent.nodes.fit_matcher import fit_node
from agent.nodes.pricing import pricing_node
from agent.nodes.risk_assessor import risk_node
from agent.nodes.scout import scout_node
from agent.nodes.verifier import verifier_node
from agent.nodes.writer import writer_node
from agent.state import TERMINAL, GigBidState


def _done(state) -> bool:
    return state.get("status") in TERMINAL


def fan_out(state):
    """Parallel branches with Send: the three analysts run in the same superstep."""
    if _done(state):
        return END
    return [Send("analyst", state), Send("risk", state), Send("fit", state)]


def after_decide(state):
    return END if _done(state) or state.get("decision") != "bid" else "pricing"


def after_critic(state):
    if _done(state):
        return END
    return "writer" if state["critique"]["send_back"] else "compliance"


def after_compliance(state):
    if _done(state):
        return END
    return "approval"


def after_approval(state):
    if _done(state):
        return END
    return {"approved": "submit", "edited": "compliance", "needs_fix": "approval"}.get(state.get("approval_status"), END)


def build_bid_graph(deps: Deps | None = None, checkpointer=None):
    deps = deps or Deps()
    g = StateGraph(GigBidState)
    for name, fn in [("scout", scout_node), ("analyst", analyst_node), ("risk", risk_node), ("fit", fit_node),
                     ("decide", decision_node), ("pricing", pricing_node), ("writer", writer_node),
                     ("verifier", verifier_node), ("critic", critic_node), ("compliance", compliance_node),
                     ("submit", submit_node)]:
        g.add_node(name, guarded(name, fn, deps))
    g.add_node("approval", guarded("approval", bid_approval_node, deps, human=True))

    g.add_edge(START, "scout")
    g.add_conditional_edges("scout", fan_out, ["analyst", "risk", "fit", END])
    g.add_edge(["analyst", "risk", "fit"], "decide")            # join: waits for all three
    g.add_conditional_edges("decide", after_decide, ["pricing", END])
    g.add_edge("pricing", "writer")
    g.add_edge("writer", "verifier")
    g.add_edge("verifier", "critic")
    g.add_conditional_edges("critic", after_critic, ["writer", "compliance", END])
    g.add_conditional_edges("compliance", after_compliance, ["approval", END])
    g.add_conditional_edges("approval", after_approval, ["submit", "compliance", "approval", END])
    g.add_edge("submit", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())


@contextmanager
def postgres_checkpointer(url: str):
    """Durable checkpointer: `with postgres_checkpointer(url) as cp: graph = build_bid_graph(deps, cp)`."""
    from langgraph.checkpoint.postgres import PostgresSaver
    with PostgresSaver.from_conn_string(url) as saver:
        saver.setup()
        yield saver
