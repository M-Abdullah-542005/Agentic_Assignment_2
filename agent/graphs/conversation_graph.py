"""Conversation graph (one thread per client conversation).

classify -(scam_signal)-> END ; else draft -> compliance -(blocked)-> END ; allowed -> approval (interrupt) -> send -> END
approval: approve -> send | edit -> compliance -> approval | reject -> END
"""
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agent.deps import Deps
from agent.nodes.approval import reply_approval_node, send_node
from agent.nodes.client_liaison import classify_node, draft_node, reply_compliance_node
from agent.nodes.common import guarded
from agent.state import TERMINAL, ConversationState


def _done(state) -> bool:
    return state.get("status") in TERMINAL


def after_classify(state):
    return END if _done(state) else "draft"


def after_compliance(state):
    return END if _done(state) else "approval"


def after_approval(state):
    if _done(state):
        return END
    return {"approved": "send", "edited": "compliance", "needs_fix": "approval"}.get(state.get("approval_status"), END)


def build_conversation_graph(deps: Deps | None = None, checkpointer=None):
    deps = deps or Deps()
    g = StateGraph(ConversationState)
    g.add_node("classify", guarded("classify", classify_node, deps))
    g.add_node("draft", guarded("draft", draft_node, deps))
    g.add_node("compliance", guarded("compliance", reply_compliance_node, deps))
    g.add_node("approval", guarded("approval", reply_approval_node, deps, human=True))
    g.add_node("send", guarded("send", send_node, deps))
    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", after_classify, ["draft", END])
    g.add_edge("draft", "compliance")
    g.add_conditional_edges("compliance", after_compliance, ["approval", END])
    g.add_conditional_edges("approval", after_approval, ["send", "compliance", "approval", END])
    g.add_edge("send", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())
