"""Verifier: runs rag/verify.py on the cover letter and every screening answer; keeps only supported text.

Rule-based on purpose (rag/verify.py measures grounding with retrieval evidence), so it uses no prompt and no model.
"""
from agent.deps import Deps
from agent.handoffs import ProposalDraft


def verifier_node(state, deps: Deps):
    draft, evidence = ProposalDraft(**state["proposal_draft"]), state.get("evidence", [])
    rep = deps.do_verify(draft.cover_letter, evidence)
    claims = [c.to_dict() if hasattr(c, "to_dict") else c for c in rep.claims]
    answers, before, after, n_before, n_after = [], rep.unsupported_before, rep.unsupported_after, rep.n_claims_before, rep.n_claims_after
    for a in draft.screening_answers:
        r = deps.do_verify(a, evidence)
        answers.append(r.verified_draft or "I would rather discuss this on a call.")
        before += r.unsupported_before; after += r.unsupported_after
        n_before += r.n_claims_before; n_after += r.n_claims_after
        claims += [c.to_dict() if hasattr(c, "to_dict") else c for c in r.claims]
    draft.cover_letter, draft.screening_answers = rep.verified_draft, answers
    stats = {"claims_before": n_before, "unsupported_before": before, "claims_after": n_after,
             "unsupported_after": after, "rate_before": before / n_before if n_before else 0.0,
             "rate_after": after / n_after if n_after else 0.0}
    return {"proposal_draft": draft.model_dump(), "claim_verification": claims, "verification_stats": stats}
