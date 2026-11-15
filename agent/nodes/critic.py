"""Critic: personalization, claim grounding, length, banned phrases. Sends back at most twice."""
import re

from agent.deps import Deps
from agent.handoffs import Critique, FitReport, JobSpec, ProposalDraft
from agent.llm import ask_json

MAX_REVISIONS = 2
INSTRUCTIONS = (
    "Review the draft cover letter for this job. Report only real problems: claims not backed by 'evidence', "
    "generic wording that ignores the client's project, or missing answers. "
    'Reply with JSON only: {"issues": ["short problem description", ...]} (empty list if the draft is good).'
)
BANNED = ["passionate", "i am writing to apply", "dear sir", "rockstar", "ninja", "perfect fit"]


def critic_node(state, deps: Deps):
    draft, spec, fit = ProposalDraft(**state["proposal_draft"]), JobSpec(**state["job_spec"]), FitReport(**state["fit_report"])
    text = draft.cover_letter
    low = text.lower()
    issues = []
    title_words = [w for w in re.findall(r"[a-z]{4,}", spec.title.lower())]
    if not any(w in low for w in title_words) and not any(s in low for s in fit.matched_skills):
        issues.append("not personalized: mention the client's project or a matched skill")
    if state.get("verification_stats", {}).get("unsupported_after", 0) > 0:
        issues.append("draft still contains unsupported claims")
    if not draft.evidence_ids:
        issues.append("no evidence ids cited")
    n = len(text.split())
    if n > 250:
        issues.append(f"too long ({n} words, max 250)")
    if n < 20:
        issues.append(f"too short ({n} words)")
    banned = BANNED + [r["value"] for r in deps.memory.style_rules(deps.user_id) if r["type"] == "avoid_word"]
    for b in banned:
        if re.search(rf"\b{re.escape(b)}\b", low):
            issues.append(f"banned phrase: {b}")
    review = ask_json(deps, "critic", {"instructions": INSTRUCTIONS, "draft": text, "job_spec": spec.model_dump(),
                                       "evidence": state.get("evidence", [])})
    if isinstance(review, dict) and isinstance(review.get("issues"), list):      # model review, at most 3 points
        issues += [f"reviewer: {str(i)[:160]}" for i in review["issues"][:3] if str(i).strip()]
    done = state.get("revisions", 0)
    ok = not issues
    send_back = (not ok) and done < MAX_REVISIONS
    c = Critique(approved=ok, score=max(0.0, round(1 - 0.25 * len(issues), 2)), issues=issues, send_back=send_back, revision=done)
    out = {"critique": c.model_dump()}
    if send_back:
        out["revisions"] = done + 1
    return out
