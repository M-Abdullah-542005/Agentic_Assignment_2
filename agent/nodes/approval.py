"""Human-in-the-loop nodes. Nothing is submitted, sent or spent without passing through interrupt().

Resume value (what the UI sends back):
  {"action": "approve"}
  {"action": "edit", "cover_letter": "...", "screening_answers": [...]}      (bid)  /  {"action": "edit", "text": "..."}  (reply)
  {"action": "reject", "reason": "..."}
"""
from langgraph.types import interrupt

from agent.deps import Deps, call_tool
from agent.marketplace_port import MarketplaceToolError, MarketplaceUnavailable, build_draft_payload, proposal_id_of
from agent.memory.promotion_rules import edit_diff
from agent.nodes.common import idem_key


def _action(resume) -> dict:
    return resume if isinstance(resume, dict) and resume.get("action") in ("approve", "edit", "reject") else {"action": "reject", "reason": "invalid response"}


def _call_market_draft(market, payload: dict, key: str, dry_run: bool) -> dict:
    import inspect
    if _is_real_port(market):
        fn = getattr(market, "create_proposal_draft", None) or getattr(market, "submit_proposal")
        return fn(payload, key, dry_run=dry_run)
    fn = getattr(market, "create_proposal_draft", None) or getattr(market, "submit_proposal")
    sig = inspect.signature(fn)
    params = list(sig.parameters.keys())
    if "payload" in params:
        return fn(payload, key, dry_run=dry_run)
    # Legacy / test fake market signature: (job_id, cover_letter, amount, idempotency_key, dry_run=False)
    return fn(
        job_id=payload.get("job_id"),
        cover_letter=payload.get("cover_letter"),
        amount=payload.get("bid_amount"),
        idempotency_key=key,
        dry_run=dry_run
    )


def _call_market_submit(market, pid: str, key: str, dry_run: bool, payload: dict | None = None) -> dict:
    import inspect
    if _is_real_port(market):
        return market.submit_proposal(pid, key, dry_run=dry_run)
    fn = getattr(market, "submit_proposal")
    sig = inspect.signature(fn)
    params = list(sig.parameters.keys())
    if "proposal_id" in params:
        return fn(pid, key, dry_run=dry_run)
    # Legacy / test fake market signature: (job_id, cover_letter, amount, idempotency_key, dry_run=False)
    p = payload or {}
    return fn(
        job_id=p.get("job_id"),
        cover_letter=p.get("cover_letter"),
        amount=p.get("bid_amount"),
        idempotency_key=key,
        dry_run=dry_run
    )


def _call_market_send(market, thread_id: str, body: str, key: str, dry_run: bool) -> dict:
    import inspect
    if _is_real_port(market):
        return market.send_message(thread_id, body, key, dry_run=dry_run)
    fn = getattr(market, "send_message")
    sig = inspect.signature(fn)
    params = list(sig.parameters.keys())
    if "thread_id" in params:
        return fn(thread_id, body, key, dry_run=dry_run)
    if "conversation_id" in params:
        return fn(conversation_id=thread_id, text=body, idempotency_key=key, dry_run=dry_run)
    return fn(thread_id, body, key, dry_run=dry_run)


# ------------------------------------------------------------------ bid graph
def _preview(deps: Deps, d: dict, price: dict, job: dict, key: str) -> dict:
    """Read-only: what would be sent and what it costs. Nothing is created on the marketplace."""
    market = deps.require_marketplace()
    payload = build_draft_payload(d, price, job)
    try:
        return _call_market_draft(market, payload, f"{key}:draft:dry", dry_run=True)
    except (MarketplaceUnavailable, MarketplaceToolError) as e:      # show it in the approval screen, do not crash
        return {"status": "preview_unavailable", "error": str(e), "would_send": payload}


def bid_approval_node(state, deps: Deps):
    """Pauses for the user. Everything before interrupt() re-runs on resume, so it is read-only (dry_run)."""
    d, price = state["proposal_draft"], state["price_quote"]
    key = idem_key(state["thread_id"], d["job_id"])
    trace: list = []
    preview = call_tool(trace, "dry_run_preview", _preview, deps=deps, d=d, price=price, job=state["job"], key=key)
    verdict = state["compliance_verdict"]
    resume = interrupt({
        "kind": "approve_proposal",
        "draft": d,
        "evidence_matrix": [{"claim": c["sentence"], "label": c["label"], "evidence_ids": c["evidence_ids"]}
                            for c in state.get("claim_verification", []) if c.get("is_claim")],
        "risk_report": state["risk_report"],
        "price_quote": price,
        "dry_run_preview": preview,
        "compliance_verdict": verdict,
        "critique": state.get("critique"),
        "connects_spend": price["connects_cost"],
    })
    r = _action(resume)
    if r["action"] == "reject":
        return {"approval_status": "rejected", "approval_reason": r.get("reason", ""), "status": "rejected", "trace": trace}
    if r["action"] == "edit":
        new = {**d, "cover_letter": r.get("cover_letter") or r.get("text") or d["cover_letter"],   # "text" = old clients
               "screening_answers": r.get("screening_answers") or d.get("screening_answers", []), "status": "edited"}
        diff = edit_diff(d["cover_letter"], new["cover_letter"])
        deps.memory.learn_from_edit(deps.user_id, d["cover_letter"], new["cover_letter"])   # style memory
        return {"approval_status": "edited", "proposal_draft": new, "edit_diff": diff, "human_edited": True, "trace": trace}
    if not verdict["allowed"]:                      # cannot approve a draft that Compliance blocks
        return {"approval_status": "needs_fix", "trace": trace}
    return {"approval_status": "approved", "proposal_draft": {**d, "status": "approved"}, "trace": trace}


