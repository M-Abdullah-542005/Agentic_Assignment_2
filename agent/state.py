"""Typed graph state.

Three things are kept apart on purpose:
  * STATE      - what this job / conversation needs right now (the fields below).
  * TRACE      - `trace`: every tool call (name, arguments, result, observation, latency, trace_id), for audit
                 and debugging only. Nothing reads it to make decisions and it never goes into memory.
  * MEMORY     - long-term knowledge lives in a LangGraph store (agent/memory), written only by the explicit
                 rules in agent/memory/promotion_rules.py.

Reducers: `steps`, `tokens`, `trace`, `errors` are written by parallel branches, so they use operator.add.
`status` uses a small reducer so that a terminal status from one branch is never overwritten by an empty one.
"""
import operator
from typing import Annotated, Any, TypedDict

TERMINAL = {"skipped", "watching", "blocked", "rejected", "submitted", "draft_created", "needs_attention",
            "dead_letter", "scam_signal", "sent", "no_jobs"}


def keep_status(old: str | None, new: str | None) -> str | None:
    return new or old


class GigBidState(TypedDict, total=False):
    # ---- active state
    query: str                         # optional search text for the Scout
    job: dict[str, Any]
    job_spec: dict[str, Any]
    risk_report: dict[str, Any]
    fit_report: dict[str, Any]
    evidence: list[dict[str, Any]]
    decision: str                      # skip | watch | bid
    bid_decision: dict[str, Any]
    price_quote: dict[str, Any]
    proposal_draft: dict[str, Any]
    claim_verification: list[dict[str, Any]]
    verification_stats: dict[str, Any]
    critique: dict[str, Any]
    compliance_verdict: dict[str, Any]
    approval_status: str               # pending | approved | edited | needs_fix | rejected
    approval_reason: str
    edit_diff: str
    human_edited: bool
    submission: dict[str, Any]
    outcome: dict[str, Any]
    revisions: int                     # times the Critic sent the draft back (max 2)
    status: Annotated[str, keep_status]
    # ---- execution trace + counters (reducers)
    trace: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[str], operator.add]
    steps: Annotated[int, operator.add]
    tokens: Annotated[int, operator.add]
    trace_id: str


class ConversationState(TypedDict, total=False):
    conversation_id: str
    job_id: str
    messages: list[dict[str, Any]]     # history, newest last
    new_message: dict[str, Any]        # {"id", "text"}
    quote: dict[str, Any]              # optional current PriceQuote for negotiation replies
    intent: str                        # question | negotiation | interview_request | scam_signal
    slots: list[str]
    draft_reply: str
    compliance_verdict: dict[str, Any]
    approval_status: str
    approval_reason: str
    edit_diff: str
    human_edited: bool
    sent: dict[str, Any]
    status: Annotated[str, keep_status]
    trace: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[str], operator.add]
    steps: Annotated[int, operator.add]
    tokens: Annotated[int, operator.add]
    trace_id: str