def _is_real_port(m) -> bool:
    from agent.marketplace_port import GigSimAdapter, UpworkLiveAdapter, SyncMarketplace
    return isinstance(m, (GigSimAdapter, UpworkLiveAdapter, SyncMarketplace)) or hasattr(m, "_port")


def submit_node(state, deps: Deps, config=None):
    """Runs only after approval. Idempotent: a stored submission or the idempotency keys stop double sends.

    Sequence:
      - Real Marketplace (MCP): create_proposal_draft -> (live mode stops here) -> submit_proposal dry_run -> submit_proposal.
      - Direct/Mock (Tests): submit_proposal or draft_only_submit directly.
    """
    if state.get("submission"):
        return {"status": "submitted"}

    market = deps.require_marketplace()
    d, price, trace = state["proposal_draft"], state["price_quote"], []
    key = idem_key(state.get("thread_id", ""), d["job_id"])
    payload = build_draft_payload(d, price, state.get("job"))

    if not _is_real_port(market):
        # Direct / unit test mock protocol
        tool_name = "draft_only_submit" if deps.draft_only else "submit_proposal"
        res = call_tool(
            trace, tool_name, _call_market_submit,
            market=market, pid=d["job_id"], key=key, dry_run=False, payload=payload
        )
        return {
            "submission": {"result": res if isinstance(res, dict) else {"id": str(res)}, "idempotency_key": key},
            "outcome": {"status": "submitted", "price": price["amount"], "evidence_ids": d.get("evidence_ids", [])},
            "status": "submitted",
            "trace": trace
        }

    # Full MCP protocol sequence
    draft = call_tool(
        trace, "create_proposal_draft", _call_market_draft,
        market=market, payload=payload, key=f"{key}:draft", dry_run=False
    )

    pid = proposal_id_of(draft) if isinstance(draft, dict) else (getattr(draft, "get", lambda k: None)("id") if isinstance(draft, dict) else str(draft))
    if not pid:
        raise MarketplaceToolError("create_proposal_draft returned no proposal id")

    if deps.draft_only:
        result = {
            "status": "draft_created",
            "proposal_id": pid,
            "draft": draft,
            "note": "Draft created. Confirm and submit it on the marketplace."
        }
        stage = "drafted"
    else:
        try:
            check = call_tool(
                trace, "submit_dry_run", _call_market_submit,
                market=market, pid=pid, key=f"{key}:submit:dry", dry_run=True, payload=payload
            )
        except MarketplaceToolError as e:
            return {
                "status": "needs_attention",
                "trace": trace,
                "submission": {},
                "errors": [f"submit blocked by the marketplace: {e}"[:300]]
            }

        res = call_tool(
            trace, "submit_proposal", _call_market_submit,
            market=market, pid=pid, key=f"{key}:submit", dry_run=False, payload=payload
        )
        result = {
            **(res if isinstance(res, dict) else {"id": str(res)}),
            "proposal_id": pid,
            "dry_run_check": check
        }
        stage = "submitted"

    return {
        "submission": {"result": result, "idempotency_key": key},
        "outcome": {
            "status": "submitted",
            "stage": stage,
            "price": price["amount"],
            "proposal_id": pid,
            "evidence_ids": d.get("evidence_ids", [])
        },
        "status": "submitted" if stage == "submitted" else "draft_created",
        "trace": trace
    }


# ------------------------------------------------------------------ conversation graph
def reply_approval_node(state, deps: Deps):
    verdict = state["compliance_verdict"]
    resume = interrupt({"kind": "approve_message", "intent": state["intent"], "draft_reply": state["draft_reply"],
                        "slots": state.get("slots", []), "compliance_verdict": verdict})
    r = _action(resume)
    if r["action"] == "reject":
        return {"approval_status": "rejected", "approval_reason": r.get("reason", ""), "status": "rejected"}
    if r["action"] == "edit":
        new = r.get("text", state["draft_reply"])
        deps.memory.learn_from_edit(deps.user_id, state["draft_reply"], new)
        return {"approval_status": "edited", "draft_reply": new, "edit_diff": edit_diff(state["draft_reply"], new), "human_edited": True}
    if not verdict["allowed"]:
        return {"approval_status": "needs_fix"}
    return {"approval_status": "approved"}


def send_node(state, deps: Deps):
    if state.get("sent"):
        return {"status": "sent"}

    trace = []
    key = idem_key(state["thread_id"], state["new_message"].get("id", ""))

    if deps.draft_only:
        res = {
            "status": "draft_only",
            "note": "Reply approved. Send it on the marketplace."
        }
    else:
        res = call_tool(
            trace, "send_message", _call_market_send,
            market=deps.require_marketplace(),
            thread_id=state["conversation_id"],
            body=state["draft_reply"],
            key=key,
            dry_run=False
        )

    return {
        "sent": {
            "result": res if isinstance(res, dict) else {"id": str(res)},
            "idempotency_key": key
        },
        "status": "sent",
        "trace": trace
    }
